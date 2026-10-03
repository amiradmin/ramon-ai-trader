"""FinBERT financial sentiment model for Ramon.

Consumes the nearest financial-calendar event text. The server may use a strong
non-neutral result as a conservative live entry veto; it never creates a trade.
"""
from __future__ import annotations

import os
from typing import Any

DEFAULT_FINBERT_CHECKPOINT = "ProsusAI/finbert"


class FinBertNewsShadow:
    def __init__(self, *, enabled: bool = False, checkpoint: str = DEFAULT_FINBERT_CHECKPOINT,
                 classifier: Any | None = None, lazy: bool = False):
        self.enabled = bool(enabled)
        self.checkpoint = checkpoint
        self.classifier = classifier
        self.error = ""
        if not self.enabled:
            self.error = "disabled"
            return
        if self.classifier is not None:
            return
        if lazy:
            self.error = "warming"
            return
        self.load()

    def load(self) -> bool:
        """Load FinBERT in-place; safe to call from a background startup worker."""
        if not self.enabled:
            self.error = "disabled"
            return False
        if self.classifier is not None:
            return True
        try:
            from transformers import pipeline
            classifier = pipeline(
                "text-classification",
                model=self.checkpoint,
                tokenizer=self.checkpoint,
                device=-1,
                top_k=None,
            )
            self.classifier = classifier
            self.error = ""
            return True
        except Exception as exc:
            self.classifier = None
            self.error = f"{type(exc).__name__}: {exc}"
            return False

    @classmethod
    def from_env(cls, *, lazy: bool = False) -> "FinBertNewsShadow":
        enabled = os.getenv("RAMON_FINBERT_SHADOW_ENABLED", "0").strip().lower() in {"1","true","yes","on"}
        return cls(
            enabled=enabled,
            checkpoint=os.getenv("RAMON_FINBERT_CHECKPOINT", DEFAULT_FINBERT_CHECKPOINT).strip() or DEFAULT_FINBERT_CHECKPOINT,
            lazy=lazy,
        )

    @property
    def ready(self) -> bool:
        return self.enabled and self.classifier is not None

    def status(self) -> dict[str, object]:
        return {
            "finbert_shadow_enabled": int(self.enabled),
            "finbert_shadow_ready": int(self.ready),
            "finbert_shadow_checkpoint": self.checkpoint,
            "finbert_shadow_error": self.error,
            "finbert_shadow_effect": "NONE",
        }

    def assess(self, *, title: str, country: str, impact: str) -> dict[str, object]:
        payload: dict[str, object] = {
            **self.status(),
            "finbert_sentiment_label": "UNAVAILABLE",
            "finbert_positive": -1.0,
            "finbert_negative": -1.0,
            "finbert_neutral": -1.0,
            "finbert_directional_score": 0.0,
        }
        if not self.ready or not title or title == "NONE":
            return payload
        try:
            text = f"{country} {impact} economic event: {title}".strip()
            result = self.classifier(text, truncation=True)
            rows = result[0] if result and isinstance(result[0], list) else result
            scores = {str(row["label"]).lower(): float(row["score"]) for row in rows}
            pos = scores.get("positive", 0.0)
            neg = scores.get("negative", 0.0)
            neu = scores.get("neutral", 0.0)
            label = max(("positive","negative","neutral"), key=lambda name: scores.get(name, -1.0)).upper()
            payload.update(
                finbert_shadow_ready=1,
                finbert_shadow_error="",
                finbert_sentiment_label=label,
                finbert_positive=pos,
                finbert_negative=neg,
                finbert_neutral=neu,
                finbert_directional_score=pos-neg,
            )
        except Exception as exc:
            payload["finbert_shadow_ready"] = 0
            payload["finbert_shadow_error"] = f"{type(exc).__name__}: {exc}"
        return payload
