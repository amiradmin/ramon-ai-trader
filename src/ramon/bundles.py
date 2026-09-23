"""Immutable, checksummed role bundles and one atomic activation pointer."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from .ensemble import (
    BinaryLogisticModel,
    ENTRY_FEATURES,
    LEGACY_META_FEATURES,
    META_FEATURES,
    REGIME_FEATURES,
)
from .news import NEWS_FEATURES

SCHEMA_VERSION = 3
LEGACY_FEATURES = {"regime": REGIME_FEATURES, "entry": ENTRY_FEATURES, "meta": LEGACY_META_FEATURES}
FEATURES = {"regime": REGIME_FEATURES, "entry": ENTRY_FEATURES, "news": NEWS_FEATURES, "meta": META_FEATURES}


def atomic_json(path: Path, value: dict[str, object]) -> None:
    """Replace a JSON document only after its complete contents reach disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_bundle(root: Path, bundle_id: str, chronos_model: str | None = None) -> tuple[dict, dict[str, BinaryLogisticModel]]:
    """Reject partial, corrupted, incompatible or cross-version role sets."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", bundle_id):
        raise ValueError("invalid bundle ID")
    directory = root / "versions" / bundle_id
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    schema_version = int(manifest["schema_version"])
    if schema_version not in {2, SCHEMA_VERSION} or manifest["bundle_id"] != bundle_id:
        raise ValueError("bundle schema/identity mismatch")
    if chronos_model is not None and manifest["chronos_model"] != chronos_model:
        raise ValueError("bundle belongs to a different Chronos checkpoint")
    threshold = float(manifest["trade_threshold"])
    if not 0.5 <= threshold < 1.0:
        raise ValueError("invalid trade threshold")
    expected_features = FEATURES if schema_version == SCHEMA_VERSION else LEGACY_FEATURES
    models = {}
    for role, expected in expected_features.items():
        path = directory / f"{role}.json"
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["sha256"][role]:
            raise ValueError(f"{role} checksum mismatch")
        model = BinaryLogisticModel.load(path)
        if model.feature_names != expected:
            raise ValueError(f"{role} feature schema mismatch")
        models[role] = model
    return manifest, models


def load_active_bundle(root: Path, chronos_model: str | None = None) -> tuple[dict, dict[str, BinaryLogisticModel]]:
    pointer = json.loads((root / "active.json").read_text(encoding="utf-8"))
    return load_bundle(root, pointer["bundle_id"], chronos_model)


def stage_bundle(root: Path, models: dict[str, BinaryLogisticModel], metadata: dict[str, object]) -> str:
    """Write all three models to a new directory; do not change active inference."""
    bundle_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    directory = root / "versions" / bundle_id
    directory.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for role in FEATURES:
        path = directory / f"{role}.json"
        models[role].save(path)
        hashes[role] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {**metadata, "schema_version": SCHEMA_VERSION, "bundle_id": bundle_id, "sha256": hashes,
                "created_at_utc": datetime.now(timezone.utc).isoformat()}
    atomic_json(directory / "manifest.json", manifest)
    load_bundle(root, bundle_id)
    return bundle_id


def activate_bundle(root: Path, bundle_id: str, chronos_model: str | None = None) -> None:
    """Atomically activate a complete approved bundle, retaining its predecessor."""
    manifest, _ = load_bundle(root, bundle_id, chronos_model)
    if not manifest.get("promotion_gate_passed"):
        raise ValueError("bundle has not passed promotion gates")
    pointer = root / "active.json"
    previous = json.loads(pointer.read_text()) if pointer.exists() else {}
    atomic_json(pointer, {"bundle_id": bundle_id, "previous_bundle_id": previous.get("bundle_id"),
                          "activated_at": datetime.now(timezone.utc).isoformat()})


def main() -> None:
    parser = argparse.ArgumentParser(description="Activate/rollback a validated complete role bundle")
    parser.add_argument("--root", default="/checkpoints/ensemble")
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--chronos-model", required=True)
    args = parser.parse_args()
    activate_bundle(Path(args.root), args.bundle, args.chronos_model)
    print("Bundle activated. Restart the model service to load it.")


if __name__ == "__main__":
    main()
