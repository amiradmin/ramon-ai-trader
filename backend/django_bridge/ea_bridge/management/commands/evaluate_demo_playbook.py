"""Read-only chronological comparison of the demo playbook and original policy."""
from dataclasses import replace
import json
import math

from django.core.management.base import BaseCommand, CommandError

from ea_bridge.decision import _build_pipeline
from ea_bridge.models import EAHeartbeat, MarketBar
from ramon_pipeline.signalflow.replay import replay_strategy


class Command(BaseCommand):
    help = "Compare demo policies on persisted candles; never create commands or change trading settings."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--symbol", default="XAUUSD_o")
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
        if type(payload.get("account_trade_mode")) is not int or payload["account_trade_mode"] != 0:
            raise CommandError("A matching, explicitly demo EA heartbeat is required for broker metadata.")
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
            raise CommandError(f"Complete valid demo symbol metadata is required: {exc}") from exc

        def candles(timeframe, limit):
            rows = list(MarketBar.objects.filter(source=options["source"], symbol=options["symbol"],
                                                timeframe=timeframe).order_by("-timestamp")[:limit])
            return [{"time": row.timestamp.timestamp(), "open": float(row.open), "high": float(row.high),
                     "low": float(row.low), "close": float(row.close), "closed": True} for row in reversed(rows)]

        raw_bars = candles("M15", options["limit"])
        bars = [bar for bar in raw_bars if bar["time"] % 900 == 0]
        if len(bars) < 129:
            raise CommandError("At least 129 persisted completed M15 candles are required.")
        # Load the same historical span for each timeframe; never mix series or sources.
        context = {"H1": candles("H1", options["limit"]//4+120),
                   "M5": candles("M5", options["limit"]*3+120)}

        def factory(compare):
            def build():
                pipeline = _build_pipeline(demo_mode=True)
                pipeline.compare_candidates = compare
                pipeline.entry_timer.config = replace(pipeline.entry_timer.config, ignore_supplied_quality=compare)
                return pipeline
            return build

        kwargs = {"context_bars": context, "account": account,
                  "spread_points": options["spread_points"], "slippage_points": options["slippage_points"],
                  "commission_points": options["commission_points"], "max_holding_bars": options["holding_bars"]}
        try:
            report = {"symbol": options["symbol"], "source": options["source"], "m15_bars": len(bars),
                      "first_bar": bars[0]["time"], "last_bar": bars[-1]["time"],
                      "context_bar_counts": {tf: len(rows) for tf, rows in context.items()},
                      "data_quality": {"input_m15_rows": len(raw_bars), "off_grid_rows_excluded": len(raw_bars)-len(bars),
                                       "policy": "EXCLUDE_OFF_GRID_NO_DATABASE_CHANGES"},
                      "validation": "DESCRIPTIVE_REPLAY_NOT_OUT_OF_SAMPLE_PROOF",
                      "baseline": replay_strategy(factory(False), bars, **kwargs),
                      "playbook": replay_strategy(factory(True), bars, **kwargs)}
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, indent=2, allow_nan=False))
