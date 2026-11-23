"""Offline, comparison-only TREND_PULLBACK research. Never creates EA commands."""
import json
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ea_bridge.replay_backtest import run_backtest_data

PRESET = Path(__file__).resolve().parents[2] / "research_presets" / "frozen-trend-pullback-forward-2026-11-23.json"

# Small, interpretable one-factor experiments and a combined candidate.
# Candidate values are hypotheses, NOT suitable live configuration.
PROFILES = {
    "baseline": {},
    "earlier_impulse": {"min_impulse_atr": 1.25},
    "shallower_pullback": {"min_retracement": 0.15},
    "deeper_pullback": {"max_retracement": 0.88},
    "shorter_regime": {"regime_lookback": 5},
    "lower_volatility": {"atr_ratio_min": 0.50},
    "combined_experiment": {
        "min_impulse_atr": 1.25, "min_retracement": 0.15,
        "max_retracement": 0.88, "regime_lookback": 5,
        "atr_ratio_min": 0.50,
    },
}


def summarize(result):
    if result.status_code != 200:
        detail = json.loads(result.content)
        raise CommandError(detail.get("error", "Replay failed"))
    payload = json.loads(result.content)
    if payload.get("selected_setups") != ["TREND_PULLBACK"]:
        raise CommandError("Research scope must be TREND_PULLBACK only")
    state = payload["strategies"][0]
    closed = state["closed_trades"]
    trades = state["trades"]
    return {
        "data_quality": payload["data_quality"],
        "start": payload["start"], "end": payload["end"],
        "evaluations": state["evaluations"],
        "signals": state["signals"], "closed_trades": closed,
        "wins": state["wins"], "win_rate": state["win_rate"],
        "net_gain": state["net_gain"], "profit_factor": state["profit_factor"],
        "max_drawdown_pct": state["max_drawdown_pct"],
        "expectancy_per_closed_trade": round(sum(t["pnl"] for t in trades) / closed, 4) if closed else None,
        "open_position_at_end": state["open_position"],
        "rejection_total": state["rejection_total"], "rejections": state["rejections"],
        "limitations": payload["limitations"],
    }


class Command(BaseCommand):
    help = "Research-only TREND_PULLBACK baseline vs structural thresholds, with untouched holdout."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, help="Exact immutable/validated MarketBar source")
        parser.add_argument("--start", required=True, help="Development range first date (UTC YYYY-MM-DD)")
        parser.add_argument("--holdout-start", required=True, help="First untouched validation date")
        parser.add_argument("--end", required=True, help="Holdout last date")
        parser.add_argument("--max-candles", type=int, default=16000)
        parser.add_argument("--profiles", nargs="+", choices=tuple(PROFILES), default=list(PROFILES))
        parser.add_argument("--output", help="Optional JSON output filename under MEDIA_ROOT research folder")

    def handle(self, *args, **opts):
        try:
            start = date.fromisoformat(opts["start"])
            holdout = date.fromisoformat(opts["holdout_start"])
            end = date.fromisoformat(opts["end"])
        except ValueError as exc:
            raise CommandError("Dates must be YYYY-MM-DD") from exc
        if not start < holdout <= end <= date.today():
            raise CommandError("Require start < holdout-start <= end <= today")
        if opts["max_candles"] < 386 or opts["max_candles"] > 16000:
            raise CommandError("--max-candles must be between 386 and 16000")
        source = opts["source"].strip()
        if not source or len(source) > 128:
            raise CommandError("Provide an exact source name")
        try:
            preset = json.loads(PRESET.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CommandError("Frozen research preset unavailable or invalid") from exc
        assumptions = dict(preset["inputs"])
        assumptions["source"] = source
        assumptions["setups"] = ["TREND_PULLBACK"]
        assumptions.pop("start", None)
        assumptions.pop("end", None)
        # The future holdout is NEVER used to choose parameters automatically.
        periods = (
            ("development", start, holdout - timedelta(days=1)),
            ("holdout", holdout, end),
        )
        results = {}
        for name in dict.fromkeys(opts["profiles"]):
            results[name] = {"overrides": PROFILES[name], "periods": {}}
            for label, a, b in periods:
                trial = {**assumptions, "start": a, "end": b,
                         "research_overrides": PROFILES[name]}
                result = run_backtest_data(trial, max_candles=opts["max_candles"])
                try:
                    summary = summarize(result)
                except CommandError as exc:
                    raise CommandError(f"{name} / {label}: {exc}") from exc
                results[name]["periods"][label] = summary
                self.stdout.write(
                    f"{name:24} {label:12} signals={summary['signals']:4} "
                    f"closed={summary['closed_trades']:4} net={summary['net_gain']:10.2f} "
                    f"drawdown={summary['max_drawdown_pct']:6.2f}% "
                    f"quality={summary['data_quality']['validation_status']}"
                )
        baseline = results.get("baseline")
        if baseline:
            fingerprints = {period: baseline["periods"][period]["data_quality"]["history_fingerprint"]
                            for period, _, _ in periods}
            for name, result in results.items():
                for period, _, _ in periods:
                    if result["periods"][period]["data_quality"]["history_fingerprint"] != fingerprints[period]:
                        raise CommandError(f"History fingerprint changed during {name}/{period}")
        else:
            fingerprints = None
        report = {
            "mode": "ISOLATED_TREND_PULLBACK_SWEEP_NO_LIVE_EXECUTION",
            "validation": "DESCRIPTIVE_ONLY_NOT_APPROVED_FOR_LIVE",
            "source": source, "symbol": assumptions["symbol"],
            "periods": {"development": [start.isoformat(), (holdout-timedelta(days=1)).isoformat()],
                        "holdout": [holdout.isoformat(), end.isoformat()]},
            "frozen_preset": PRESET.name,
            "assumptions": assumptions,
            "baseline_fingerprints": fingerprints,
            "notes": [
                "All tests are simulated on completed historical M15 OHLC, never broker orders.",
                "One-factor candidates are hypotheses; repeated evaluation and data gaps can bias results.",
                "Do not select a configuration based on the holdout; require independent forward paper validation.",
                "Live account scope, command gates, strategy controls and persisted thresholds were not modified.",
            ],
            "results": results,
        }
        folder = Path(settings.MEDIA_ROOT) / "trend-pullback-sweeps"
        folder.mkdir(parents=True, exist_ok=True)
        filename = opts["output"] or ("sweep-" + uuid4().hex[:12] + ".json")
        if Path(filename).name != filename or not filename.endswith(".json"):
            raise CommandError("--output must be a simple .json filename")
        destination = folder / filename
        with destination.open("x", encoding="utf-8") as file:
            json.dump(report, file, indent=2, allow_nan=False)
        self.stdout.write(self.style.SUCCESS("Research report: " + str(destination)))
        self.stdout.write("No live strategy parameters or MT5 execution commands were changed.")
