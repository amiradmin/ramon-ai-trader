"""Historical candles must not masquerade as live model inputs or UTC."""
import sqlite3

from ramon.monitor import analysis_bundle, recent_market_context


def history(tmp_path, *, m1_time):
    db = tmp_path / "history.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE history_bars(symbol TEXT,timeframe TEXT,time INT,open REAL,high REAL,low REAL,close REAL,spread_points INT)")
        con.execute("INSERT INTO history_bars VALUES('XAUUSD_l','M15',100000,100,102,99,101,42)")
        if m1_time is not None:
            con.execute("INSERT INTO history_bars VALUES('XAUUSD_l','M1',?,100,102,99,101,42)", (m1_time,))
    return db


def test_stale_m1_is_flagged_and_broker_clock_is_not_utc(tmp_path):
    market = recent_market_context(history(tmp_path, m1_time=1000), signal_bar_time=100000, quote_time=100920)
    assert any("قدیمی" in warning for warning in market["warnings"])
    bundle = analysis_bundle({"recent_market": market, "warnings": market["warnings"]})
    candle_lines = [line for line in bundle.splitlines() if " | O " in line]
    assert candle_lines
    assert all("broker time; UTC offset unknown" in line and "+00:00" not in line for line in candle_lines)
    assert "not exact decision input" in bundle


def test_recent_m1_is_not_flagged_stale(tmp_path):
    market = recent_market_context(history(tmp_path, m1_time=100900), signal_bar_time=100000, quote_time=100920)
    assert not any("قدیمی" in warning for warning in market["warnings"])
    assert len(market["warnings"]) == 1  # Offset and input attribution remain unknown.


def test_missing_m1_and_mismatched_m15_are_visible(tmp_path):
    market = recent_market_context(history(tmp_path, m1_time=None), signal_bar_time=99900)
    assert any("M1" in warning and "موجود نیست" in warning for warning in market["warnings"])
    assert any("M15" in warning and "یکسان نیست" in warning for warning in market["warnings"])


def test_later_m1_is_not_attributed_to_earlier_decision(tmp_path):
    market = recent_market_context(history(tmp_path, m1_time=101000), signal_bar_time=100000, quote_time=100920)
    assert any("بعد از زمان تصمیم" in warning for warning in market["warnings"])
