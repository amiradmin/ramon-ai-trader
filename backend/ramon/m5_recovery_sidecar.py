"""Validate an MT5 M5 export and select only audited missing candles.

This is an offline research intake.  It never opens or writes Ramon's primary
SQLite database, never fills gaps, and never substitutes a neighbouring bar.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


FIELDS = ("time", "open", "high", "low", "close", "spread_points")


def _read_missing(path: Path) -> set[int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        rows = data["missing_candle_open_timestamps"]["M5"]
        return {int(row["open_utc"]) for row in rows}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("report has no valid M5 missing timestamp list") from exc


def _parse_row(row: dict[str, str]) -> tuple[int, float, float, float, float, int]:
    try:
        stamp = int(float(row["time"]))
        open_, high, low, close = (float(row[name]) for name in ("open", "high", "low", "close"))
        spread = int(float(row.get("spread_points") or 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("malformed CSV candle") from exc
    if stamp <= 0 or stamp % 300 or min(open_, high, low, close) <= 0:
        raise ValueError("invalid M5 timestamp or nonpositive OHLC")
    if low > min(open_, close) or high < max(open_, close) or spread < 0:
        raise ValueError("invalid M5 OHLC or spread")
    return stamp, open_, high, low, close, spread


def select_recovery_candles(missing: set[int], rows: list[dict[str, str]]):
    """Return validated rows only when exactly one candidate matches a missing M5 bar."""
    candidates: dict[int, list[tuple[int, float, float, float, float, int]]] = {}
    malformed = 0
    for row in rows:
        try:
            parsed = _parse_row(row)
        except ValueError:
            malformed += 1
            continue
        if parsed[0] in missing:
            candidates.setdefault(parsed[0], []).append(parsed)

    selected = []
    conflicts = []
    for stamp in sorted(missing):
        values = candidates.get(stamp, [])
        unique = set(values)
        if len(unique) == 1:
            selected.append(next(iter(unique)))
        elif len(unique) > 1:
            conflicts.append(stamp)
    return selected, {
        "missing_timestamps": len(missing),
        "candidate_matches": sum(len(v) for v in candidates.values()),
        "selected_real_candles": len(selected),
        "unresolved_timestamps": len(missing) - len(selected) - len(conflicts),
        "conflicting_timestamps": conflicts,
        "malformed_candidate_rows": malformed,
    }


def export_sidecar(missing_report: Path, candidate_csv: Path, output: Path, report: Path):
    if output.resolve() == report.resolve():
        raise ValueError("output and report must differ")
    missing = _read_missing(missing_report)
    raw = candidate_csv.read_bytes()
    with candidate_csv.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not set(FIELDS[:-1]).issubset(
                {name.strip().lower() for name in reader.fieldnames}):
            raise ValueError("candidate CSV needs time,open,high,low,close columns")
        normalized = {name.strip().lower(): name for name in reader.fieldnames}
        rows = [{key: value.get(normalized[key], "") for key in normalized} for value in reader]
    selected, summary = select_recovery_candles(missing, rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(FIELDS)
        writer.writerows(selected)
    summary.update({
        "mode": "OFFLINE_M5_RECOVERY_SIDECAR_ONLY",
        "candidate_csv_sha256": hashlib.sha256(raw).hexdigest(),
        "limitations": [
            "Selection is not proof that the candidate export is broker-authentic.",
            "No primary SQLite database, trade, decision, or live setting is modified.",
            "Missing or conflicting timestamps remain unresolved; no candle is synthesized.",
        ],
    })
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--missing-report", required=True, type=Path)
    parser.add_argument("--candidate-csv", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(export_sidecar(**vars(args)), ensure_ascii=False))


if __name__ == "__main__":
    main()
