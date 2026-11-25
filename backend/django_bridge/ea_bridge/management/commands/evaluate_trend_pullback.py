"""Offline chronological Trend Pullback comparison; no settings or order writes."""
import json
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.http import QueryDict
from types import SimpleNamespace
from ea_bridge.replay_backtest import _inputs, run_backtest_data

BASE = dict(min_impulse_atr=1.5, min_retracement=0.2, max_retracement=0.82,
            zone_tolerance_atr=0.4, regime_lookback=8)
VARIANTS = {"baseline": BASE, **{
    name: {**BASE, **changes} for name, changes in (
        ("impulse_1_2", {"min_impulse_atr": 1.2}),
        ("impulse_1_0", {"min_impulse_atr": 1.0}),
        ("retracement_15", {"min_retracement": 0.15}),
        ("retracement_70", {"max_retracement": 0.70}),
        ("retracement_85", {"max_retracement": 0.85}),
        ("zone_06", {"zone_tolerance_atr": 0.6}),
        ("zone_08", {"zone_tolerance_atr": 0.8}),
        ("regime_4", {"regime_lookback": 4}),
    )
}}


V2 = {"baseline": {}, "utc_00_06": {"entry_hours": [0,6]},
      "utc_06_12": {"entry_hours": [6,12]}, "utc_12_18": {"entry_hours": [12,18]},
      "utc_18_24": {"entry_hours": [18,24]}, "h1_alignment": {"h1_confirmation": True},
      **{f"reward_{r}": {"target_reward_risk": r} for r in (1.5,2.0,2.5)},
      **{f"breakeven_{r}": {"breakeven_r": r} for r in (0.75,1.0)},
      **{f"trailing_{r}": {"trailing_atr": r} for r in (1.0,1.5)},
      **{f"time_{n}": {"time_exit": n} for n in (4,8,16,24)},
      **{f"zone_{z}": {"opportunity": {"zone_tolerance_atr": z}} for z in (0.6,0.8,1.0)},
      "morning_deep": {"entry_hours": [6,12], "opportunity": {"max_retracement": 0.85}},
      "morning_h1": {"entry_hours": [6,12], "h1_confirmation": True}}


class Command(BaseCommand):
    help = "Compare Trend Pullback candidates on disjoint chronological periods; descriptive only."

    def add_arguments(self, parser):
        for name in ("source", "symbol", "start", "split", "end"):
            parser.add_argument("--" + name, required=True)
        for name in ("balance", "spread-points", "point", "tick-size", "tick-value",
                     "volume-min", "volume-step", "volume-max", "slippage-points", "commission-per-lot"):
            parser.add_argument("--" + name, required=True, type=float)
        parser.add_argument("--version", choices=("1","2"), default="1")
        parser.add_argument("--minimum-trades", type=int, default=30)
        parser.add_argument("--max-candles", type=int, default=20000)

    def handle(self, *args, **options):
        try:
            start, split, end = (date.fromisoformat(options[k]) for k in ("start", "split", "end"))
            if not start <= split < end or not 1 <= options["minimum_trades"] or not 130 <= options["max_candles"] <= 100000:
                raise ValueError("Invalid chronological windows or limits")
            q = QueryDict(mutable=True)
            for key in ("source", "symbol", "start", "end", "balance", "spread_points",
                        "point", "tick_size", "tick_value", "volume_min", "volume_step",
                        "volume_max", "slippage_points", "commission_per_lot"):
                q[key] = str(options[key])
            q.setlist("setups", ["TREND_PULLBACK"])
            data = _inputs(SimpleNamespace(GET=q))
        except (ValueError, KeyError) as exc:
            raise CommandError(str(exc)) from exc

        v2 = options["version"] == "2"
        variants = V2 if v2 else VARIANTS

        def evaluate(params, first, last, *, phase, name, index, total):
            last_percent = -1
            self.stderr.write(f"[{phase} {index}/{total}] {name}: starting")
            def progress(done, candles):
                nonlocal last_percent
                percent = min(100, int(100 * done / max(1, candles)))
                if percent != last_percent:
                    self.stderr.write(f"[{phase} {index}/{total}] {name}: {percent:3d}% ({done:,}/{candles:,} candles)")
                    self.stderr.flush()
                    last_percent = percent

            overrides = dict(data, start=first, end=last)
            if v2:
                overrides["opportunity_overrides"] = {**BASE, **params.get("opportunity", {})}
                overrides["trend_research"] = {"version": 2, **params}
                overrides["target_reward_risk"] = params.get("target_reward_risk", data["target_reward_risk"])
            else:
                overrides["opportunity_overrides"] = params
            response = run_backtest_data(overrides, max_candles=options["max_candles"], progress=progress)
            result = json.loads(response.content)
            if response.status_code != 200:
                raise CommandError(result.get("error", "Research failed"))
            stats = result["strategies"][0]
            stats.pop("trades", None)
            return dict(metrics=stats, data_quality=result["data_quality"],
                        assumptions=result["assumptions"], limitations=result["limitations"])

        development = {name: evaluate(params, start, split, phase="development", name=name,
                                      index=index, total=len(variants))
                       for index, (name, params) in enumerate(variants.items(), 1)}
        minimum = options["minimum_trades"]
        eligible = [name for name, report in development.items()
                    if report["metrics"]["closed_trades"] >= minimum
                    and report["metrics"]["net_gain"] > 0
                    and report["metrics"]["profit_factor"] is not None
                    and report["metrics"]["profit_factor"] > 1]
        winner = max(eligible, key=lambda name: (
            development[name]["metrics"]["net_gain"] / max(1, development[name]["metrics"]["max_drawdown_pct"]),
            name)) if eligible else None
        selected = list(dict.fromkeys(["baseline"] + ([winner] if winner else [])))
        validation = {name: evaluate(variants[name], split + timedelta(days=1), end,
                                    phase="validation", name=name, index=index, total=len(selected))
                      for index, name in enumerate(selected, 1)}
        self.stdout.write(json.dumps(dict(mode="ISOLATED_RESEARCH_DESCRIPTIVE_ONLY",
            live_promotion=False, research_version=options["version"], variants=variants, development=development,
            validation=validation, development_candidate=winner, minimum_trades=minimum,
            windows=dict(development=[str(start), str(split)],
                         validation=[str(split + timedelta(days=1)), str(end)]),
            limitations="Standard research risk capped at 0.5%; not live 1.5% parity. "
                        "Independent starting balance per period, fixed costs, OHLC stop-first approximation, "
                        "closed-trade drawdown only; no live settings or orders changed. "
                        "Validation results are descriptive, not deployment authorization. V2 hypotheses were informed by V1, including previously viewed 2026 baseline results; fresh forward data is required. V2 H1 uses completed M15 groups, stops update at close for next bar, and time exits use modeled close."),
            indent=2))
        self.stderr.write("[research] 100% complete; JSON report written.")
        self.stderr.flush()
