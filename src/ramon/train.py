from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from .history import load_bars
from .model import model_name


def train_checkpoint(
    *,
    db: str,
    symbol: str,
    base: str,
    device: str,
    out: str | Path,
    steps: int,
) -> Path:
    """Fine-tune one versioned Chronos-2 LoRA challenger and return its model path."""
    bars, _ = load_bars(db, symbol)
    if len(bars) < 4000:
        raise ValueError("need >=4000 completed M15 bars before fine-tuning")
    if not 10 <= steps <= 5000:
        raise ValueError("steps must be between 10 and 5000")
    output = Path(out).expanduser().resolve()
    if output.exists():
        raise ValueError("output directory already exists; use a new versioned path")
    if importlib.util.find_spec("peft") is None:
        raise RuntimeError("LoRA requires peft: uv sync --extra train")

    import numpy as np
    from chronos import Chronos2Pipeline

    cut = len(bars) * 7 // 10
    validation_end = len(bars) * 8 // 10
    series = np.asarray([bar.close for bar in bars], dtype=np.float32)
    pipeline = Chronos2Pipeline.from_pretrained(model_name(base), device_map=device)
    trained = pipeline.fit(
        inputs=[series[:cut]],
        validation_inputs=[series[cut:validation_end]],
        prediction_length=4,
        context_length=256,
        finetune_mode="lora",
        learning_rate=1e-5,
        num_steps=steps,
        batch_size=16,
        output_dir=output,
    )
    checkpoint = output / "model"
    trained.save_pretrained(checkpoint)
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "symbol": symbol,
                "base": base,
                "total_bars": len(bars),
                "train_bars": cut,
                "validation_bars": validation_end - cut,
                "untouched_holdout_bars": len(bars) - validation_end,
                "last_train_bar_time": bars[cut - 1].time,
                "last_validation_bar_time": bars[validation_end - 1].time,
                "latest_available_bar_time": bars[-1].time,
                "steps": steps,
                "checkpoint": str(checkpoint),
                "promoted": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return checkpoint


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage a Chronos-2 LoRA checkpoint from broker history")
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--base", default="autogluon/chronos-2-small")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--out", required=True)
    parser.add_argument("--steps", type=int, default=500)
    args = parser.parse_args()
    try:
        checkpoint = train_checkpoint(
            db=args.db,
            symbol=args.symbol,
            base=args.base,
            device=args.device,
            out=args.out,
            steps=args.steps,
        )
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Staged checkpoint: {checkpoint}; evaluate holdout before serving it")


if __name__ == "__main__":
    main()
