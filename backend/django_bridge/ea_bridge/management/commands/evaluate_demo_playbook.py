"""Read-only chronological comparison of the demo playbook and original policy."""
from ea_bridge.account_scope import real_cent_scope

from dataclasses import replace
import json
import math

from django.core.management.base import BaseCommand, CommandError
from django.test import override_settings
from ea_bridge.policy_snapshot import effective_profiles, fingerprint

from ea_bridge.decision import _build_pipeline
from ea_bridge.models import EAHeartbeat, MarketBar
from ramon_pipeline.signalflow.replay import replay_strategy


class Command(BaseCommand):
    help = "Compare demo policies on persisted candles; never create commands or change trading settings."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--allow-off-grid", action="store_true",
                            help="Explicitly exclude misaligned historical rows (descriptive studies only).")
        parser.add_argument("--profile", choices=("standard", "expanded"), default="standard",
                            help="Use standard thresholds by default; expanded is a separate research policy.")
        parser.add_argument("--symbol", default="XAUUSD_i")
        parser.add_argument("--limit", type=int, default=2000)
        parser.add_argument("--spread-points", type=float, required=True)
        parser.add_argument("--slippage-points", type=float, required=True)
        parser.add_argument("--commission-points", type=float, required=True,
                            help="Round-trip commission converted into symbol points.")
        parser.add_argument("--holding-bars", type=int, default=24)
        parser.add_argument("--balance", type=float, default=10000,
                            help="Starting paper balance for both policies, not a broker balance update.")

    def handle(self, *args, **options):
        if not 129 <= options["limit"] <= 20000:
            raise CommandError("--limit must be between 129 and 20000")
        heartbeat = EAHeartbeat.objects.filter(instance_id=options["source"], symbol=options["symbol"]).first()
        payload = heartbeat.payload if heartbeat and isinstance(heartbeat.payload, dict) else {}
        if not real_cent_scope(heartbeat)[0]:
            raise CommandError("A matching, configured real cent EA heartbeat is required for broker metadata.")
        try:
            quote, info = payload["quote"], payload["symbol_info"]
            point = float(quote["point"])
            account = {"symbol": options["symbol"], "account_balance": options["balance"], "point": point,
                       "point_value_per_lot": float(info["tick_value"])*point/float(info["tick_size"]),
                       "volume_step": float(info["volume_step"]), "min_volume": float(info["volume_min"]),
                       "max_volume": float(info["volume_max"]), "stop_level_points": float(info["stops_level_points"])}
            if not all(math.isfinite(value) and value > 0 for key, value in account.items()
                       if key not in {"symbol", "stop_level_points"}):
                raise ValueError("invalid broker metadata")
            if not math.isfinite(account["stop_level_points"]) or account["stop_level_points"] < 0:
                raise ValueError("invalid broker stop level")
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            raise CommandError(f"Complete valid real cent symbol metadata is required: {exc}") from exc

        quality = {}
        periods = {"M15": 900, "M5": 300, "H1": 3600}

        def candles(timeframe, limit):
            rows = list(MarketBar.objects.filter(source=options["source"], symbol=options["symbol"],
                                                timeframe=timeframe).order_by("-timestamp")[:limit])
            period = periods[timeframe]
            accepted = [row for row in rows if row.timestamp.microsecond == 0
                        and int(row.timestamp.timestamp()) % period == 0]
            quality[timeframe] = {"input_rows": len(rows),
                                  "off_grid_rows_excluded": len(rows) - len(accepted)}
            return [{"time": row.timestamp.timestamp(), "open": float(row.open),
                     "high": float(row.high), "low": float(row.low),
                     "close": float(row.close), "closed": True} for row in reversed(accepted)]

        bars = candles("M15", options["limit"])
        if len(bars) < 129:
            raise CommandError("At least 129 valid completed M15 candles are required.")
        # Load the same historical span for each timeframe; never mix series or sources.
        context = {"H1": candles("H1", options["limit"]//4+120),
                   "M5": candles("M5", options["limit"]*3+120)}
        off_grid_total = sum(item["off_grid_rows_excluded"] for item in quality.values())
        if off_grid_total and not options["allow_off_grid"]:
            raise CommandError(f"{off_grid_total} off-grid candles detected in M15/M5/H1; "
                               "repair/reimport source data first, or pass --allow-off-grid "
                               "for an explicitly labeled descriptive comparison.")

        def factory(compare):
            def build():
                with override_settings(RAMON_DEMO_PROFILE=options["profile"]):
                    pipeline = _build_pipeline(demo_mode=options["profile"] == "expanded")
                pipeline.compare_candidates = compare
                pipeline.entry_timer.config = replace(pipeline.entry_timer.config, ignore_supplied_quality=compare)
                return pipeline
            return build

        with override_settings(RAMON_DEMO_PROFILE=options["profile"]):
            profiles = effective_profiles()
        policy_sha256 = fingerprint(profiles)

        kwargs = {"context_bars": context, "account": account,
                  "spread_points": options["spread_points"], "slippage_points": options["slippage_points"],
                  "commission_points": options["commission_points"], "max_holding_bars": options["holding_bars"]}
        try:
            report = {"symbol": options["symbol"], "source": options["source"], "profile": options["profile"],
                      "profiles": profiles, "policy_sha256": policy_sha256,
                      "replay_execution": {"spread_points": options["spread_points"],
                                           "slippage_points": options["slippage_points"],
                                           "commission_points": options["commission_points"],
                                           "holding_bars": options["holding_bars"],
                                           "balance": options["balance"],
                                           "candidate_comparison": "BASELINE_VS_COMPARATIVE_CANDIDATES"},
                      "m15_bars": len(bars),
                      "first_bar": bars[0]["time"], "last_bar": bars[-1]["time"],
                      "context_bar_counts": {tf: len(rows) for tf, rows in context.items()},
                      "data_quality": {"timeframes": quality, "total_off_grid_rows_excluded": off_grid_total,
                                       "policy": "EXPLICIT_EXCLUSION_NO_DATABASE_CHANGES" if off_grid_total else "CLEAN_ALIGNED_INPUT"},
                      "validation": "DESCRIPTIVE_REPLAY_NOT_OUT_OF_SAMPLE_PROOF",
                      "baseline": replay_strategy(factory(False), bars, **kwargs),
                      "playbook": replay_strategy(factory(True), bars, **kwargs)}
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, indent=2, allow_nan=False))
