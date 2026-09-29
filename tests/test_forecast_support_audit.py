from __future__ import annotations

import json
import sqlite3

from ramon.forecast_support_audit import audit


def test_read_only_audit_separates_kept_and_removed_trade_results(tmp_path):
    db = tmp_path / "history.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE decision_samples (
                sample_key TEXT, quote_time INTEGER, mid REAL, spread REAL,
                model_metadata TEXT
            );
            CREATE TABLE trade_outcomes (
                sample_key TEXT, training_status TEXT, direction TEXT, net_r REAL
            );
        """)
        for key, low, median, high, pnl in (
            ("strong", 99.0, 103.0, 105.0, 1.0),
            ("weak", 98.8, 101.2, 101.3, -1.0),
        ):
            metadata = {"decision_audit": {"base": {
                "forecast_low": low, "forecast_median": median,
                "forecast_high": high,
                "signal_bid": 100.0, "signal_ask": 100.4,
            }}}
            conn.execute("INSERT INTO decision_samples VALUES (?,?,?,?,?)",
                         (key, 1000, 100.2, 0.4, json.dumps(metadata)))
            conn.execute("INSERT INTO trade_outcomes VALUES (?,?,?,?)",
                         (key, "LEARNABLE", "BUY", pnl))

    result = audit(db)

    assert result["scored_rows"] == 2
    assert result["kept"]["net_r"] == 1.0
    assert result["removed"]["net_r"] == -1.0
    assert result["kept"]["trades"] == result["removed"]["trades"] == 1
