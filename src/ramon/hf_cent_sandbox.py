"""Audit a pinned external gold model. Never sends orders or runs remote code."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

MODEL_ID = "addyAIMLprojects/xauusd-v03h-sell"
REVISION = "c32ecee7ff0b1c38311c6d279cc9f4c070265f5d"


def validate_features(order: list[str], values: dict[str, float]) -> list[float]:
    """Require exact names, order and finite values; never fill missing inputs."""
    if len(order) != 300 or len(set(order)) != 300 or not all(isinstance(x, str) for x in order):
        raise ValueError("Expected 300 unique feature names")
    if set(order) != set(values):
        raise ValueError("Feature names do not match the model contract")
    result = []
    for name in order:
        value = values[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"Invalid feature: {name}")
        result.append(float(value))
    return result


def audit(directory: Path) -> dict:
    names = ("README.md", "feature_order.json", "policy_config.json", "model_registry.json",
             "final_rf_sell_model.onnx", "score_distribution.parquet")
    files = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
             for name in names if (directory / name).is_file()}
    return {
        "mode": "SHADOW_ONLY", "execution_enabled": False,
        "model_id": MODEL_ID, "revision": REVISION,
        "status": "BLOCKED_UNVERIFIED_FEATURE_PIPELINE",
        "missing_files": [name for name in names if name not in files],
        "files_sha256": files,
        "required_before_inference": [
            "Verify training-equivalent computation and exact order of all 300 features",
            "Verify historical broker timestamps against UTC including DST",
            "Verify SELL class mapping and reference score percentile semantics",
            "Verify source provenance and model artifact hashes against pinned revision",
            "Resolve README rank >=0.90 versus policy RF_TOP1 / top 1 percent",
        ],
        "note": "This report never infers profitability or generates a trade signal.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.model_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
