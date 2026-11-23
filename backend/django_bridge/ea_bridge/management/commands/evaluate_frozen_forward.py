"""Run the frozen Trend Pullback research configuration on later M15 candles only."""
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ea_bridge.models import MarketBar
from ea_bridge.replay_backtest import run_backtest_data


PRESET = Path(__file__).resolve().parents[2] / "research_presets" / "frozen-trend-pullback-forward-2026-11-23.json"


class Command(BaseCommand):
    help = "Read-only forward research on M15 bars strictly after the frozen development period."

    def add_arguments(self, parser):
        parser.add_argument("--min-bars", type=int, default=1000,
                            help="Minimum new M15 candles before reporting (default: 1000).")

    def handle(self, *args, **options):
        if options["min_bars"] < 130 or options["min_bars"] > 15000:
            raise CommandError("--min-bars must be between 130 and 15000")
        preset = json.loads(PRESET.read_text(encoding="utf-8"))
        cutoff = date.fromisoformat(preset["development_period_end_utc"])
        start = cutoff + timedelta(days=1)
        inputs = preset["inputs"]
        scope = dict(source=inputs["source"], symbol=inputs["symbol"], timeframe="M15")
        # Exclude the current UTC day so the test always ends on a completed day.
        latest = MarketBar.objects.filter(**scope, timestamp__gte=datetime.combine(
            start, time.min, tzinfo=timezone.utc), timestamp__lt=datetime.combine(
            datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)).order_by("-timestamp").first()
        if latest is None:
            self.stdout.write("WAITING: no new complete-day M15 candles after " + cutoff.isoformat()
                              + " in source " + inputs["source"] + ". Import a new MT5 M1 export first.")
            return
        end = latest.timestamp.date()
        count = MarketBar.objects.filter(**scope, timestamp__gte=datetime.combine(
            start, time.min, tzinfo=timezone.utc), timestamp__lt=datetime.combine(
            end + timedelta(days=1), time.min, tzinfo=timezone.utc)).count()
        if count < options["min_bars"]:
            self.stdout.write(f"WAITING: {count} new M15 candles from {start} through {end}; "
                              f"need {options['min_bars']}. No parameters changed.")
            return
        if count + 256 > 16000:
            raise CommandError("Forward window exceeds 15,744 candles; archive the fixed period and run a bounded segment.")
        result = run_backtest_data({**inputs, "start": start, "end": end}, max_candles=16000)
        payload = json.loads(result.content)
        if result.status_code != 200:
            raise CommandError(payload.get("error", "Forward research failed"))
        row = payload["strategies"][0]
        self.stdout.write(json.dumps({
            "status": "DESCRIPTIVE_FORWARD_RESEARCH_ONLY",
            "frozen_at_utc": preset["frozen_at_utc"],
            "start": payload["start"], "end": payload["end"],
            "preset": str(PRESET.name), "source": inputs["source"],
            "candles_in_new_window": count,
            "history_fingerprint": payload["data_quality"]["history_fingerprint"],
            "data_quality": payload["data_quality"]["validation_status"],
            "gaps": payload["data_quality"]["gap_count"],
            "trades_crossing_gaps": row["trade_diagnostics"]["gap_affected_trades"],
            "closed_trades": row["closed_trades"], "wins": row["wins"],
            "win_rate": row["win_rate"], "net_gain": row["net_gain"],
            "max_drawdown_pct": row["max_drawdown_pct"],
            "limitations": payload["limitations"],
        }, indent=2))
