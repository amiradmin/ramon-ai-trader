"""Fail-closed, read-only preflight for manually arming Ramon at market open.

It never starts Docker or MT5, changes EA inputs, or submits trade requests.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from urllib.request import urlopen


def _line(text: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}:\s*(.*)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def evaluate_diagnostic(text: str) -> dict:
    checks = {
        "terminal_connected": "TerminalConnected: YES" in _line(text, "Market"),
        "trade_permissions": "terminal=YES ea=YES account=YES" in _line(text, "Trade permissions"),
        "account_lock": _line(text, "Live").startswith("ARMED") and "AccountLock: OK" in _line(text, "Live"),
        "money_units_confirmed": _line(text, "MoneyUnitsConfirmed") == "YES",
        "model_ready": "ModelReady: YES" in _line(text, "News"),
        "fresh_tick": _line(text, "Status") != "Stale tick",
        "risk_gate": _line(text, "RiskGate").startswith("PASS"),
    }
    return checks


def check_model(url: str) -> bool:
    try:
        with urlopen(url, timeout=5) as response:
            return bool(json.loads(response.read()).get("ready"))
    except Exception:
        return False


def run(diagnostic: Path, *, model_health_url: str, max_age_seconds: int) -> dict:
    if not diagnostic.is_file():
        return {"ready": False, "checks": {"diagnostic_exists": False}, "failed": ["diagnostic_exists"]}
    checks = evaluate_diagnostic(diagnostic.read_text(encoding="utf-8", errors="replace"))
    checks["diagnostic_exists"] = True
    checks["diagnostic_fresh"] = time.time() - diagnostic.stat().st_mtime <= max_age_seconds
    checks["model_health"] = check_model(model_health_url)
    failed = sorted(name for name, ok in checks.items() if not ok)
    return {"ready": not failed, "checks": checks, "failed": failed,
            "mode": "READ_ONLY_LIVE_START_PREFLIGHT"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostic", required=True, type=Path)
    parser.add_argument("--model-health-url", default="http://127.0.0.1:8012/health")
    parser.add_argument("--max-age-seconds", default=180, type=int)
    args = parser.parse_args()
    if args.max_age_seconds <= 0:
        parser.error("--max-age-seconds must be positive")
    result = run(args.diagnostic, model_health_url=args.model_health_url,
                 max_age_seconds=args.max_age_seconds)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["ready"] else 2)


if __name__ == "__main__":
    main()
