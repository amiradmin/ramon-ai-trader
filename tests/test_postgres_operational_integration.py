"""Real isolated-PostgreSQL coverage for execution-adjacent repositories.

This test is deliberately skipped outside ``scripts/run_postgres_integration.sh``.
It must never discover or connect to a production database.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

def test_manual_and_mtf_repository_round_trip_on_isolated_postgres() -> None:
    dsn = os.environ.get("RAMON_PG_TEST_DSN", "")
    if not dsn:
        pytest.skip("RAMON_PG_TEST_DSN is required for isolated PostgreSQL integration")
    from ramon.storage import PostgresOperationalRepository

    repo = PostgresOperationalRepository(dsn)
    marker = uuid4().hex
    repo.persist_human_opinion({
        "created_utc": 1_800_000_000, "reviewer": "test", "symbol": "XAUUSD_l",
        "sample_key": marker[:16], "signal_bar_time": 1_800_000_000,
        "opinion": "BUY", "confidence": 5, "note": marker, "snapshot": "{}",
    })
    repo.persist_human_opinion({
        "created_utc": 1_800_000_000, "reviewer": "test", "symbol": "XAUUSD_l",
        "sample_key": marker[16:], "signal_bar_time": 1_800_000_900,
        "opinion": "SELL", "confidence": 4, "note": marker + "-new", "snapshot": "{}",
    })
    opinions = repo.recent_human_opinions("XAUUSD_l", 50)
    marked = [row for row in opinions if str(row["note"]).startswith(marker)]
    assert [row["opinion"] for row in marked[:2]] == ["SELL", "BUY"]

    repo.persist_manual_override({
        "created_utc": 1_800_000_000, "signal_bar_time": 1_800_000_000,
        "sample_key": marker[:16], "stage": "edge", "action": "FORCE_PASS",
        "payload": '{"operator":"integration"}',
    })
    rows = [("XAUUSD_l", "M1", 1_800_000_000, 1_800_010_800, 10800,
             4200.0, 4201.0, 4199.0, 4200.5, 12)]
    assert repo.persist_mtf_snapshot(rows) == 1

    import psycopg
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ramon.manual_gate_overrides WHERE sample_key=%s", (marker[:16],))
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT close FROM ramon.mtf_utc_bars WHERE symbol=%s AND timeframe='M1' AND time=%s", ("XAUUSD_l", 1_800_000_000))
            assert cur.fetchone()[0] == 4200.5


def test_target_learning_backfill_on_isolated_postgres(monkeypatch) -> None:
    dsn = os.environ.get("RAMON_PG_TEST_DSN", "")
    if not dsn:
        pytest.skip("RAMON_PG_TEST_DSN is required for isolated PostgreSQL integration")
    from ramon.postgres_operational import DDL
    from ramon.target_outcomes import backfill_target_outcomes
    import psycopg

    sample_key = uuid4().hex[:16]
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
            cur.execute("""INSERT INTO ramon.decision_samples
              (sample_key,captured,symbol,signal_bar_time,quote_time,chronos_model,schema_version,direction,base_decision,final_decision,mid,spread,atr,regime_features,entry_features,meta_base_features,news_features,metadata)
              VALUES (%s,100,'XAUUSD_l',100,100,'test',4,'BUY','BUY','BUY',100,1,2,'{}','{}','{}','{}',%s::jsonb)""",
              (sample_key, '{"target_structure":{"tp1":101,"tp2":102,"tp3":103,"impulse_end":100}}'))
            cur.execute("""INSERT INTO ramon.trade_outcomes
              (trade_key,sample_key,symbol,direction,opened,closed,net_units,initial_risk_units,net_r,exit_reason,received,telemetry)
              VALUES (%s,%s,'XAUUSD_l','BUY',100,1000,1,1,1,'DEAL_REASON_TP',1000,'{"training_status":"LEARNABLE"}')""",
              ("trade-" + sample_key, sample_key))
            cur.executemany("""INSERT INTO ramon.history_bars
              (symbol,timeframe,time,open,high,low,close,spread_points)
              VALUES ('XAUUSD_l','M15',%s,100,%s,%s,100,1)""", [(0, 100, 99), (900, 104, 99)])
    monkeypatch.setenv("RAMON_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("RAMON_PG_DSN", dsn)
    result = backfill_target_outcomes("unused", symbol="XAUUSD_l")
    assert result["inserted"] >= 1
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT tp1_hit,tp2_hit,tp3_hit FROM ramon.target_outcomes WHERE sample_key=%s", (sample_key,))
            assert cur.fetchone() == (1, 1, 1)


def test_monitor_history_reader_on_isolated_postgres(monkeypatch) -> None:
    dsn = os.environ.get("RAMON_PG_TEST_DSN", "")
    if not dsn:
        pytest.skip("RAMON_PG_TEST_DSN is required for isolated PostgreSQL integration")
    from ramon.postgres_operational import DDL
    from ramon.monitor import read_history
    import psycopg

    sample_key = uuid4().hex[:16]
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
            cur.execute("""INSERT INTO ramon.decision_samples
              (sample_key,captured,symbol,signal_bar_time,quote_time,chronos_model,schema_version,direction,base_decision,final_decision,mid,spread,atr,regime_features,entry_features,meta_base_features,news_features,metadata)
              VALUES (%s,200,'XAUUSD_l',200,200,'test',3,'BUY','BUY','BUY',100,1,2,'{}','{}','{}','{}','{}')""", (sample_key,))
            cur.execute("""INSERT INTO ramon.trade_outcomes
              (trade_key,sample_key,symbol,direction,opened,closed,net_units,initial_risk_units,net_r,exit_reason,received,telemetry)
              VALUES (%s,%s,'XAUUSD_l','BUY',200,300,1,1,1,'DEAL_REASON_TP',300,'{"trade_role":"MAIN"}')""",
              ("monitor-" + sample_key, sample_key))
    monkeypatch.setenv("RAMON_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("RAMON_PG_DSN", dsn)
    sample, rows, trades, error = read_history("unused", "XAUUSD_l", sample_key)
    assert error is None
    assert sample["sample_key"] == sample_key
    assert any(row["sample_key"] == sample_key for row in rows)
    assert any(row["sample_key"] == sample_key for row in trades)
