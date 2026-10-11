import csv
import json

from ramon.m5_recovery_sidecar import export_sidecar, select_recovery_candles


def candle(stamp, close=100.5):
    return {"time": str(stamp), "open": "100", "high": "101", "low": "99", "close": str(close), "spread_points": "4"}


def test_selects_only_valid_unique_missing_candles():
    selected, report = select_recovery_candles({300, 600, 900}, [
        candle(300), candle(300), candle(600, 100.8), candle(600, 100.7), candle(1200),
        {"time": "900", "open": "100", "high": "99", "low": "98", "close": "100"},
    ])
    assert [row[0] for row in selected] == [300]
    assert report["conflicting_timestamps"] == [600]
    assert report["unresolved_timestamps"] == 1
    assert report["malformed_candidate_rows"] == 1


def test_export_is_sidecar_and_records_candidate_checksum(tmp_path):
    missing = tmp_path / "missing.json"
    missing.write_text(json.dumps({"missing_candle_open_timestamps": {"M5": [{"open_utc": 300}]}}))
    candidate = tmp_path / "mt5.csv"
    with candidate.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("time", "open", "high", "low", "close", "spread_points"))
        writer.writeheader(); writer.writerow(candle(300))
    output, report = tmp_path / "sidecar.csv", tmp_path / "report.json"
    result = export_sidecar(missing, candidate, output, report)
    assert result["selected_real_candles"] == 1
    assert len(result["candidate_csv_sha256"]) == 64
    assert list(csv.DictReader(output.open()))[0]["time"] == "300"
