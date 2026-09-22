from __future__ import annotations

import argparse
import gc
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .history import history_count, load_bars
from .model import ChronosForecaster, model_name
from .replay import ReplayResult, replay
from .train import train_checkpoint


def _evaluate(db: str, symbol: str, model_value: str, device: str) -> ReplayResult:
    bars, spreads = load_bars(db, symbol)
    model = ChronosForecaster(model_name(model_value), device)
    try:
        return replay(bars, spreads, model, stride=4)
    finally:
        del model
        gc.collect()


def _promotion_gate(
    incumbent: ReplayResult,
    challenger: ReplayResult,
    *,
    minimum_trades: int,
    minimum_improvement_r: float,
    maximum_drawdown_r: float,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    trades = challenger.buys + challenger.sells
    if trades < minimum_trades:
        reasons.append(f"challenger trades {trades} < {minimum_trades}")
    if challenger.net_r <= 0:
        reasons.append(f"challenger net_r {challenger.net_r} <= 0")
    if challenger.net_r < incumbent.net_r + minimum_improvement_r:
        reasons.append(
            f"net_r improvement {challenger.net_r - incumbent.net_r:.4f} "
            f"< {minimum_improvement_r:.4f}"
        )
    if challenger.max_drawdown_r > maximum_drawdown_r:
        reasons.append(
            f"challenger drawdown {challenger.max_drawdown_r:.4f} "
            f"> {maximum_drawdown_r:.4f}"
        )
    if challenger.max_drawdown_r > incumbent.max_drawdown_r + 1.0:
        reasons.append("challenger drawdown worsened by more than 1R versus incumbent")
    return not reasons, reasons


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily Ramon challenger training and guarded promotion")
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--base", default="autogluon/chronos-2-small")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--checkpoints-root", default="/checkpoints/daily")
    parser.add_argument("--active-model-file", default="/checkpoints/active_model.txt")
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--minimum-bars", type=int, default=4000)
    parser.add_argument("--minimum-trades", type=int, default=20)
    parser.add_argument("--minimum-improvement-r", type=float, default=0.5)
    parser.add_argument("--maximum-drawdown-r", type=float, default=8.0)
    parser.add_argument("--auto-promote", action="store_true")
    args = parser.parse_args()

    bars_count = history_count(args.db, args.symbol)
    if bars_count < args.minimum_bars:
        print(json.dumps({
            "status": "waiting_for_history",
            "bars": bars_count,
            "minimum_bars": args.minimum_bars,
        }))
        return

    bars, _ = load_bars(args.db, args.symbol)
    latest_bar = bars[-1].time
    root = Path(args.checkpoints_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    state_file = root / "daily_learning_state.json"
    if state_file.is_file():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}
        if int(state.get("latest_bar_time", 0)) == latest_bar:
            print(json.dumps({"status": "already_trained_latest_bar", "latest_bar_time": latest_bar}))
            return

    active_file = Path(args.active_model_file).expanduser().resolve()
    incumbent_model = args.base
    if active_file.is_file():
        candidate = active_file.read_text(encoding="utf-8").strip()
        if candidate:
            incumbent_model = candidate

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = root / f"challenger-{stamp}"
    checkpoint = train_checkpoint(
        db=args.db,
        symbol=args.symbol,
        base=args.base,
        device=args.device,
        out=out,
        steps=args.steps,
    )

    incumbent_result = _evaluate(args.db, args.symbol, incumbent_model, args.device)
    challenger_result = _evaluate(args.db, args.symbol, str(checkpoint), args.device)
    promote, reasons = _promotion_gate(
        incumbent_result,
        challenger_result,
        minimum_trades=args.minimum_trades,
        minimum_improvement_r=args.minimum_improvement_r,
        maximum_drawdown_r=args.maximum_drawdown_r,
    )
    promoted = bool(args.auto_promote and promote)
    if promoted:
        active_file.parent.mkdir(parents=True, exist_ok=True)
        active_file.write_text(str(checkpoint) + "\n", encoding="utf-8")

    report = {
        "status": "trained",
        "latest_bar_time": latest_bar,
        "bars": bars_count,
        "base": args.base,
        "incumbent_model": incumbent_model,
        "challenger_model": str(checkpoint),
        "incumbent": asdict(incumbent_result),
        "challenger": asdict(challenger_result),
        "promotion_gate_passed": promote,
        "auto_promote": args.auto_promote,
        "promoted": promoted,
        "gate_reasons": reasons,
    }
    (out / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["promoted"] = promoted
    manifest["promotion_gate_passed"] = promote
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    state_file.write_text(
        json.dumps(
            {
                "latest_bar_time": latest_bar,
                "last_run_utc": stamp,
                "last_challenger": str(checkpoint),
                "promoted": promoted,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()
