import sqlite3

from ramon.utc_history_audit import inspect, install


def test_detects_broker_three_hour_offset_and_preserves_raw(tmp_path):
    db = tmp_path / "history.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("""CREATE TABLE history_bars
            (symbol TEXT,timeframe TEXT,time INTEGER,open REAL,high REAL,
             low REAL,close REAL,spread_points INTEGER)""")
        con.execute("""CREATE TABLE mtf_utc_bars
            (symbol TEXT,timeframe TEXT,time INTEGER,broker_time INTEGER,
             offset_seconds INTEGER,open REAL,high REAL,low REAL,
             close REAL,spread_points INTEGER)""")
        for i in range(4):
            utc = 1791780000 + i * 60
            vals = ("XAUUSD_l", "M1", 4200.0+i, 4201.0+i, 4199.0+i, 4200.5+i, 42)
            con.execute("INSERT INTO history_bars VALUES (?,?,?,?,?,?,?,?)",
                        (*vals[:2], utc+10800, *vals[2:]))
            con.execute("INSERT INTO mtf_utc_bars VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (*vals[:2], utc, utc+10800, 10800, *vals[2:]))
        con.commit()
    with sqlite3.connect(db) as con:
        result = inspect(con)
        assert result["M1"][0]["offset_seconds"] == 10800
        assert result["M1"][0]["matching_ohlc"] == 4
    install(db, True)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM canonical_utc_bars").fetchone()[0] == 4
        assert con.execute("SELECT min(time) FROM canonical_utc_bars").fetchone()[0] == 1791780000
        assert con.execute("SELECT min(time) FROM history_bars").fetchone()[0] == 1791790800
        assert con.execute("SELECT distinct time_quality FROM canonical_utc_bars").fetchone()[0] == "VERIFIED_UTC"
