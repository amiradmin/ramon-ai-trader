from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
from typing import Iterator, TextIO
import zipfile


DATE_FORMAT = "%Y.%m.%d %H:%M"
EXPECTED_COLUMNS = ("Date", "Open", "High", "Low", "Close", "Volume")
TIMEFRAME_MINUTES = {"M15": 15}
DEFAULT_MEMBER = {"M15": "XAU_15m_data.csv"}


@dataclass(frozen=True)
class AuditReport:
    source: str
    timeframe: str
    rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_timestamps: int
    non_monotonic_rows: int
    misaligned_timestamps: int
    zero_volume_rows: int
    first_time: str | None
    last_time: str | None
    expected_interval_seconds: int
    regular_intervals: int
    gap_intervals: int
    largest_gap_seconds: int

    @property
    def clean(self) -> bool:
        return (
            self.rows > 0
            and self.invalid_rows == 0
            and self.duplicate_timestamps == 0
            and self.non_monotonic_rows == 0
            and self.misaligned_timestamps == 0
        )


@dataclass(frozen=True)
class ParsedBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def _open_source(
    source: str | Path, *, timeframe: str, member: str | None = None
) -> tuple[TextIO, str, object]:
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(
            f"unsupported timeframe: {timeframe}; "
            "Ramon historical lab currently accepts M15 only"
        )

    if path.suffix.lower() == ".zip":
        archive = zipfile.ZipFile(path)
        selected = member or DEFAULT_MEMBER[timeframe]
        if selected not in archive.namelist():
            archive.close()
            raise ValueError(f"archive does not contain {selected}")
        raw = archive.open(selected, "r")
        text_handle = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        return text_handle, f"{path}!{selected}", archive

    handle = path.open("r", encoding="utf-8-sig", newline="")
    return handle, str(path), handle


def _parse_row(raw: dict[str, str]) -> ParsedBar:
    dt = datetime.strptime(raw["Date"].strip(), DATE_FORMAT).replace(tzinfo=timezone.utc)
    values = [float(raw[name]) for name in ("Open", "High", "Low", "Close", "Volume")]
    open_, high, low, close, volume = values
    if min(open_, high, low, close) <= 0:
        raise ValueError("non-positive OHLC")
    if volume < 0:
        raise ValueError("negative volume")
    if low > min(open_, close) or high < max(open_, close) or low > high:
        raise ValueError("invalid OHLC envelope")
    return ParsedBar(int(dt.timestamp()), open_, high, low, close, volume)


def _iter_rows(handle: TextIO) -> Iterator[tuple[ParsedBar | None, str | None]]:
    reader = csv.DictReader(handle, delimiter=";")
    if reader.fieldnames is None:
        raise ValueError("CSV has no header")
    fields = tuple(name.strip() for name in reader.fieldnames)
    missing = [name for name in EXPECTED_COLUMNS if name not in fields]
    if missing:
        raise ValueError(f"CSV missing columns: {','.join(missing)}")

    for raw in reader:
        normalized = {
            (key or "").strip(): (value or "").strip() for key, value in raw.items()
        }
        try:
            yield _parse_row(normalized), None
        except (KeyError, TypeError, ValueError) as exc:
            yield None, str(exc)


def audit_source(
    source: str | Path, *, timeframe: str = "M15", member: str | None = None
) -> AuditReport:
    interval = TIMEFRAME_MINUTES.get(timeframe)
    if interval is None:
        raise ValueError(
            f"unsupported timeframe: {timeframe}; "
            "Ramon historical lab currently accepts M15 only"
        )
    interval_seconds = interval * 60

    handle, label, owner = _open_source(source, timeframe=timeframe, member=member)
    rows = valid = invalid = duplicates = non_monotonic = misaligned = zero_volume = 0
    regular_intervals = gap_intervals = largest_gap = 0
    first_ts: int | None = None
    last_ts: int | None = None
    previous_ts: int | None = None
    seen: set[int] = set()

    try:
        for bar, _error in _iter_rows(handle):
            rows += 1
            if bar is None:
                invalid += 1
                continue
            valid += 1
            if bar.timestamp in seen:
                duplicates += 1
            seen.add(bar.timestamp)
            if bar.timestamp % interval_seconds != 0:
                misaligned += 1
            if bar.volume == 0:
                zero_volume += 1
            if first_ts is None:
                first_ts = bar.timestamp
            if previous_ts is not None:
                delta = bar.timestamp - previous_ts
                if delta <= 0:
                    non_monotonic += 1
                elif delta == interval_seconds:
                    regular_intervals += 1
                elif delta > interval_seconds:
                    gap_intervals += 1
                    largest_gap = max(largest_gap, delta)
            previous_ts = bar.timestamp
            last_ts = bar.timestamp
    finally:
        handle.close()
        if owner is not handle:
            owner.close()

    def iso(ts: int | None) -> str | None:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")

    return AuditReport(
        source=label,
        timeframe=timeframe,
        rows=rows,
        valid_rows=valid,
        invalid_rows=invalid,
        duplicate_timestamps=duplicates,
        non_monotonic_rows=non_monotonic,
        misaligned_timestamps=misaligned,
        zero_volume_rows=zero_volume,
        first_time=iso(first_ts),
        last_time=iso(last_ts),
        expected_interval_seconds=interval_seconds,
        regular_intervals=regular_intervals,
        gap_intervals=gap_intervals,
        largest_gap_seconds=largest_gap,
    )


def _ensure_history_schema(db: str | Path) -> Path:
    path = Path(db).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS history_bars (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                time INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                spread_points INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (symbol, timeframe, time)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_symbol_tf_time "
            "ON history_bars(symbol,timeframe,time)"
        )
    return path


def import_source(
    db: str | Path,
    source: str | Path,
    *,
    symbol: str = "XAUUSD_KAGGLE",
    timeframe: str = "M15",
    member: str | None = None,
    require_clean: bool = True,
    batch_size: int = 10_000,
) -> dict[str, object]:
    if not symbol.upper().startswith("XAUUSD"):
        raise ValueError("only XAUUSD variants are supported")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    audit = audit_source(source, timeframe=timeframe, member=member)
    if require_clean and not audit.clean:
        raise ValueError(
            f"dataset audit failed: {json.dumps(asdict(audit), sort_keys=True)}"
        )

    path = _ensure_history_schema(db)
    handle, _label, owner = _open_source(source, timeframe=timeframe, member=member)
    changed = 0
    imported = 0
    batch: list[tuple[object, ...]] = []

    def flush(conn: sqlite3.Connection) -> None:
        nonlocal changed
        if not batch:
            return
        before = conn.total_changes
        conn.executemany(
            """
            INSERT INTO history_bars(
                symbol,timeframe,time,open,high,low,close,spread_points
            )
            VALUES (?,?,?,?,?,?,?,0)
            ON CONFLICT(symbol,timeframe,time) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close
            """,
            batch,
        )
        changed += conn.total_changes - before
        batch.clear()

    try:
        with sqlite3.connect(path, timeout=30) as conn:
            for bar, _error in _iter_rows(handle):
                if bar is None:
                    if require_clean:
                        raise AssertionError("audit/import mismatch")
                    continue
                batch.append(
                    (
                        symbol,
                        timeframe,
                        bar.timestamp,
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                    )
                )
                imported += 1
                if len(batch) >= batch_size:
                    flush(conn)
            flush(conn)
            total = int(
                conn.execute(
                    "SELECT COUNT(*) FROM history_bars "
                    "WHERE symbol=? AND timeframe=?",
                    (symbol, timeframe),
                ).fetchone()[0]
            )
    finally:
        handle.close()
        if owner is not handle:
            owner.close()

    return {
        "audit": asdict(audit),
        "imported": imported,
        "changed": changed,
        "total": total,
        "symbol": symbol,
        "timeframe": timeframe,
        "spread_points_policy": "zero_unknown_external_dataset",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Audit/import the Kaggle XAUUSD M15 dataset into an isolated "
            "Ramon historical-lab symbol"
        )
    )
    parser.add_argument("source", help="archive.zip or XAU_15m_data.csv")
    parser.add_argument(
        "--timeframe", default="M15", choices=sorted(TIMEFRAME_MINUTES)
    )
    parser.add_argument("--member", help="CSV member name when source is a zip archive")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_KAGGLE")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="skip invalid rows instead of refusing import",
    )
    args = parser.parse_args()

    if args.audit_only:
        result: object = asdict(
            audit_source(args.source, timeframe=args.timeframe, member=args.member)
        )
    else:
        result = import_source(
            args.db,
            args.source,
            symbol=args.symbol,
            timeframe=args.timeframe,
            member=args.member,
            require_clean=not args.allow_dirty,
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
