import json
import sqlite3
import zlib
from pathlib import Path

from ramon.monitor import exact_decision_market_context


def test_exact_decision_input_uses_recorded_broker_offset(tmp_path: Path):
    db = tmp_path / "history.db"
    sample_key = "0123456789abcdef"
    context = {
        "symbol": "XAUUSD_l",
        "timeframe": "M15",
        "bars": [
            {"time": 1_800_003_600, "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5},
            {"time": 1_800_004_500, "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5},
        ],
    }
    request = {
        "bid": 101.4,
        "ask": 101.6,
        "point": 0.1,
        "quote_time": 1_800_005_430,
        "broker_utc_offset_seconds": 3600,
        "micro_bars": [
            {"time": 1_800_005_240, "open": 101.0, "high": 101.2, "low": 100.9, "close": 101.1},
            {"time": 1_800_005_300, "open": 101.1, "high": 101.4, "low": 101.0, "close": 101.3},
            {"time": 1_800_005_360, "open": 101.3, "high": 101.5, "low": 101.2, "close": 101.4},
            {"time": 1_800_005_420, "open": 101.4, "high": 101.6, "low": 101.3, "close": 101.5},
        ],
    }
    body = json.dumps(context, separators=(",", ":")).encode()
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE input_blobs (sha256 TEXT PRIMARY KEY, codec TEXT NOT NULL, body BLOB NOT NULL)")
        con.execute("""CREATE TABLE inference_audit (
            sample_key TEXT PRIMARY KEY, input_sha256 TEXT NOT NULL,
            recorded_utc REAL NOT NULL, response_json TEXT NOT NULL, provenance_json TEXT NOT NULL)""")
        con.execute("INSERT INTO input_blobs VALUES (?,?,?)", ("abc", "zlib-json-v1", zlib.compress(body)))
        con.execute(
            "INSERT INTO inference_audit VALUES (?,?,?,?,?)",
            (
                sample_key,
                "abc",
                1_800_001_000.0,
                "{}",
                json.dumps({"request": request}),
            ),
        )

    result = exact_decision_market_context(db, sample_key)
    assert result is not None
    assert result["exact_input"] is True
    assert result["warnings"] == []
    assert result["broker_utc_offset_seconds"] == 3600
    assert result["m15"][-1]["time"] == 1_800_004_500 - 3600
    assert result["m1"][-1]["time"] == 1_800_005_420 - 3600
    assert result["quote_time_utc"] == 1_800_005_430 - 3600
    assert result["m1"][-1]["spread_points"] == 2
