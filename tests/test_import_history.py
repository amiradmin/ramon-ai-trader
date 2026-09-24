from __future__ import annotations

import csv
from pathlib import Path

from ramon.history import history_count, load_bars
from ramon.import_history import import_csv


def test_import_mt5_epoch_history_is_idempotent(tmp_path: Path) -> None:
    csv_path = tmp_path / "history.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time", "open", "high", "low", "close", "spread_points"])
        writer.writerow([1_800_000_000, 100, 101, 99, 100.5, 42])
        writer.writerow([1_800_000_900, 100.5, 102, 100, 101.5, 43])

    db = tmp_path / "ramon.sqlite3"
    first = import_csv(db, csv_path, symbol="XAUUSD_l")
    second = import_csv(db, csv_path, symbol="XAUUSD_l")

    assert first["valid"] == 2
    assert second["valid"] == 2
    assert history_count(db) == 2
    bars, spreads = load_bars(db)
    assert bars[-1].time == 1_800_000_900
    assert spreads == (42, 43)


def test_import_skips_invalid_rows(tmp_path: Path) -> None:
    csv_path = tmp_path / "history.csv"
    csv_path.write_text(
        "time,open,high,low,close,spread_points\n"
        "1800000000,100,101,99,100.5,42\n"
        "bad,100,101,99,100,42\n",
        encoding="utf-8",
    )
    result = import_csv(tmp_path / "ramon.sqlite3", csv_path, symbol="XAUUSD_l")
    assert result["valid"] == 1
    assert result["skipped"] == 1
