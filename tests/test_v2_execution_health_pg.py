from unittest.mock import patch

from ramon.v2_mt5_execution_health import analyze


def test_pg_execution_health_does_not_touch_sqlite():
    row = {
        "trade_key": "t1", "symbol": "XAUUSD_l", "closed": 100,
        "net_units": 1.0, "net_r": .5, "exit_reason": "DEAL_REASON_TP",
        "direction": "BUY", "commission_units": None,
        "swap_units": None, "fee_units": None, "training_status": "LEARNABLE",
        "exit_detail": None, "actual_fill_price": None, "entry_source": None
    }
    with patch.dict("os.environ", {"RAMON_STORAGE_BACKEND": "postgres", "RAMON_PG_DSN": "unused"}):
        with patch("ramon.v2_mt5_execution_health._pg_rows", return_value=[row]) as read:
            with patch("ramon.v2_mt5_execution_health.sqlite3.connect",
                       side_effect=AssertionError("SQLite used")):
                result = analyze("/nonexistent/old.db", current_time=101)
    read.assert_called_once_with("XAUUSD_l", 101, 500)
    assert result["ready"] is True
    assert result["trade_count"] == 1


def test_pg_failure_fails_closed():
    with patch.dict("os.environ", {"RAMON_STORAGE_BACKEND": "postgres", "RAMON_PG_DSN": "unused"}):
        with patch("ramon.v2_mt5_execution_health._pg_rows", side_effect=RuntimeError("offline")):
            result = analyze("/unused")
    assert result["ready"] is False
    assert result["reason"] == "postgres_read_failed"
