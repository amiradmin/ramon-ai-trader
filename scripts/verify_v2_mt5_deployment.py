#!/usr/bin/env python3
"""Read-only EA deployment verification, does not execute MetaTrader orders.

Compares repository MQL5 source with the installed terminal source, confirms a
recent EX5 and prints a report. It cannot prove binary/source equivalence or
substitute for Strategy Tester.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect(repo: Path, terminal: Path) -> dict:
    source = repo / "mt5" / "Ramon.mq5"
    installed = terminal / "Ramon.mq5"
    binary = terminal / "Ramon.ex5"
    errors = []
    for path in (source, installed, binary):
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"missing_or_empty:{path.name}")
    if errors:
        return {"ready_for_tester_review": False, "errors": errors}
    source_sha, installed_sha, binary_sha = sha(source), sha(installed), sha(binary)
    if source_sha != installed_sha:
        errors.append("installed_source_differs_from_git")
    if binary.stat().st_mtime_ns < installed.stat().st_mtime_ns:
        errors.append("compiled_binary_older_than_source")
    if not all(term in installed.read_text(encoding="utf-8-sig") for term in (
            "EnableV2AccountRiskGuard", "V2Preflight(", "V2StopoutKey()")):
        errors.append("risk_guard_not_detected")
    return {
        "ready_for_tester_review": not errors,
        "live_trading_authorized": False,
        "errors": errors,
        "git_source_sha256": source_sha,
        "installed_source_sha256": installed_sha,
        "ex5_sha256": binary_sha,
        "ex5_size_bytes": binary.stat().st_size,
        "ex5_modified_utc": datetime.fromtimestamp(binary.stat().st_mtime, timezone.utc).isoformat(),
        "note": "Hashes and timestamps cannot prove EX5 behavior. Requires demo/MT5 Strategy Tester.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--terminal-experts", type=Path,
                        default=Path.home() / ".mt5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/Ramon")
    args = parser.parse_args()
    report = inspect(args.repo, args.terminal_experts)
    print(json.dumps(report, indent=2))
    return 0 if report["ready_for_tester_review"] else 1


if __name__ == "__main__":
    sys.exit(main())
