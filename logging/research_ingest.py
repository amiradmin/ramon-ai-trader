"""Best-effort, read-only-to-trading, idempotent research and SQLite mirror."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from datetime import datetime, timezone
import psycopg
from psycopg.types.json import Jsonb

DB_URL = os.environ["RAMON_LOG_DATABASE_URL"]
ROOT = Path(os.getenv("RAMON_RESEARCH_ROOT", "/mt5-users"))
SQLITE = Path(os.getenv("RAMON_HISTORY_DB", "/data/ramon_history.sqlite3"))
POLL = max(2, int(os.getenv("RAMON_LOG_POLL_SECONDS", "10")))

def iso_epoch(value):
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None

def parsed(value):
    if value is None:
        return {}
    try:
        x = json.loads(value) if isinstance(value, str) else value
        return x if isinstance(x, dict) else {}
    except (ValueError, TypeError):
        return {}

def ingest_research(con, path):
    path_id = str(path)
    st = path.stat()
    ident = f"{st.st_dev}:{st.st_ino}"
    with con.cursor() as cur:
        cur.execute("SELECT source_identity,next_byte_offset FROM ingest_cursors WHERE source_name=%s", (path_id,))
        row = cur.fetchone()
    offset = row[1] if row and row[0] == ident and st.st_size >= row[1] else 0
    with path.open("rb") as stream:
        stream.seek(offset)
        for _ in range(250):
            start = stream.tell()
            line = stream.readline()
            if not line or not line.endswith(b"\n"):
                break  # never advance past incomplete append
            end = stream.tell()
            try:
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    raise ValueError("non-object JSON")
                kind = str(obj.get("kind") or "UNKNOWN")
                data = obj.get("data") if isinstance(obj.get("data"), dict) else {}
                uid = obj.get("event_id") or hashlib.sha256(line.rstrip(b"\r\n")).hexdigest()
                event_time = iso_epoch(obj.get("observed_utc"))
                with con.transaction():
                    with con.cursor() as cur:
                        cur.execute("""
                            INSERT INTO raw_research_events
                            (event_uid,source_file,source_offset,kind,account_key,sample_key,
                             position_id,observed_at,broker_time_msc,event_data)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT(event_uid) DO NOTHING RETURNING id
                        """, (str(uid),path_id,start,kind,obj.get("account_key"),obj.get("sample_key"),
                              str(obj.get("position_id") or ""),event_time,obj.get("broker_time_msc"),Jsonb(obj)))
                        inserted = cur.fetchone()
                        if inserted and kind == "position_quote":
                            cur.execute("""
                                INSERT INTO position_quotes
                                (raw_event_id,account_key,sample_key,position_id,observed_at,
                                 bid,ask,floating_profit_units,volume,sl,tp,tp_stage,event_data)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                ON CONFLICT DO NOTHING
                            """, (inserted[0],obj.get("account_key"),obj.get("sample_key"),
                                  str(obj.get("position_id") or ""),event_time,data.get("bid"),data.get("ask"),
                                  data.get("profit_units"),data.get("volume"),data.get("sl"),
                                  data.get("tp"),data.get("tp_stage"),Jsonb(data)))
                        if inserted and kind in ("position_change","protection_change","protection_baseline",
                                                 "order_intent","order_result","close_intent","close_result",
                                                 "deal","deal_detail"):
                            cur.execute("""
                                INSERT INTO position_actions
                                (raw_event_id,account_key,sample_key,position_id,observed_at,
                                 action_kind,actor_class,reason,event_data)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                ON CONFLICT DO NOTHING
                            """, (inserted[0],obj.get("account_key"),obj.get("sample_key"),
                                  str(obj.get("position_id") or ""),event_time,kind,
                                  str(data.get("actor") or "UNKNOWN"),str(data.get("detail") or data.get("source") or ""),
                                  Jsonb(data)))
                        cur.execute("""
                            INSERT INTO ingest_cursors(source_name,source_identity,next_byte_offset)
                            VALUES (%s,%s,%s)
                            ON CONFLICT(source_name) DO UPDATE SET
                            source_identity=excluded.source_identity,next_byte_offset=excluded.next_byte_offset,
                            last_success_at=now()
                        """, (path_id,ident,end))
            except (ValueError, UnicodeDecodeError) as exc:
                with con.transaction():
                    con.execute("""
                        INSERT INTO ingest_errors(source_name,source_offset,error_code)
                        VALUES (%s,%s,%s) ON CONFLICT DO NOTHING
                    """, (path_id,start,type(exc).__name__))
                    con.execute("""
                        INSERT INTO ingest_cursors(source_name,source_identity,next_byte_offset)
                        VALUES (%s,%s,%s)
                        ON CONFLICT(source_name) DO UPDATE SET
                        source_identity=excluded.source_identity,next_byte_offset=excluded.next_byte_offset,
                        last_success_at=now()
                    """, (path_id,ident,end))

def mirror_sqlite(con):
    if not SQLITE.is_file():
        return
    with sqlite3.connect(f"file:{SQLITE}?mode=ro", uri=True, timeout=3) as source:
        source.row_factory = sqlite3.Row
        # Decisions are append-only and have a monotonic id; mirror incrementally.
        with con.cursor() as cur:
            cur.execute("SELECT coalesce(max(source_decision_id),0) FROM decision_features")
            last = cur.fetchone()[0]
        decisions = source.execute("""
            SELECT * FROM decision_samples WHERE id>? ORDER BY id LIMIT 300
        """, (last,)).fetchall()
        for r in decisions:
            d = dict(r)
            with con.transaction():
                con.execute("""
                    INSERT INTO decision_features
                    (sample_key,source_decision_id,symbol,quote_at,captured_at,signal_bar_at,
                     direction,base_decision,final_decision,mid,spread,atr,model_bundle,
                     regime_features,entry_features,news_features,decision_metadata,target_structure)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(sample_key) DO NOTHING
                """, (d["sample_key"],d["id"],d["symbol"],iso_epoch(d.get("quote_time")),
                      iso_epoch(d.get("captured")),iso_epoch(d.get("signal_bar_time")),
                      d.get("direction"),d.get("base_decision"),d.get("final_decision"),
                      d.get("mid"),d.get("spread"),d.get("atr"),d.get("bundle_id"),
                      Jsonb(parsed(d.get("regime_features"))),Jsonb(parsed(d.get("entry_features"))),
                      Jsonb(parsed(d.get("news_features"))),Jsonb(parsed(d.get("model_metadata"))),
                      Jsonb(parsed(d.get("target_structure")))))
        # Only ~hundreds of closed trades; upsert to catch late edits and training-status changes.
        trades = source.execute("SELECT * FROM trade_outcomes").fetchall()
        for r in trades:
            d = dict(r)
            with con.transaction():
                con.execute("""
                    INSERT INTO trade_labels
                    (trade_key,sample_key,symbol,side,opened_at,closed_at,net_units,
                     initial_risk_units,net_r,profit_units,commission_units,swap_units,fee_units,
                     exit_reason,exit_detail,entry_source,trade_role,entry_magic,
                     manual_intervention,actual_fill_price,training_status,entry_ea_version,original)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(trade_key) DO UPDATE SET
                       closed_at=excluded.closed_at,net_units=excluded.net_units,
                       net_r=excluded.net_r,exit_reason=excluded.exit_reason,
                       exit_detail=excluded.exit_detail,manual_intervention=excluded.manual_intervention,
                       training_status=excluded.training_status,original=excluded.original,synced_at=now()
                """, (d["trade_key"],d.get("sample_key"),d["symbol"],d["direction"],
                      iso_epoch(d.get("opened")),iso_epoch(d.get("closed")),
                      d.get("net_units"),d.get("initial_risk_units"),d.get("net_r"),
                      d.get("profit_units"),d.get("commission_units"),d.get("swap_units"),
                      d.get("fee_units"),d.get("exit_reason"),d.get("exit_detail"),
                      d.get("entry_source"),d.get("trade_role"),d.get("entry_magic"),
                      str(d["manual_intervention"]) if d.get("manual_intervention") is not None else None,
                      d.get("actual_fill_price"),d.get("training_status"),d.get("entry_ea_version"),
                      Jsonb(d)))

def main():
    print("research-log-ingest started: read-only MT5 and SQLite sources", flush=True)
    while True:
        try:
            with psycopg.connect(DB_URL, autocommit=True, connect_timeout=5) as con:
                with open("/app/schema.sql", encoding="utf-8") as f:
                    con.execute(f.read())
                for path in sorted(ROOT.glob("*/AppData/Roaming/MetaQuotes/Terminal/Common/Files/RamonResearch_*.jsonl")):
                    ingest_research(con,path)
                mirror_sqlite(con)
        except Exception as exc:
            print(f"ingest failure: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(POLL)

if __name__ == "__main__":
    main()
