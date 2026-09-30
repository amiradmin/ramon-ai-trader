"""Prospective, paired forecasts in a separate ledger; never place orders."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from statistics import mean
import time

from .bundles import atomic_json
from .compare import MomentumBaseline
from .core import Bar, Market
from .covariates import CovariateForecaster
from .model import ChronosForecaster, model_name

MODELS = ("momentum_baseline", "chronos", "chronos_past_covariates")
SCHEMA = """
CREATE TABLE IF NOT EXISTS experiment (id INTEGER PRIMARY KEY CHECK(id=1), manifest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS predictions (
    asof INTEGER PRIMARY KEY, recorded_utc REAL NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outcomes (
    asof INTEGER PRIMARY KEY REFERENCES predictions(asof), evaluated_utc REAL NOT NULL,
    status TEXT NOT NULL, payload TEXT NOT NULL
);
"""


def encode(value):
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"))


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def implementation_digest():
    digest = hashlib.sha256()
    for name in ("forward_research.py", "model.py", "covariates.py", "compare.py", "core.py"):
        digest.update(name.encode() + Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


def open_ledger(path, source):
    path = Path(path).resolve()
    if path == Path(source).resolve() or (path.exists() and path.samefile(source)):
        raise ValueError("forward ledger must be separate from the live history database")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.executescript(SCHEMA)
    return conn


def begin_experiment(conn, model, symbol, *, context=256, horizon=4, max_age=120, now=None):
    if context < 16 or horizon < 1 or max_age < 1 or not getattr(model, "revision", None):
        raise ValueError("valid context/horizon/freshness and an identifiable checkpoint revision are required")
    config = {"schema_version": 1, "symbol": symbol, "timeframe": "M15", "context": context,
              "horizon": horizon, "max_quote_age_seconds": max_age, "model_id": model.model_id,
              "checkpoint_revision": model.revision, "implementation_sha256": implementation_digest(),
              "models": list(MODELS), "clock": "broker bar/quote timestamps; recorded UTC with observed deal offset"}
    row = conn.execute("SELECT manifest FROM experiment WHERE id=1").fetchone()
    if row:
        manifest = json.loads(row[0])
        if {k: manifest[k] for k in config} != config:
            raise ValueError("experiment settings, code or checkpoint changed; use a new ledger filename")
        return manifest
    manifest = dict(config, started_utc=time.time() if now is None else now)
    conn.execute("INSERT INTO experiment VALUES (1,?)", (encode(manifest),))
    conn.commit()
    return manifest


def snapshot(source, manifest, now):
    """Read one coherent live context; no historical backfill or assumed timezone."""
    symbol = manifest["symbol"]
    with readonly(source) as conn:
        conn.execute("BEGIN")
        row = conn.execute("""SELECT captured,quote_time,signal_bar_time,mid,spread,atr,sample_key
            FROM decision_samples WHERE symbol=? ORDER BY captured DESC,id DESC LIMIT 1""", (symbol,)).fetchone()
        if not row or row[1] is None:
            return None, "waiting_for_live_sample"
        captured, quote, asof, mid, spread, atr, key = row
        if not manifest["started_utc"] <= captured <= now or now - captured > manifest["max_quote_age_seconds"]:
            return None, "waiting_for_fresh_sample"
        # Offset evidence is historical telemetry, not quote_time minus the machine clock.
        offsets = [int(r[0]) for r in conn.execute("""
            SELECT opened_utc_offset_seconds FROM trade_outcomes WHERE symbol=? AND opened BETWEEN ? AND ?
                AND opened_utc_offset_seconds IS NOT NULL
            UNION ALL
            SELECT closed_utc_offset_seconds FROM trade_outcomes WHERE symbol=? AND closed BETWEEN ? AND ?
                AND closed_utc_offset_seconds IS NOT NULL
        """, (symbol, quote-86400, quote, symbol, quote-86400, quote))]
        if not offsets or len(set(offsets)) != 1 or abs(offsets[0]) > 14*3600:
            return None, "waiting_for_agreeing_recent_broker_offset"
        offset = offsets[0]
        quote_utc = quote-offset
        if not manifest["started_utc"] <= quote_utc <= captured <= now or now-quote_utc > manifest["max_quote_age_seconds"]:
            return None, "waiting_for_fresh_quote"
        if not asof+900 <= quote < asof+1800:
            return None, "quote_bar_mismatch"
        rows = conn.execute("""SELECT time,open,high,low,close FROM history_bars
            WHERE symbol=? AND timeframe='M15' ORDER BY time DESC LIMIT ?""",
            (symbol, manifest["context"])).fetchall()
    bars = tuple(Bar(int(t), float(o), float(h), float(l), float(c)) for t, o, h, l, c in reversed(rows))
    if len(bars) != manifest["context"] or bars[-1].time != asof:
        return None, "waiting_for_matching_completed_context"
    if not all(math.isfinite(v) and v > 0 for v in (mid, spread, atr)):
        return None, "invalid_quote_or_atr"
    Market._validate_bar_sequence(bars, label="forward context", completed=True)
    return {"bars": [asdict(b) for b in bars], "asof": asof, "sample_key": key,
            "live_sample_captured_utc": captured, "quote_time_mt5": quote, "quote_utc": quote_utc,
            "broker_utc_offset_seconds": offset, "offset_evidence_samples": len(offsets),
            "mid": mid, "spread": spread, "atr": atr,
            "first_target_close_utc": asof+1800-offset}, "ready"


def validated_forecast(forecast, horizon):
    values = (forecast.low, forecast.median, forecast.high, *forecast.median_path)
    if (len(forecast.median_path) != horizon or
            not all(math.isfinite(v) and v > 0 for v in values) or
            not forecast.low <= forecast.median <= forecast.high or
            forecast.median_path[-1] != forecast.median):
        raise ValueError("invalid paired forecast")
    return asdict(forecast)


def record_once(source, conn, manifest, model, *, clock=time.time):
    snap, status = snapshot(source, manifest, clock())
    if snap is None:
        return {"status": status}
    asof = snap["asof"]
    if conn.execute("SELECT 1 FROM predictions WHERE asof=?", (asof,)).fetchone():
        return {"status": "already_recorded", "asof_mt5": asof}
    bars = tuple(Bar(**b) for b in snap["bars"])
    closes = [b.close for b in bars]
    # Quote prices are provenance and never fed to any forecasting model.
    market = Market(manifest["symbol"], "M15", snap["mid"]-snap["spread"]/2,
                    snap["mid"]+snap["spread"]/2, .01, bars)
    models = (MomentumBaseline(), model, CovariateForecaster(model).for_market(market))
    forecasts = {name: validated_forecast(m.forecast(closes, manifest["horizon"]), manifest["horizon"])
                 for name, m in zip(MODELS, models)}
    finished = clock()
    # Every model must finish before the first future candle closes. No partial pair.
    if finished >= snap["first_target_close_utc"]-30 or finished < snap["quote_utc"]:
        return {"status": "excluded_late_forecast", "asof_mt5": asof}
    payload = {**snap, "forecasts": forecasts,
               "context_sha256": hashlib.sha256(encode(snap["bars"]).encode()).hexdigest()}
    cursor = conn.execute("INSERT OR IGNORE INTO predictions VALUES (?,?,?)", (asof, finished, encode(payload)))
    conn.commit()
    return {"status": "recorded" if cursor.rowcount else "already_recorded", "asof_mt5": asof,
            "recorded_utc": finished}


def evaluate_pending(source, conn, manifest, *, now=None):
    pending = conn.execute("""SELECT p.asof,p.payload FROM predictions p
        LEFT JOIN outcomes o ON p.asof=o.asof WHERE o.asof IS NULL ORDER BY p.asof""").fetchall()
    matured = excluded = 0
    with readonly(source) as history:
        history.execute("BEGIN")
        for asof, payload in pending:
            rows = history.execute("""SELECT time,open,high,low,close FROM history_bars
                WHERE symbol=? AND timeframe='M15' AND time>? ORDER BY time LIMIT ?""",
                (manifest["symbol"], asof, manifest["horizon"])).fetchall()
            if len(rows) < manifest["horizon"]:
                continue
            targets = [dict(zip(("time", "open", "high", "low", "close"), row)) for row in rows]
            snap = json.loads(payload)
            evaluated = time.time() if now is None else now
            # Extra guard even if imported history happens to contain future-dated bars.
            if evaluated < targets[-1]["time"]+900-snap["broker_utc_offset_seconds"]:
                continue
            status = "matured" if all(row[0] == asof+900*(i+1) for i, row in enumerate(rows)) else "excluded_gap"
            valid = all(all(math.isfinite(b[k]) and b[k] > 0 for k in ("open", "high", "low", "close"))
                        and b["low"] <= min(b["open"], b["close"]) <= max(b["open"], b["close"]) <= b["high"]
                        for b in targets)
            if not valid:
                status = "excluded_invalid_ohlc"
            conn.execute("INSERT OR IGNORE INTO outcomes VALUES (?,?,?,?)", (asof, evaluated, status, encode(targets)))
            matured += status == "matured"
            excluded += status != "matured"
    conn.commit()
    return {"new_matured": matured, "new_excluded": excluded}


def sign(value):
    return (value > 0) - (value < 0)


def summarize(conn):
    row = conn.execute("SELECT manifest FROM experiment WHERE id=1").fetchone()
    if not row:
        raise ValueError("ledger has no experiment")
    manifest = json.loads(row[0])
    rows = conn.execute("""SELECT p.payload,o.payload FROM predictions p JOIN outcomes o ON p.asof=o.asof
        WHERE o.status='matured' ORDER BY p.asof""").fetchall()
    pairs = [(json.loads(p), json.loads(o)) for p, o in rows]
    results = {}
    naive_errors = [mean(abs(b["close"]-p["bars"][-1]["close"]) for b in outcome) for p, outcome in pairs]
    for name in MODELS:
        errors, normalized, endpoint, pinballs, hits, widths, correct, screened = ([] for _ in range(8))
        active = nonflat = 0
        for p, targets in pairs:
            forecast, actual = p["forecasts"][name], targets[-1]["close"]
            mae = mean(abs(pred-b["close"]) for pred, b in zip(forecast["median_path"], targets))
            errors.append(mae)
            normalized.append(mae/p["atr"])
            endpoint.append(abs(forecast["median"]-actual))
            pinballs.append(mean(max(q*(actual-pred), (q-1)*(actual-pred))
                                 for q, pred in zip((.1, .5, .9), (forecast["low"], forecast["median"], forecast["high"]))))
            hits.append(forecast["low"] <= actual <= forecast["high"])
            widths.append(forecast["high"]-forecast["low"])
            origin = p["bars"][-1]["close"]
            predicted, realized = sign(forecast["median"]-origin), sign(actual-origin)
            if predicted:
                active += 1
                if realized:
                    nonflat += 1
                    correct.append(predicted == realized)
            if abs(forecast["median"]-origin) > p["spread"] and realized:
                screened.append(predicted == realized)
        avg = lambda values: mean(values) if values else None
        naive = avg(naive_errors)
        results[name] = {"paired_samples": len(pairs), "path_mae_price": avg(errors), "path_mae_atr": avg(normalized),
                         "endpoint_mae_price": avg(endpoint), "endpoint_pinball_price": avg(pinballs),
                         "endpoint_interval_80_coverage": avg(hits), "endpoint_interval_width_price": avg(widths),
                         "path_mae_relative_to_no_change": avg(errors)/naive if naive else None,
                         "direction_nonflat_predictions": active, "direction_scored_nonflat_targets": nonflat,
                         "direction_accuracy": avg(correct), "move_above_observed_spread_samples": len(screened),
                         "move_above_observed_spread_direction_accuracy": avg(screened)}
    total = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    statuses = dict(conn.execute("SELECT status,COUNT(*) FROM outcomes GROUP BY status"))
    return {"mode": "prospective_paired_forecast_research", "live_execution_effect": "NONE",
            "promotion_allowed": False, "manifest": manifest, "recorded_pairs": total,
            "pending_pairs": total-sum(statuses.values()), "outcome_status_counts": statuses,
            "paired_matured_samples": len(pairs), "no_change_path_mae_price": mean(naive_errors) if pairs else None,
            "results": results, "selected_model": None,
            "limitations": ["Forecast accuracy is not trading profitability; no entries, exits or EA risk rules are simulated.",
                            "Above-spread direction screening is descriptive; commissions and slippage are not modeled.",
                            "Overlapping four-bar horizons are dependent observations, not independent trades.",
                            "Only fresh successfully paired forecasts with contiguous target bars are scored; gaps and outages may bias the sample.",
                            "Broker offset evidence comes from agreeing deals in the prior 24 hours; clocks must be synchronized. Forecasts must finish at least 30 seconds before the first target close.",
                            "Quantile calibration applies only to the final horizon, not simultaneous path coverage."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("record", "watch", "evaluate"))
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--ledger", default="/data/research/forward-v1.sqlite3")
    parser.add_argument("--out", default="/data/research/forward-v1.json")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--model", default="autogluon/chronos-2-small")
    parser.add_argument("--active-model-file", default="/checkpoints/active_model.txt")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()
    if args.poll_seconds < 5:
        parser.error("poll-seconds must be >=5")
    if Path(args.out).resolve() in {Path(args.db).resolve(), Path(args.ledger).resolve()}:
        parser.error("report path must differ from both databases")
    try:
        with open_ledger(args.ledger, args.db) as conn:
            if args.action == "evaluate":
                row = conn.execute("SELECT manifest FROM experiment WHERE id=1").fetchone()
                if not row:
                    raise ValueError("record a prospective sample first")
                manifest = json.loads(row[0])
                evaluate_pending(args.db, conn, manifest)
                report = summarize(conn)
                atomic_json(Path(args.out), report)
                print(json.dumps(report, indent=2), flush=True)
                return
            pointer = Path(args.active_model_file)
            model_id = pointer.read_text().strip() if pointer.exists() else args.model
            model = ChronosForecaster(model_name(model_id), args.device)
            manifest = begin_experiment(conn, model, args.symbol)
            while True:
                try:
                    result = record_once(args.db, conn, manifest, model)
                    result.update(evaluate_pending(args.db, conn, manifest))
                    report = summarize(conn)
                    atomic_json(Path(args.out), report)
                    print(json.dumps(result), flush=True)
                except (ValueError, sqlite3.Error, RuntimeError) as exc:
                    print(json.dumps({"status": "not_recorded_error", "error": str(exc)}), flush=True)
                    if args.action == "record":
                        raise
                if args.action == "record":
                    return
                time.sleep(args.poll_seconds)
    except (ValueError, sqlite3.Error, FileNotFoundError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
