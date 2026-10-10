"""Ramon V3 MT5 integration foundation: observation-only, no order APIs.

Adapters intentionally fail closed. Integration with a live MT5 terminal is a
separate deployment step; this module never calls order_send or changes risk.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import importlib.util
import itertools
import json
import math
from pathlib import Path
import sqlite3
from typing import Iterable, Mapping


FEATURES = (
    "mcp", "strategy_tester", "trade_transactions", "onnx",
    "economic_calendar", "python_integration", "optimization_agents", "openblas",
)


def capability_report(*, tester_report: str | None = None, onnx_path: str | None = None,
                      mcp_config: str | None = None) -> dict:
    """Describe installed *local* prerequisites, never assume terminal connectivity."""
    checks = {
        "mcp": bool(mcp_config and Path(mcp_config).is_file()),
        "strategy_tester": bool(tester_report and Path(tester_report).is_file()),
        "trade_transactions": True,  # durable receiver below; EA hook is separate
        "onnx": bool(onnx_path and Path(onnx_path).is_file()),
        "economic_calendar": True,  # normalization only; no live calendar transport
        "python_integration": importlib.util.find_spec("MetaTrader5") is not None,
        "optimization_agents": True,  # deterministic local parameter search only
        "openblas": importlib.util.find_spec("numpy") is not None,
    }
    return {
        "mode": "OBSERVE_ONLY",
        "live_order_access": False,
        "features": {key: {"prerequisite_detected": checks[key]} for key in FEATURES},
        "note": "Prerequisite detection is not proof of working MT5/MCP integration.",
    }


def safe_mcp_config(config: Mapping) -> bool:
    """Require all requested MCP tools to be read-only and local.

    The terminal's MCP permission mechanism must also enforce these restrictions.
    """
    tools = config.get("allowed_tools")
    endpoint = config.get("endpoint", "")
    return (isinstance(tools, list) and len(tools) > 0
            and all(isinstance(t, str) and t.startswith(("get_", "list_", "read_"))
                    for t in tools)
            and isinstance(endpoint, str)
            and endpoint.startswith(("http://127.0.0.1:", "http://localhost:")))


def create_event_store(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path))
    connection.execute("""CREATE TABLE IF NOT EXISTS mt5_trade_events (
        event_key TEXT PRIMARY KEY, timestamp_utc INTEGER NOT NULL,
        event_kind TEXT NOT NULL, position_id TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )""")
    connection.commit()
    return connection


def store_trade_transaction(db: sqlite3.Connection, event: Mapping) -> bool:
    """Deduplicate queued MT5 transactions; manual SL/TP changes can be recorded.

    event_key must originate from a stable EA-side identifier. Insert is idempotent.
    """
    key, kind, pos = (event.get(k) for k in ("event_key", "event_kind", "position_id"))
    ts = event.get("timestamp_utc")
    if not all(isinstance(s, str) and 0 < len(s) <= 256 for s in (key, kind, pos)):
        raise ValueError("Missing/invalid transaction identifiers")
    if isinstance(ts, bool) or not isinstance(ts, int) or ts < 0:
        raise ValueError("timestamp_utc must be Unix seconds")
    payload = json.dumps(dict(event), sort_keys=True, allow_nan=False)
    with db:
        cursor = db.execute(
            "INSERT OR IGNORE INTO mt5_trade_events VALUES (?,?,?,?,?)",
            (key, ts, kind, pos, payload))
    return cursor.rowcount == 1


def normalize_calendar_event(event: Mapping) -> dict:
    """Validate an MT5 economic-calendar event provided by a trusted bridge.

    Does not confuse server-local clock with UTC. Caller must supply UTC seconds.
    """
    timestamp = event.get("timestamp_utc")
    currency = event.get("currency")
    impact = event.get("impact")
    if (isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0
            or not isinstance(currency, str) or len(currency) != 3
            or impact not in ("LOW", "MEDIUM", "HIGH")):
        raise ValueError("Invalid event; provide explicit UTC Unix seconds")
    return {"timestamp_utc": timestamp, "currency": currency.upper(), "impact": impact,
            "title": str(event.get("title", ""))[:200]}


def replay_asof(decisions: Iterable[Mapping], *, at_utc: int, max_age_seconds: int = 60) -> dict | None:
    """Last published signal as of execution time; future signals never leak."""
    if max_age_seconds < 0:
        raise ValueError("max_age_seconds must be nonnegative")
    candidate = None
    for raw in decisions:
        published = raw.get("published_at_utc")
        if isinstance(published, bool) or not isinstance(published, int):
            continue
        if at_utc - max_age_seconds <= published <= at_utc:
            if candidate is None or published > candidate["published_at_utc"]:
                candidate = dict(raw)
    return candidate


def optimize_shadow(samples: Iterable[Mapping], grid: Mapping[str, Iterable], score_fn):
    """Exhaustive offline grid; no MT5 Strategy Tester or trading side effects."""
    parameters = sorted(grid)
    values = [tuple(grid[k]) for k in parameters]
    if not parameters or any(not v for v in values):
        raise ValueError("Specify nonempty parameter candidates")
    data = tuple(samples)
    best = None
    for combination in itertools.product(*values):
        params = dict(zip(parameters, combination))
        score = float(score_fn(data, params))
        if not math.isfinite(score):
            continue
        if best is None or score > best["score"]:
            best = {"params": params, "score": score}
    if best is None:
        raise ValueError("No finite scores")
    return best


def onnx_shadow_metadata(path: str | Path) -> dict:
    """Record a validated model artifact without importing it into live EA."""
    file = Path(path)
    if file.suffix.lower() != ".onnx" or not file.is_file() or file.stat().st_size == 0:
        raise ValueError("Expected nonempty .onnx file")
    return {"path": str(file), "bytes": file.stat().st_size, "live_enabled": False}


def openblas_info() -> dict:
    try:
        import numpy as np
        from io import StringIO
        import contextlib
        output = StringIO()
        with contextlib.redirect_stdout(output):
            np.show_config()
        details = output.getvalue()
        return {"numpy_version": np.__version__, "openblas_reported": "openblas" in details.lower()}
    except ImportError:
        return {"numpy_version": None, "openblas_reported": False}


def cli() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Ramon V3 read-only MT5 capability audit")
    parser.add_argument("--tester-report")
    parser.add_argument("--onnx-path")
    parser.add_argument("--mcp-config")
    args = parser.parse_args()
    print(json.dumps(capability_report(tester_report=args.tester_report,
                                       onnx_path=args.onnx_path,
                                       mcp_config=args.mcp_config),
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    cli()
