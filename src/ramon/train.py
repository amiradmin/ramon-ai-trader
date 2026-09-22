from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from .history import load_bars
from .model import model_name


def main() -> None:
    """Fine-tune on older bars; leave recent holdout and live model untouched."""
    parser = argparse.ArgumentParser(description="Stage a Chronos-2 LoRA checkpoint from broker history")
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--base", default="autogluon/chronos-2-small")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--out", required=True)
    parser.add_argument("--steps", type=int, default=500)
    args = parser.parse_args()
    bars, _ = load_bars(args.db, args.symbol)
    if len(bars) < 4000:
        raise SystemExit("need >=4000 completed M15 bars before fine-tuning")
    if not 10 <= args.steps <= 5000:
        raise SystemExit("steps must be between 10 and 5000")
    out = Path(args.out).expanduser().resolve()
    if out.exists():
        raise SystemExit("output directory already exists; use a new versioned path")
    if importlib.util.find_spec("peft") is None:
        raise SystemExit("LoRA requires peft: uv sync --extra train")

    import numpy as np
    from chronos import Chronos2Pipeline

    cut = len(bars) * 7 // 10
    validation_end = len(bars) * 8 // 10
    series = np.asarray([bar.close for bar in bars], dtype=np.float32)
    pipeline = Chronos2Pipeline.from_pretrained(model_name(args.base), device_map=args.device)
    trained = pipeline.fit(
        inputs=[series[:cut]],
        validation_inputs=[series[cut:validation_end]],
        prediction_length=4,
        context_length=256,
        finetune_mode="lora",
        learning_rate=1e-5,
        num_steps=args.steps,
        batch_size=16,
        output_dir=out,
    )
    checkpoint = out / "model"
    trained.save_pretrained(checkpoint)
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "symbol": args.symbol,
                "base": args.base,
                "train_bars": cut,
                "validation_bars": validation_end - cut,
                "untouched_holdout_bars": len(bars) - validation_end,
                "last_train_bar_time": bars[cut - 1].time,
                "last_validation_bar_time": bars[validation_end - 1].time,
                "checkpoint": str(checkpoint),
                "promoted": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Staged checkpoint: {checkpoint}; evaluate holdout before serving it")


if __name__ == "__main__":
    main()
