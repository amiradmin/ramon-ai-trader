from __future__ import annotations

import csv
import sqlite3

import pytest

from ramon.eliot_ticks import Tick, check_alignment, import_ticks, tick_outcome


def test_tick_sequence_uses_bid_ask_and_first_touch():
    ticks = [Tick(1000, 100, 100.42), Tick(2000, 94.4, 94.82),
             Tick(3000, 106, 106.42)]
    assert tick_outcome(ticks, entry_msc=1000, end_msc=4000,
                        side=1, units_per_price=1) == ("SL", -6)
    assert tick_outcome([ticks[0], ticks[2]], entry_msc=1000, end_msc=4000,
                        side=1, units_per_price=1) == ("TP", 5)
    assert tick_outcome([Tick(1_000_000, 100, 100.42)], entry_msc=1000,
                        end_msc=2_000_000, side=1, units_per_price=1) is None


def test_import_is_idempotent_and_price_alignment_must_match(tmp_path):
    tick_db = tmp_path / "eliot.sqlite3"
    bar_db = tmp_path / "bars.sqlite3"
    csv_path = tmp_path / "ticks.csv"
    base = 1_790_000_000
    with csv_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("time_msc", "bid", "ask", "flags"))
        for i in range(26):
            writer.writerow(((base + 300 * i) * 1000, 100, 100.42, 6))
    with sqlite3.connect(bar_db) as con:
        con.execute("CREATE TABLE history_bars(symbol TEXT,timeframe TEXT,time INTEGER,open REAL)")
        con.executemany("INSERT INTO history_bars VALUES ('XAUUSD_l','M5',?,100)",
                        [(base + 300 * i,) for i in range(25)])
    assert import_ticks(csv_path, tick_db, symbol="XAUUSD_l")["stored"] == 26
    assert import_ticks(csv_path, tick_db, symbol="XAUUSD_l")["stored"] == 26
    assert check_alignment(bar_db, tick_db, symbol="XAUUSD_l")["matched_bar_opens"] == 25
    with sqlite3.connect(bar_db) as con:
        con.execute("UPDATE history_bars SET open=105")
    with pytest.raises(ValueError, match="opening prices disagree"):
        check_alignment(bar_db, tick_db, symbol="XAUUSD_l")
