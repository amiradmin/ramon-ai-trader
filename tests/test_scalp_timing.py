import sqlite3

from ramon.scalp_timing import scalp_timing


def make_bars(m5_up=True, gap=False):
    con = sqlite3.connect(":memory:")
    con.execute("""CREATE TABLE mtf_utc_bars (
        symbol TEXT, timeframe TEXT, time INTEGER, open REAL,
        high REAL, low REAL, close REAL)""")
    now = 1_800_000_000
    for tf, period, rising in (("M1", 60, True), ("M5", 300, m5_up)):
        end = (now // period) * period - period
        for idx in range(4):
            ts = end - (3 - idx) * period
            if gap and tf == "M1" and idx == 1:
                ts -= period
            close = 4200 + (idx if rising else -idx)
            con.execute("INSERT INTO mtf_utc_bars VALUES(?,?,?,?,?,?,?)",
                        ("XAUUSD_l", tf, ts, close, close + 1, close - 1, close))
    return con, now


def test_live_agreement():
    con, now = make_bars()
    outcome = scalp_timing(con, "XAUUSD_l", now=now)
    assert outcome["status"] == "AGREE"
    assert outcome["direction"] == "BUY"


def test_conflicting_timeframes():
    con, now = make_bars(m5_up=False)
    outcome = scalp_timing(con, "XAUUSD_l", now=now)
    assert outcome["status"] == "CONFLICT"
    assert outcome["direction"] == "WAIT"


def test_missing_bar_gap():
    con, now = make_bars(gap=True)
    outcome = scalp_timing(con, "XAUUSD_l", now=now)
    assert outcome["status"] == "INSUFFICIENT_DATA"
    assert "gap" in outcome["reason"]
