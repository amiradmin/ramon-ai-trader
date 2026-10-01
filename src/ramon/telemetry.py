"""Immutable observational telemetry. Never used by entry/exit policy.

Broker timestamps are kept raw; UTC observations are a separate clock.
Missing telemetry is reported, never substituted with fabricated zeroes.
"""
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from math import isfinite
from pathlib import Path
import sqlite3
import sys
import time
import zlib

from .history import ensure_history_db


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False)


def ensure_telemetry_db(db: str | Path) -> Path:
    path = ensure_history_db(db)
    with sqlite3.connect(path, timeout=10) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS input_blobs (
            sha256 TEXT PRIMARY KEY, codec TEXT NOT NULL, body BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS inference_audit (
            sample_key TEXT PRIMARY KEY, input_sha256 TEXT NOT NULL,
            recorded_utc REAL NOT NULL, response_json TEXT NOT NULL,
            provenance_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS research_events (
            event_id TEXT PRIMARY KEY, account_key TEXT NOT NULL,
            role TEXT NOT NULL, symbol TEXT NOT NULL, kind TEXT NOT NULL,
            sample_key TEXT, position_id TEXT, broker_time_msc INTEGER NOT NULL,
            received_utc REAL NOT NULL, body_json TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_events_position ON research_events(account_key,position_id,broker_time_msc);
        CREATE INDEX IF NOT EXISTS idx_events_sample ON research_events(sample_key,kind);
        CREATE TABLE IF NOT EXISTS opportunity_labels (
            sample_key TEXT NOT NULL, horizon INTEGER NOT NULL,
            endpoint_time INTEGER NOT NULL, endpoint_close REAL NOT NULL,
            mid_change REAL NOT NULL, spread_at_decision REAL NOT NULL,
            source TEXT NOT NULL, computed_utc REAL NOT NULL,
            PRIMARY KEY(sample_key,horizon));
        """)
    return path


def implementation_digest() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def runtime_provenance() -> dict:
    packages = {}
    for name in ("chronos-forecasting", "torch", "numpy", "transformers", "scikit-learn"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {"python": sys.version, "packages": packages}


def persist_inference(db: str | Path, *, sample_key: str, request: dict,
                      response: dict, provenance: dict, recorded_utc: float) -> None:
    """Compress and deduplicate context; freeze quote/context/response atomically.

    Reusing an identity with different content is an error, not an overwrite.
    """
    import re
    if not re.fullmatch(r"[a-f0-9]{16}", sample_key):
        raise ValueError("invalid sample key")
    # Separate common bars from per-request quote/account data to avoid storing
    # 256 identical bars on every 30-second decision and on both charts.
    context = {key: request.get(key) for key in ("symbol", "timeframe", "bars")}
    context_bytes = canonical(context).encode()
    context_hash = hashlib.sha256(context_bytes).hexdigest()
    envelope = {key: value for key, value in request.items() if key != "bars"}
    envelope["bars_sha256"] = context_hash
    frozen_provenance = {**provenance, "request": envelope, "schema_version": 1}
    news_bytes = canonical(frozen_provenance.pop("news_events_observed", [])).encode()
    news_hash = hashlib.sha256(news_bytes).hexdigest()
    frozen_provenance["news_events_sha256"] = news_hash
    row = (context_hash, canonical(response), canonical(frozen_provenance))
    path = ensure_telemetry_db(db)
    with sqlite3.connect(path, timeout=10) as conn:
        old = conn.execute("SELECT input_sha256,response_json,provenance_json FROM inference_audit WHERE sample_key=?", (sample_key,)).fetchone()
        if old and old != row:
            raise ValueError("conflicting inference identity")
        conn.execute("INSERT OR IGNORE INTO input_blobs VALUES (?,?,?)",
                     (context_hash, "zlib-json-v1", zlib.compress(context_bytes)))
        conn.execute("INSERT OR IGNORE INTO input_blobs VALUES (?,?,?)",
                     (news_hash, "zlib-json-v1", zlib.compress(news_bytes)))
        conn.execute("INSERT OR IGNORE INTO inference_audit VALUES (?,?,?,?,?)",
                     (sample_key, context_hash, recorded_utc, row[1], row[2]))


def load_blob(conn: sqlite3.Connection, sha256: str) -> object:
    blob = conn.execute("SELECT codec,body FROM input_blobs WHERE sha256=?", (sha256,)).fetchone()
    if not blob or blob[0] != "zlib-json-v1":
        raise ValueError("missing or unsupported input blob")
    raw = zlib.decompress(blob[1])
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError("input hash mismatch")
    return json.loads(raw)


def replay_input(conn: sqlite3.Connection, sample_key: str) -> dict:
    row = conn.execute("SELECT input_sha256,provenance_json FROM inference_audit WHERE sample_key=?", (sample_key,)).fetchone()
    if not row:
        raise ValueError("missing inference audit")
    request = json.loads(row[1])["request"]
    request.pop("bars_sha256")
    request["bars"] = load_blob(conn, row[0])["bars"]
    return request


def observed_path_labels(conn: sqlite3.Connection, sample_key: str) -> dict:
    """Executable-side sampled excursions, kept separate from OHLC estimates."""
    quotes = conn.execute("SELECT broker_time_msc,body_json FROM research_events WHERE sample_key=? AND kind='position_quote' ORDER BY broker_time_msc,event_id", (sample_key,)).fetchall()
    audit = conn.execute("SELECT response_json FROM inference_audit WHERE sample_key=?", (sample_key,)).fetchone()
    targets = json.loads(audit[0]) if audit else {}
    favorable, adverse, hits = [], [], {}
    intervals = []
    for at, body in quotes:
        data = json.loads(body)["data"]
        side = int(data["position_type"])
        price = float(data["bid"] if side == 0 else data["ask"])
        move = (price - float(data["entry_price"])) * (1 if side == 0 else -1)
        favorable.append(max(0, move)); adverse.append(max(0, -move))
        intervals.append(data.get("sampling_interval_ms"))
        for level in (1, 2, 3):
            target = targets.get(f"target_tp{level}")
            if (target and targets.get("target_direction") == ("BUY" if side == 0 else "SELL")
                    and (price >= target if side == 0 else price <= target)):
                hits.setdefault(f"tp{level}_first_observed_msc", at)
    return {"source": "sampled_bid_ask_not_complete_ticks", "samples": len(quotes),
            "first_observed_msc": quotes[0][0] if quotes else None,
            "last_observed_msc": quotes[-1][0] if quotes else None,
            "mfe_price_observed": max(favorable) if favorable else None,
            "mae_price_observed": max(adverse) if adverse else None,
            "max_between_observations_ms": max((b[0]-a[0] for a,b in zip(quotes,quotes[1:])), default=None),
            "sampling_intervals_ms": sorted({i for i in intervals if i is not None}),
            "target_hits": hits, "exact_first_touch_order_available": False}


EVENT_KINDS = {"session_start", "session_end", "decision_gate", "order_intent", "order_result",
               "deal", "deal_detail", "position_quote", "position_change", "close_intent", "close_result", "telemetry_gap", "decision_error", "protection_change", "protection_baseline"}


def persist_events(db: str | Path, payload: dict, received_utc: float) -> list[str]:
    """Transactional batch acknowledgement; retries are safe, conflicts rejected."""
    events = payload.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= 100:
        raise ValueError("need 1..100 events")
    rows = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("invalid event")
        body = canonical(event)
        if len(body.encode()) > 30_000:
            raise ValueError("event too large")
        identity = str(event.get("event_id", ""))
        account = str(event.get("account_key", ""))
        role, kind = event.get("role"), event.get("kind")
        at = event.get("broker_time_msc")
        if (not identity or len(identity) > 256 or not account or len(account) > 256
                or role not in {"MAIN", "SMALL"} or kind not in EVENT_KINDS
                or isinstance(at, bool) or not isinstance(at, int) or at <= 0):
            raise ValueError("invalid event identity/clock/kind")
        sample = event.get("sample_key") or None
        if sample is not None:
            import re
            if not isinstance(sample, str) or not re.fullmatch(r"[a-f0-9]{16}", sample):
                raise ValueError("invalid event sample key")
        symbol = event.get("symbol")
        if not isinstance(symbol, str) or not symbol.startswith("XAUUSD"):
            raise ValueError("invalid event symbol")
        data = event.get("data")
        if not isinstance(data, dict):
            raise ValueError("event data must be an object")
        if kind == "protection_change":
            try:
                values = [float(data[name]) for name in ("sl_before", "tp_before", "sl_after", "tp_after")]
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid protection change") from exc
            if (not all(isfinite(v) and v >= 0 for v in values)
                    or values[:2] == values[2:] or data.get("previous_known") is not True
                    or data.get("actor") != "external_unattributed"):
                raise ValueError("invalid protection change")
        if kind == "position_quote":
            try:
                bid, ask, entry = (float(data[name]) for name in ("bid", "ask", "entry_price"))
                side = data["position_type"]
                interval = data["sampling_interval_ms"]
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid position quote") from exc
            if (not all(isfinite(value) and value > 0 for value in (bid, ask, entry))
                    or ask < bid or side not in {0, 1} or not isinstance(interval, int) or interval < 1000):
                raise ValueError("invalid position quote")
        rows.append((identity, account, role, symbol, kind, sample,
                     str(event["position_id"]) if event.get("position_id") else None,
                     at, received_utc, body))
    path = ensure_telemetry_db(db)
    with sqlite3.connect(path, timeout=10) as conn:
        for row in rows:
            old = conn.execute("SELECT body_json FROM research_events WHERE event_id=?", (row[0],)).fetchone()
            if old and old[0] != row[-1]:
                raise ValueError("conflicting event identity")
            conn.execute("INSERT OR IGNORE INTO research_events VALUES (?,?,?,?,?,?,?,?,?,?)", row)
        conn.execute("""UPDATE trade_outcomes SET training_status='CENSORED_EXTERNAL_SL_TP'
            WHERE training_status!='CENSORED_MANUAL' AND sample_key IN
            (SELECT sample_key FROM research_events WHERE kind='protection_change'
             AND json_extract(body_json,'$.data.actor')='external_unattributed')""")
    return [row[0] for row in rows]


def backfill_opportunities(db: str | Path, horizon: int = 4) -> int:
    """Descriptive fixed-horizon price labels, never hypothetical realized P&L.

    Begin with the first complete bar after the quote, not an already partially
    observed current bar. Require contiguous history and a recorded input clock.
    """
    if not 1 <= horizon <= 96:
        raise ValueError("invalid horizon")
    path = ensure_telemetry_db(db)
    changed = 0
    with sqlite3.connect(path, timeout=10) as conn:
        samples = conn.execute("""SELECT s.sample_key,s.symbol,s.quote_time,s.mid,s.spread FROM decision_samples s
            JOIN inference_audit a USING(sample_key)
            LEFT JOIN opportunity_labels l ON l.sample_key=s.sample_key AND l.horizon=?
            WHERE l.sample_key IS NULL AND s.quote_time IS NOT NULL""", (horizon,)).fetchall()
        for key, symbol, quote, mid, spread in samples:
            first = (quote // 900 + 1) * 900
            bars = conn.execute("SELECT time,close FROM history_bars WHERE symbol=? AND timeframe='M15' AND time>=? ORDER BY time LIMIT ?", (symbol, first, horizon)).fetchall()
            if len(bars) != horizon or any(t != first + i * 900 for i, (t, _) in enumerate(bars)):
                continue
            # Only completed history is stored by the live collector.
            conn.execute("INSERT OR IGNORE INTO opportunity_labels VALUES (?,?,?,?,?,?,?,?)",
                         (key, horizon, bars[-1][0] + 900, bars[-1][1], bars[-1][1] - mid, spread,
                          "completed_m15_price_only_not_trade_pnl", time.time()))
            changed += 1
    return changed


def quality_report(db: str | Path) -> dict:
    path = ensure_telemetry_db(db)
    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN")
        scalar = lambda sql: conn.execute(sql).fetchone()[0]
        closed = scalar("SELECT COUNT(*) FROM trade_outcomes")
        missing_input = scalar("SELECT COUNT(*) FROM trade_outcomes t LEFT JOIN inference_audit a USING(sample_key) WHERE a.sample_key IS NULL")
        manual = scalar("SELECT COUNT(*) FROM trade_outcomes WHERE training_status='CENSORED_MANUAL'")
        groups = [dict(role=r or "UNKNOWN", ea_version=v or "UNKNOWN", training_status=s, trades=n,
                       winners=w, losses=l, net_units=net) for r,v,s,n,w,l,net in conn.execute("""
            SELECT trade_role,entry_ea_version,training_status,COUNT(*),SUM(net_units>0),SUM(net_units<0),SUM(net_units)
            FROM trade_outcomes GROUP BY trade_role,entry_ea_version,training_status""")]
        kinds = dict(conn.execute("SELECT kind,COUNT(*) FROM research_events GROUP BY kind"))
        missing_gate = scalar("""SELECT COUNT(*) FROM inference_audit a WHERE NOT EXISTS
            (SELECT 1 FROM research_events e WHERE e.sample_key=a.sample_key AND e.kind='decision_gate')""")
        return {"schema_version": 1, "live_execution_effect": "NONE", "promotion_allowed": False,
                "closed_trades": closed, "manual_closed_trades": manual,
                "closed_with_external_sl_tp": scalar("SELECT COUNT(DISTINCT t.trade_key) FROM trade_outcomes t JOIN research_events e USING(sample_key) WHERE e.kind='protection_change' AND json_extract(e.body_json,'$.data.actor')='external_unattributed'"),
                "closed_missing_immutable_input": missing_input,
                "closed_missing_decision_sample": scalar("SELECT COUNT(*) FROM trade_outcomes t LEFT JOIN decision_samples s USING(sample_key) WHERE s.sample_key IS NULL"),
                "closed_missing_cost_breakdown": scalar("SELECT COUNT(*) FROM trade_outcomes WHERE profit_units IS NULL OR commission_units IS NULL OR swap_units IS NULL OR fee_units IS NULL"),
                "closed_missing_fill_events": scalar("SELECT COUNT(*) FROM trade_outcomes t WHERE NOT EXISTS (SELECT 1 FROM research_events e WHERE e.sample_key=t.sample_key AND e.kind='deal')"),
                "closed_missing_position_quotes": scalar("SELECT COUNT(*) FROM trade_outcomes t WHERE NOT EXISTS (SELECT 1 FROM research_events e WHERE e.sample_key=t.sample_key AND e.kind='position_quote')"),
                "inferences_missing_ea_gate": missing_gate,
                "immutable_inputs": scalar("SELECT COUNT(*) FROM inference_audit"),
                "inferences_missing_model_revision": scalar("SELECT COUNT(*) FROM inference_audit WHERE json_extract(provenance_json,'$.checkpoint_revision') IS NULL"),
                "inferences_missing_ea_role": scalar("SELECT COUNT(*) FROM inference_audit WHERE json_extract(provenance_json,'$.request.ea_context.role') IS NULL"),
                "event_counts": kinds, "groups": groups,
                "opportunity_labels": scalar("SELECT COUNT(*) FROM opportunity_labels"),
                "limitations": ["Legacy inputs and missing events cannot be recreated exactly.",
                    "Position quotes are sampled observations; extrema and first-touch order may be missed.",
                    "500 total trades are not 500 independent clean samples per role/policy.",
                    "Manual exits stay in account performance but are censored from autonomous-exit training.",
                    "External SL/TP changes have an unattributed actor; observed changes censor autonomous-exit labels. Changes during downtime may be missed.",
                    "Price-only opportunity labels are not replacement trades or realized profitability."]}


def export_dataset(db: str | Path, out: str | Path) -> dict:
    """Consistent SQLite read snapshot, exact inputs, all manual/automatic labels."""
    path = ensure_telemetry_db(db)
    digest = hashlib.sha256()
    count = 0
    output = Path(out)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with sqlite3.connect(path) as conn, temporary.open("w", encoding="utf-8") as stream:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        for row in conn.execute("SELECT * FROM decision_samples ORDER BY quote_time,id"):
            sample = dict(row)
            key = sample["sample_key"]
            audit = conn.execute("SELECT * FROM inference_audit WHERE sample_key=?", (key,)).fetchone()
            record = {"record_type": "decision", "sample": sample, "input": replay_input(conn, key) if audit else None,
                      "audit": dict(audit) if audit else None,
                      "news_events_observed": load_blob(conn, json.loads(audit["provenance_json"])["news_events_sha256"]) if audit else None,
                      "observed_path": observed_path_labels(conn, key),
                      "trade": [dict(r) for r in conn.execute("SELECT * FROM trade_outcomes WHERE sample_key=?", (key,))],
                      "events": [json.loads(r[0]) for r in conn.execute("SELECT body_json FROM research_events WHERE sample_key=? ORDER BY broker_time_msc,event_id", (key,))],
                      "opportunity_labels": [dict(r) for r in conn.execute("SELECT * FROM opportunity_labels WHERE sample_key=?", (key,))]}
            line = canonical(record) + "\n"
            stream.write(line)
            digest.update(line.encode())
            count += 1
        # Never omit manual/performance outcomes merely because the decision
        # sample was lost by an old writer or arrived out of order.
        for row in conn.execute("SELECT t.* FROM trade_outcomes t LEFT JOIN decision_samples s USING(sample_key) WHERE s.sample_key IS NULL ORDER BY opened,trade_key"):
            line = canonical({"record_type": "orphan_trade", "trade": dict(row), "input": None}) + "\n"
            stream.write(line)
            digest.update(line.encode())
        # All raw events remain available, including sessions and broker history
        # without a valid learning sample identity.
        for row in conn.execute("SELECT body_json FROM research_events WHERE sample_key IS NULL OR sample_key NOT IN (SELECT sample_key FROM decision_samples WHERE sample_key IS NOT NULL) ORDER BY broker_time_msc,event_id"):
            line = canonical({"record_type": "unlinked_event", "event": json.loads(row[0])}) + "\n"
            stream.write(line)
            digest.update(line.encode())
    temporary.replace(output)
    manifest = {"schema_version": 1, "samples": count, "sha256": digest.hexdigest(),
                "created_utc": time.time(), "split_policy": "chronological; purge overlapping outcome intervals; fit preprocessing on train only",
                "auto_promote": False}
    output.with_suffix(output.suffix + ".manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def backup_database(db: str | Path, out: str | Path) -> dict:
    """SQLite backup API includes committed WAL data; no migration beforehand."""
    source, target = Path(db).resolve(), Path(out).resolve()
    if source == target or target.exists():
        raise ValueError("backup destination must be a new file")
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.as_uri()+"?mode=ro", uri=True) as original:
        with sqlite3.connect(target) as copy:
            original.backup(copy)
    return {"backup": str(target)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("report", "label-opportunities", "export", "backup"))
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--out", default="/data/research/training-audit.jsonl")
    args = parser.parse_args()
    result = (backup_database(args.db, args.out) if args.command == "backup" else
              quality_report(args.db) if args.command == "report" else
              {"labeled": backfill_opportunities(args.db)} if args.command == "label-opportunities" else
              export_dataset(args.db, args.out))
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
