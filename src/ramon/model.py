from __future__ import annotations

from collections.abc import Sequence
import hashlib
from pathlib import Path

from .core import Forecast


class ChronosForecaster:
    """Load a real Chronos-2 checkpoint; model failures never produce orders."""

    def __init__(self, model_id: str = "autogluon/chronos-2-small", device: str = "cpu") -> None:
        import numpy as np
        from chronos import Chronos2Pipeline

        self._np = np
        self.model_id = model_id
        self.pipeline = Chronos2Pipeline.from_pretrained(model_id, device_map=device)
        self.revision = checkpoint_revision(model_id, self.pipeline)

    def forecast(self, closes: Sequence[float], horizon: int) -> Forecast:
        values = self._np.asarray(closes, dtype=self._np.float32)
        quantiles, _ = self.pipeline.predict_quantiles(
            [values], prediction_length=horizon, quantile_levels=[0.1, 0.5, 0.9]
        )
        rows = quantiles[0][0].tolist()
        if not isinstance(rows, list) or len(rows) != horizon:
            raise ValueError("invalid Chronos horizon output")
        if any(not isinstance(row, list) or len(row) < 3 for row in rows):
            raise ValueError("invalid Chronos quantile output")
        low, median, high = rows[-1][:3]
        median_path = tuple(float(row[1]) for row in rows)
        return Forecast(float(low), float(median), float(high), median_path)


def model_name(value: str) -> str:
    """Allow only an explicit local checkpoint or a named Chronos-2 model."""
    if value in {"autogluon/chronos-2-small", "amazon/chronos-2"}:
        return value
    path = Path(value).expanduser().resolve()
    if not path.is_dir() or not (path / "config.json").exists():
        raise ValueError("model must be a supported Chronos-2 ID or local checkpoint")
    return str(path)


def checkpoint_revision(model_id: str, pipeline: object) -> str | None:
    """Identify loaded local weights once, or retain the resolved Hub commit when exposed."""
    path = Path(model_id)
    if path.is_dir():
        files = sorted(p for p in path.rglob("*") if p.is_file()
                       and p.suffix in {".json", ".safetensors", ".bin"})
        if not files:
            return None
        digest = hashlib.sha256()
        for file in files:
            digest.update(str(file.relative_to(path)).encode() + b"\0")
            with file.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        return "sha256:" + digest.hexdigest()
    config = getattr(getattr(pipeline, "model", None), "config", None)
    return getattr(config, "_commit_hash", None)
