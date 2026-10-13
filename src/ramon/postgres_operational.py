"""Native PostgreSQL persistence for Ramon operational market telemetry.

This is deliberately separate from SQLite compatibility code.  Callers select
it with ``RAMON_STORAGE_BACKEND=postgres`` and must supply ``RAMON_PG_DSN``.
"""
from __future__ import annotations

import json
from .core import Bar, Market

DDL = """
CREATE SCHEMA IF NOT EXISTS ramon;
CREATE TABLE IF NOT EXISTS ramon.history_bars (
 symbol text NOT NULL, timeframe text NOT NULL, time bigint NOT NULL,
 open double precision NOT NULL, high double precision NOT NULL,
 low double precision NOT NULL, close double precision NOT NULL,
 spread_points integer NOT NULL DEFAULT 0, PRIMARY KEY(symbol,timeframe,time));
CREATE TABLE IF NOT EXISTS ramon.decision_samples (
 sample_key text PRIMARY KEY, captured bigint NOT NULL, symbol text NOT NULL,
 signal_bar_time bigint NOT NULL, quote_time bigint, chronos_model text,
 schema_version integer NOT NULL, direction text NOT NULL, base_decision text NOT NULL,
 final_decision text, mid double precision NOT NULL, spread double precision NOT NULL,
 atr double precision NOT NULL, regime_features jsonb NOT NULL, entry_features jsonb NOT NULL,
 meta_base_features jsonb NOT NULL, news_features jsonb, metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
 UNIQUE(captured,symbol));
CREATE INDEX IF NOT EXISTS history_bars_symbol_tf_time ON ramon.history_bars(symbol,timeframe,time);
CREATE INDEX IF NOT EXISTS decision_samples_symbol_quote ON ramon.decision_samples(symbol,quote_time);
"""

def _connect(dsn: str):
    if not dsn: raise ValueError("RAMON_PG_DSN is required for postgres storage")
    import psycopg
    return psycopg.connect(dsn, connect_timeout=3, options="-c statement_timeout=5000")

def ensure(dsn: str) -> None:
    with _connect(dsn) as conn:
        with conn.cursor() as cur: cur.execute(DDL)

def persist_market(dsn: str, market: Market) -> int:
    rows=[]; newest=market.bars[-1].time; spread=round((market.ask-market.bid)/market.point)
    for b in market.bars: rows.append((market.symbol,market.timeframe,b.time,b.open,b.high,b.low,b.close,spread if b.time==newest else 0))
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
            cur.executemany("""INSERT INTO ramon.history_bars VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT(symbol,timeframe,time) DO UPDATE SET open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,close=EXCLUDED.close,
              spread_points=CASE WHEN EXCLUDED.spread_points>0 THEN EXCLUDED.spread_points ELSE ramon.history_bars.spread_points END""",rows)
    return len(rows)

def load_bars(dsn: str, symbol: str, timeframe: str="M15") -> tuple[tuple[Bar,...],tuple[int,...]]:
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT time,open,high,low,close,spread_points FROM ramon.history_bars WHERE symbol=%s AND timeframe=%s ORDER BY time",(symbol,timeframe)); rows=cur.fetchall()
    if not rows: raise ValueError(f"no {symbol}/{timeframe} history in PostgreSQL")
    return tuple(Bar(int(t),float(o),float(h),float(l),float(c)) for t,o,h,l,c,_ in rows),tuple(int(x) for *_,x in rows)

def persist_decision_sample(dsn: str, values: dict[str, object]) -> bool:
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
            cur.execute("""INSERT INTO ramon.decision_samples(sample_key,captured,symbol,signal_bar_time,quote_time,chronos_model,schema_version,direction,base_decision,final_decision,mid,spread,atr,regime_features,entry_features,meta_base_features,news_features,metadata)
            VALUES (%(sample_key)s,%(captured)s,%(symbol)s,%(signal_bar_time)s,%(quote_time)s,%(chronos_model)s,%(schema_version)s,%(direction)s,%(base_decision)s,%(final_decision)s,%(mid)s,%(spread)s,%(atr)s,%(regime_features)s::jsonb,%(entry_features)s::jsonb,%(meta_base_features)s::jsonb,%(news_features)s::jsonb,%(metadata)s::jsonb) ON CONFLICT DO NOTHING""",values)
            return cur.rowcount==1
