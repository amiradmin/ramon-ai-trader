from __future__ import annotations

import sqlite3
from pathlib import Path
import zipfile

import pytest

from ramon.kaggle_history import audit_source, import_source


def _write_zip(path: Path, rows: list[str]) -> Path:
    content = "Date;Open;High;Low;Close;Volume\n" + "\n".join(rows) + "\n"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("XAU_15m_data.csv", content)
    return path


def test_audit_and_import_clean_m15_archive(tmp_path: Path) -> None:
    source = _write_zip(
        tmp_path / "archive.zip",
        [
            "2026.01.02 10:00;2600;2602;2599;2601;100",
            "2026.01.02 10:15;2601;2603;2600;2602;110",
            "2026.01.02 10:45;2602;2604;2601;2603;120",
        ],
    )
    audit = audit_source(source)
    assert audit.clean is True
    assert audit.valid_rows == 3
    assert audit.regular_intervals == 1
    assert audit.gap_intervals == 1
    assert audit.largest_gap_seconds == 1800

    db = tmp_path / "history.sqlite3"
    result = import_source(db, source)
    assert result["imported"] == 3
    assert result["total"] == 3
    assert result["spread_points_policy"] == "zero_unknown_external_dataset"

    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT symbol,timeframe,close,spread_points "
            "FROM history_bars ORDER BY time"
        ).fetchall()
    assert rows == [
        ("XAUUSD_KAGGLE", "M15", 2601.0, 0),
        ("XAUUSD_KAGGLE", "M15", 2602.0, 0),
        ("XAUUSD_KAGGLE", "M15", 2603.0, 0),
    ]


def test_dirty_dataset_is_rejected_by_default(tmp_path: Path) -> None:
    source = _write_zip(
        tmp_path / "archive.zip",
        [
            "2026.01.02 10:00;2600;2602;2599;2601;100",
            "2026.01.02 10:10;2601;2603;2600;2602;110",
        ],
    )
    audit = audit_source(source)
    assert audit.misaligned_timestamps == 1
    assert audit.clean is False
    with pytest.raises(ValueError, match="dataset audit failed"):
        import_source(tmp_path / "history.sqlite3", source)


def test_rejects_non_xau_symbol_and_non_m15(tmp_path: Path) -> None:
    source = _write_zip(
        tmp_path / "archive.zip",
        ["2026.01.02 10:00;2600;2602;2599;2601;100"],
    )
    with pytest.raises(ValueError, match="XAUUSD"):
        import_source(tmp_path / "history.sqlite3", source, symbol="EURUSD")
    with pytest.raises(ValueError, match="M15 only"):
        audit_source(source, timeframe="M5")
