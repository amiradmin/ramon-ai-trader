"""Read-only, deterministic comparison of M15 impulse research and TREND_PULLBACK.

No EA, risk-command, or live worker modules are imported. All broker cost
parameters are explicit assumptions; this command does not open trades.
"""
import json
from datetime import date
from django.core.management.base import BaseCommand, CommandError
from ea_bridge.replay_backtest import run_backtest_data


class Command(BaseCommand):
    help = "Compare isolated IMPULSE_CONTINUATION research to TREND_PULLBACK on stored M15 bars (read only)."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--symbol", required=True)
        parser.add_argument("--start", required=True, help="YYYY-MM-DD")
        parser.add_argument("--end", required=True, help="YYYY-MM-DD")
        parser.add_argument("--balance", type=float, required=True, help="Broker-reported account balance (verify cent units)")
        parser.add_argument("--spread-points", type=float, required=True, help="Explicit modeled bid-ask spread")
        parser.add_argument("--point", type=float, required=True)
        parser.add_argument("--tick-size", type=float, required=True)
        parser.add_argument("--tick-value", type=float, required=True)
        parser.add_argument("--volume-min", type=float, required=True)
        parser.add_argument("--volume-max", type=float, required=True)
        parser.add_argument("--volume-step", type=float, required=True)
        parser.add_argument("--slippage-points", type=float, default=0)
        parser.add_argument("--commission-per-lot", type=float, default=0)

    def handle(self, *args, **opts):
        try:
            begin, end = date.fromisoformat(opts["start"]), date.fromisoformat(opts["end"])
        except ValueError as exc:
            raise CommandError("Invalid ISO start or end") from exc
        if begin > end or end > date.today():
            raise CommandError("Invalid date range")
        params = {
            "source":opts["source"], "symbol":opts["symbol"], "start":begin, "end":end,
            "balance":opts["balance"],"spread_points":opts["spread_points"],
            "point":opts["point"],"tick_size":opts["tick_size"],"tick_value":opts["tick_value"],
            "volume_min":opts["volume_min"],"volume_max":opts["volume_max"],
            "volume_step":opts["volume_step"],
            "slippage_points":opts["slippage_points"],
            "commission_per_lot":opts["commission_per_lot"],
            "risk_percent":0.5,"setups":["TREND_PULLBACK"],
            "stop_buffer_atr":0.25,"target_reward_risk":1.25,
            "minimum_opportunity_score":0.55,"minimum_entry_quality":0.55,
        }
        for k in ("balance","spread_points","point","tick_size","tick_value","volume_min","volume_max","volume_step"):
            if params[k] <= 0:
                raise CommandError(k+" must be positive")
        if params["volume_max"] < params["volume_min"]:
            raise CommandError("Invalid volume bounds")
        if min(params["slippage_points"],params["commission_per_lot"]) < 0:
            raise CommandError("Negative modeled costs are invalid")
        response = run_backtest_data(params,max_candles=16000)
        data = json.loads(response.content)
        if response.status_code != 200:
            raise CommandError(data.get("error","Research replay unsuccessful"))
        baseline = next((row for row in data["strategies"] if row["setup"]=="TREND_PULLBACK"), None)
        candidate = data.get("impulse_continuation_research")
        if not baseline or not candidate:
            raise CommandError("Missing comparison rows: ensure this research branch is deployed")
        def summary(row):
            return {k:row.get(k) for k in ("signals","closed_trades","win_rate","net_gain",
                                            "profit_factor","max_drawdown_pct","open_position")}
        output = {
            "status":"DESCRIPTIVE_RESEARCH_NOT_VALIDATED",
            "symbol":data["symbol"],"source":data["source"],
            "date_range":[data["start"],data["end"]],
            "data_quality":data["data_quality"],
            "history_fingerprint":data["data_quality"].get("history_fingerprint"),
            "assumptions":data["assumptions"],
            "trend_pullback":summary(baseline),
            "impulse_continuation":summary(candidate),
            "candidate_diagnostics":{"status":candidate.get("status"),
                "rejections":candidate.get("rejections"),
                "limitations":candidate.get("limitations")},
            "warning":"The same historical data may be in-sample; results are NOT verified profit or broker fills. Do not enable live orders.",
        }
        self.stdout.write(json.dumps(output, indent=2, sort_keys=True))
