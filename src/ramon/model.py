from __future__ import annotations

from collections.abc import Sequence
import hashlib
import os
import json
from pathlib import Path

from .core import Forecast


SUPPORTED_CHRONOS_MODELS = frozenset({"autogluon/chronos-2-small"})


def configure_cpu_workers(workers: int) -> dict[str, int]:
    """Configure CPU inference thread pools before loading Chronos."""
    if workers < 1:
        raise ValueError("cpu workers must be >=1")

    value = str(int(workers))
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = value

    import torch

    torch.set_num_threads(workers)
    # PyTorch only allows changing inter-op threads before parallel work starts.
    try:
        torch.set_num_interop_threads(max(1, min(workers, 2)))
    except RuntimeError:
        pass

    return {
        "requested": workers,
        "torch_intraop": int(torch.get_num_threads()),
        "torch_interop": int(torch.get_num_interop_threads()),
    }


class ChronosForecaster:
    """Load a real Chronos-2 checkpoint; model failures never produce orders."""

    def __init__(
        self,
        model_id: str = "autogluon/chronos-2-small",
        device: str = "cpu",
        cpu_workers: int | None = None,
    ) -> None:
        self.cpu_workers = None
        if device == "cpu" and cpu_workers is not None:
            self.cpu_workers = configure_cpu_workers(cpu_workers)

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
    """Allow a supported Hub model, full local checkpoint, or local LoRA adapter."""
    if value in SUPPORTED_CHRONOS_MODELS:
        return value
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("model must be a supported Chronos-2 ID or local checkpoint")

    # Full Hugging Face checkpoints expose config.json. Chronos-2 LoRA training
    # intentionally saves a PEFT adapter instead, whose entrypoint is
    # adapter_config.json plus adapter weights.
    if (path / "config.json").is_file():
        return str(path)

    adapter_config = path / "adapter_config.json"
    adapter_weights = (
        (path / "adapter_model.safetensors").is_file()
        or (path / "adapter_model.bin").is_file()
    )
    if not adapter_config.is_file() or not adapter_weights:
        raise ValueError("model must be a supported Chronos-2 ID or local checkpoint")
    try:
        adapter = json.loads(adapter_config.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError("local Chronos-2 adapter config is unreadable") from exc
    if adapter.get("peft_type") != "LORA":
        raise ValueError("local Chronos-2 adapter must use LoRA")
    if adapter.get("base_model_name_or_path") not in SUPPORTED_CHRONOS_MODELS:
        raise ValueError("local adapter must target a supported Chronos-2 base model")
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
