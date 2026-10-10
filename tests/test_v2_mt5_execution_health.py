import json
import sqlite3

from ramon.v2_mt5_execution_health import analyze, atomic_publish


def make_db(path):
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE trade_outcomes (
          trade_key TEXT PRIMARY KEY, symbol TEXT, closed INTEGER,
          net_units REAL, net_r REAL, exit_reason TEXT, commission_units REAL,
          swap_units REAL, fee_units REAL, training_status TEXT,
          actual_fill_price REAL)""")
        db.executemany("INSERT INTO trade_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
            ("a", "XAUUSD_l", 100, 2., .5, "DEAL_REASON_TP", -.2, 0., -.1, "LEARNABLE", 4000.),
            ("b", "XAUUSD_l", 200, -1., -.25, "DEAL_REASON_CLIENT", None, None, None, "CENSORED_MANUAL", None),
            ("future", "XAUUSD_l", 9999, 1000., 1000., "DEAL_REASON_TP", 0., 0., 0., "LEARNABLE", 4000.),
            ("other", "EURUSD", 150, 100., 1., "DEAL_REASON_TP", 0., 0., 0., "LEARNABLE", 1.),
        ])


def test_realized_metrics_are_symbol_and_time_scoped(tmp_path):
    db = tmp_path / "history.sqlite3"
    make_db(db)
    result = analyze(db, current_time=300)
    assert result["ready"] is True
    assert result["trade_count"] == 2
    assert result["net_units"] == 1
    assert result["profit_factor"] == 2
    assert result["win_rate"] == .5
    assert result["mean_r"] == .125
    assert result["live_order_access"] is False
    assert result["exit_reasons"]["DEAL_REASON_CLIENT"] == 1
    assert result["cost_coverage"]["commission_units"] == {"observed": 1, "total": 2}
    assert "partial_cost_telemetry" in result["quality_warnings"]


def test_missing_schema_and_no_database_fail_closed(tmp_path):
    assert analyze(tmp_path / "missing.db")["reason"] == "database_not_found"
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    assert analyze(path)["reason"] == "trade_outcomes_schema_missing"


def test_atomic_snapshot_can_be_reloaded(tmp_path):
    path = tmp_path / "snapshot.json"
    atomic_publish(path, {"ready": True, "live_order_access": False})
    assert json.loads(path.read_text())["live_order_access"] is False
    assert not list(tmp_path.glob("*.tmp"))
