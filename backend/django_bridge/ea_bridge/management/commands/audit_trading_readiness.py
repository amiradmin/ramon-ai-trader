"""Read-only checks for the Django SignalFlow runtime and stored market bars."""
import json
from collections import Counter
from datetime import timezone

from django.core.management.base import BaseCommand, CommandError

from ea_bridge.decision import _build_pipeline
from ea_bridge.models import MarketBar

SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}


class Command(BaseCommand):
    help = "Audit strategy configuration and candle alignment; never mutate bars or authorize trades."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--symbol", default="XAUUSD_o")
        parser.add_argument("--limit", type=int, default=20000)
        parser.add_argument("--strict", action="store_true", help="Exit nonzero for off-grid rows or unsafe risk.")

    def handle(self, *args, **opts):
        if not 1 <= opts["limit"] <= 500000:
            raise CommandError("--limit must be between 1 and 500000")
        profiles = {}
        for name, demo_mode in (("standard", False), ("active_demo", True)):
            pipeline = _build_pipeline(demo_mode=demo_mode)
            risk = pipeline.risk_manager
            profiles[name] = {
                "maximum_risk_percent": float(risk.maximum_risk_percent),
                "strict_structural": bool(risk.config.strict_structural),
                "short_stop_spread_adjust": bool(risk.config.short_stop_spread_adjust),
                "stop_buffer_atr": float(risk.config.stop_buffer_atr),
                "min_stop_atr": float(risk.config.min_stop_atr),
                "max_stop_atr": float(risk.config.max_stop_atr),
                "maximum_spread_points": float(pipeline.entry_timer.maximum_spread_points),
                "minimum_entry_quality": float(pipeline.entry_timer.minimum_quality),
                "minimum_opportunity_score": float(pipeline.opportunities.minimum_score),
            }
        queryset = MarketBar.objects.filter(source=opts["source"], symbol=opts["symbol"])
        rows = list(queryset.order_by("-timestamp").values_list("timeframe", "timestamp")[:opts["limit"]])
        metrics = {}
        observed = {}
        for tf, stamp in rows:
            bucket = metrics.setdefault(tf, {"rows": 0, "off_grid": 0, "duplicates": 0,
                                             "first_utc": None, "last_utc": None})
            bucket["rows"] += 1
            stamp = stamp.astimezone(timezone.utc)
            epoch = int(stamp.timestamp())
            if bucket["last_utc"] is None:
                bucket["last_utc"] = stamp.isoformat()
            bucket["first_utc"] = stamp.isoformat()
            period = SECONDS.get(tf)
            if period is None or stamp.microsecond or epoch % period:
                bucket["off_grid"] += 1
            key = (tf, epoch, stamp.microsecond)
            if key in observed:
                bucket["duplicates"] += 1
            observed[key] = True
        issues = [f"{tf}: {row['off_grid']} off-grid, {row['duplicates']} duplicated"
                  for tf, row in metrics.items() if row["off_grid"] or row["duplicates"]]
        for name, profile in profiles.items():
            if profile["maximum_risk_percent"] > 0.5 or profile["maximum_risk_percent"] <= 0:
                issues.append(f"{name}: risk ceiling outside (0, 0.5]")
        report = {
            "mode": "READ_ONLY", "source": opts["source"], "symbol": opts["symbol"],
            "profiles": profiles, "market_bars": metrics, "rows_scanned": len(rows),
            "issues": issues,
            "note": "Profiles show runtime defaults and persisted overrides, not broker terminal settings. "
                    "Off-grid bars are not repaired or assumed to be aligned.",
        }
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        if issues and opts["strict"]:
            raise CommandError("Trading readiness audit detected issues")
