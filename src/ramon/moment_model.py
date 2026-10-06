"""MOMENT anomaly detector for Ramon."""
from __future__ import annotations

import math
import os
from typing import Any

from .core import Market

DEFAULT_MOMENT_CHECKPOINT = "AutonLab/MOMENT-1-small"


class MomentAnomalyModel:
    def __init__(self, *, enabled: bool = False, checkpoint: str = DEFAULT_MOMENT_CHECKPOINT,
                 model: Any | None = None, lazy: bool = False):
        self.enabled = bool(enabled)
        self.checkpoint = checkpoint
        self.model = model
        self.error = ""
        self._torch = None
        if not self.enabled:
            self.error = "disabled"
            return
        if self.model is not None:
            return
        if lazy:
            self.error = "warming"
            return
        self.load()

    def load(self) -> bool:
        """Load MOMENT in-place; safe to call from a background startup worker."""
        if not self.enabled:
            self.error = "disabled"
            return False
        if self.model is not None:
            return True
        try:
            import torch
            from momentfm import MOMENTPipeline
            self._torch = torch
            model = MOMENTPipeline.from_pretrained(
                self.checkpoint,
                model_kwargs={"task_name": "reconstruction"},
            )
            model.init()
            model.eval()
            self.model = model
            self.error = ""
            return True
        except Exception as exc:
            self.model = None
            self.error = f"{type(exc).__name__}: {exc}"
            return False

    @classmethod
    def from_env(cls, *, lazy: bool = False) -> "MomentAnomalyModel":
        enabled = os.getenv("RAMON_MOMENT_ENABLED", "0").strip().lower() in {"1","true","yes","on"}
        return cls(
            enabled=enabled,
            checkpoint=os.getenv("RAMON_MOMENT_CHECKPOINT", DEFAULT_MOMENT_CHECKPOINT).strip() or DEFAULT_MOMENT_CHECKPOINT,
            lazy=lazy,
        )

    @property
    def ready(self) -> bool:
        return self.enabled and self.model is not None

    def status(self) -> dict[str, object]:
        return {
            "moment_enabled": int(self.enabled),
            "moment_ready": int(self.ready),
            "moment_checkpoint": self.checkpoint,
            "moment_error": self.error,
            "moment_effect": "NONE",
        }

    def assess(self, market: Market) -> dict[str, object]:
        payload: dict[str, object] = {
            **self.status(),
            "moment_anomaly_score": -1.0,
            "moment_anomaly_ratio": -1.0,
            "moment_anomaly_label": "UNAVAILABLE",
            "moment_anomaly_bar_time": int(market.bars[-1].time) if market.bars else 0,
        }
        if not self.ready:
            return payload
        try:
            torch = self._torch
            if torch is None:
                import torch as torch_module
                torch = torch_module
            closes = [float(bar.close) for bar in market.bars[-512:]]
            if len(closes) < 32:
                raise ValueError("need at least 32 bars for MOMENT")
            pad = 512 - len(closes)
            values = [closes[0]] * pad + closes
            mask_values = [0.0] * pad + [1.0] * len(closes)
            x = torch.tensor(values, dtype=torch.float32).reshape(1, 1, 512)
            input_mask = torch.tensor(mask_values, dtype=torch.float32).reshape(1, 512)
            with torch.no_grad():
                outputs = self.model.detect_anomalies(
                    x_enc=x, input_mask=input_mask, anomaly_criterion="mse"
                )
            scores = outputs.anomaly_scores
            if hasattr(scores, "detach"):
                scores = scores.detach().cpu()
            flat = scores.reshape(-1).tolist()
            valid = [float(v) for v in flat[-len(closes):] if math.isfinite(float(v))]
            if not valid:
                raise ValueError("empty MOMENT anomaly scores")
            tail = valid[-min(8, len(valid)):]
            current = sum(tail) / len(tail)
            ordered = sorted(valid)
            median = ordered[len(ordered)//2]
            ratio = current / max(median, 1e-12)
            label = "ELEVATED" if ratio >= 2.0 else "NORMAL"
            payload.update(
                moment_ready=1,
                moment_error="",
                moment_anomaly_score=current,
                moment_anomaly_ratio=ratio,
                moment_anomaly_label=label,
            )
        except Exception as exc:
            payload["moment_ready"] = 0
            payload["moment_error"] = f"{type(exc).__name__}: {exc}"
        return payload
