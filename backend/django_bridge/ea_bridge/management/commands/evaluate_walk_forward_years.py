"""Read-only calendar-year holdout evaluation of a single frozen Ramon policy.

No model fitting, parameter search, DB writes or trading commands. Each year is
an independent holdout fold with preceding completed candles for warm-up.
"""
from ea_bridge.account_scope import real_cent_scope

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from django.core.management.base import BaseCommand, CommandError
from django.test import override_settings
from ea_bridge.decision import _build_pipeline
from ea_bridge.models import EAHeartbeat, MarketBar
from ea_bridge.policy_snapshot import effective_profiles, fingerprint
from ea_bridge.walk_forward_stats import grouped_summary
from ramon_pipeline.signalflow.replay import replay_strategy

PERIODS = {"M15": 900, "M5": 300, "H1": 3600}


def yearly_coverage_issues(series, beginning, end):
    """Identify obvious truncation; never certify coverage without a market calendar."""
    issues = []
    tolerance = timedelta(days=7).total_seconds()
    start, stop = beginning.timestamp(), end.timestamp()
    for timeframe in ("M15", "M5", "H1"):
        timestamps = [bar["time"] for bar in series[timeframe] if start <= bar["time"] < stop]
        if not timestamps:
            issues.append(f"{timeframe}:NO_YEAR_DATA")
        else:
            if timestamps[0] - start > tolerance:
                issues.append(f"{timeframe}:LATE_START")
            if stop - timestamps[-1] > tolerance:
                issues.append(f"{timeframe}:EARLY_END")
    return issues


def read_bars(*, source, symbol, timeframe, start, end, maximum, allow_off_grid=False):
    rows = MarketBar.objects.filter(
        source=source, symbol=symbol, timeframe=timeframe,
        timestamp__gte=start, timestamp__lt=end,
    ).order_by("timestamp")
    candles = []
    digest = hashlib.sha256()
    previous = None
    quality = {"input_rows": 0, "accepted_rows": 0, "off_grid_excluded": 0,
               "first_off_grid": None, "offset_seconds_counts": {}}
    for row in rows.iterator(chunk_size=5000):
        ts = row.timestamp
        stamp = ts.timestamp()
        quality["input_rows"] += 1
        if ts.microsecond or int(stamp) % PERIODS[timeframe] or stamp <= 0:
            if not allow_off_grid:
                raise CommandError(
                    f"OFF_GRID_{timeframe}: {ts.isoformat()} - repair/reimport source, "
                    "do not round. For a descriptive-only report pass --allow-off-grid "
                    "to exclude and count invalid candles without changing the database.")
            quality["off_grid_excluded"] += 1
            if quality["first_off_grid"] is None:
                quality["first_off_grid"] = ts.isoformat()
            offset = round(stamp % PERIODS[timeframe], 6)
            offset_label = str(offset)
            quality["offset_seconds_counts"][offset_label] = (
                quality["offset_seconds_counts"].get(offset_label, 0) + 1)
            continue
        if previous is not None and stamp <= previous:
            raise CommandError(f"NON_MONOTONIC_{timeframe}: {ts.isoformat()}")
        previous = stamp
        values = [float(getattr(row, key)) for key in ("open", "high", "low", "close")]
        o, h, l, c = values
        if (not all(math.isfinite(x) and x > 0 for x in values)
                or l > min(o, c) or h < max(o, c) or h < l):
            raise CommandError(f"INVALID_OHLC_{timeframe}: {ts.isoformat()}")
        if len(candles) >= maximum:
            raise CommandError(f"{timeframe} exceeds --max-bars-per-timeframe ({maximum})")
        bar = {"time": stamp, "open": o, "high": h, "low": l, "close": c, "closed": True}
        candles.append(bar)
        quality["accepted_rows"] += 1
        digest.update(json.dumps([ts.isoformat(), *map(str, (row.open, row.high, row.low, row.close))],
                                 separators=(",", ":")).encode("utf-8") + b"\n")
    return candles, digest.hexdigest(), quality


def broker_contract(source, symbol, balance):
    heartbeat = EAHeartbeat.objects.filter(instance_id=source, symbol=symbol).first()
    payload = heartbeat.payload if heartbeat and isinstance(heartbeat.payload, dict) else {}
    if not real_cent_scope(heartbeat)[0]:
        raise CommandError("A matching configured real cent EA heartbeat is required for symbol metadata.")
    try:
        quote, info = payload["quote"], payload["symbol_info"]
        point = float(quote["point"])
        tick_size = float(info["tick_size"])
        contract = {
            "symbol": symbol, "account_balance": balance, "point": point,
            "point_value_per_lot": float(info["tick_value"]) * point / tick_size,
            "volume_step": float(info["volume_step"]),
            "min_volume": float(info["volume_min"]),
            "max_volume": float(info["volume_max"]),
            "stop_level_points": float(info["stops_level_points"]),
        }
        if not all(math.isfinite(v) and v > 0 for k, v in contract.items()
                   if k not in ("symbol", "stop_level_points")):
            raise ValueError("missing or invalid positive metadata")
        if not math.isfinite(contract["stop_level_points"]) or contract["stop_level_points"] < 0:
            raise ValueError("invalid broker stop distance")
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise CommandError(f"Invalid real cent broker symbol metadata: {exc}") from exc
    return contract


class Command(BaseCommand):
    help = "Read-only, frozen-policy, non-overlapping yearly holdout replays of persisted XAUUSD bars."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--allow-off-grid", action="store_true",
                            help="Descriptive only: skip/count legacy off-grid history; never repair timestamps.")
        parser.add_argument("--symbol", default="XAUUSD_l")
        parser.add_argument("--first-year", type=int, default=2022)
        parser.add_argument("--last-year", type=int, default=2026)
        parser.add_argument("--spread-points", type=float, required=True)
        parser.add_argument("--slippage-points", type=float, required=True)
        parser.add_argument("--commission-points", type=float, required=True)
        parser.add_argument("--holding-bars", type=int, default=24)
        parser.add_argument("--balance", type=float, default=10000)
        parser.add_argument("--max-bars-per-timeframe", type=int, default=200000)
        parser.add_argument("--warmup-bars", type=int, default=500)

    def handle(self, *args, **opts):
        first, last = opts["first_year"], opts["last_year"]
        if not 2000 <= first <= last <= 2100 or last - first > 20:
            raise CommandError("Invalid year range (maximum 21 years).")
        if opts["warmup_bars"] < 128 or opts["warmup_bars"] > 2000:
            raise CommandError("--warmup-bars must be 128..2000")
        if opts["max_bars_per_timeframe"] < 2000:
            raise CommandError("--max-bars-per-timeframe must be >= 2000")
        costs = [opts[k] for k in ("spread_points", "slippage_points", "commission_points")]
        if not all(math.isfinite(v) and v >= 0 for v in costs):
            raise CommandError("Costs must be finite, non-negative and explicitly supplied")
        if not math.isfinite(opts["balance"]) or opts["balance"] <= 0:
            raise CommandError("--balance must be positive")
        account = broker_contract(opts["source"], opts["symbol"], opts["balance"])
        # Standard policy is frozen for the whole report, including settings
        # previously overridden in the database.
        with override_settings(RAMON_DEMO_PROFILE="standard"):
            profiles = effective_profiles()
            policy_hash = fingerprint(profiles)

        def factory():
            with override_settings(RAMON_DEMO_PROFILE="standard"):
                return _build_pipeline(demo_mode=False)

        folds = []
        total_excluded = 0
        for year in range(first, last + 1):
            beginning = datetime(year, 1, 1, tzinfo=timezone.utc)
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            # Include preceding M15 completed bars; never include future bars.
            warmup_start = beginning - timedelta(seconds=opts["warmup_bars"] * 900 * 4)
            bars, m15_sha, m15_quality = read_bars(
                source=opts["source"], symbol=opts["symbol"], timeframe="M15",
                start=warmup_start, end=end, maximum=opts["max_bars_per_timeframe"],
                allow_off_grid=opts["allow_off_grid"])
            context = {}
            hashes = {"M15": m15_sha}
            quality = {"M15": m15_quality}
            for tf in ("M5", "H1"):
                context[tf], hashes[tf], quality[tf] = read_bars(
                    source=opts["source"], symbol=opts["symbol"], timeframe=tf,
                    start=warmup_start, end=end, maximum=opts["max_bars_per_timeframe"],
                    allow_off_grid=opts["allow_off_grid"])
            fold_excluded = sum(q["off_grid_excluded"] for q in quality.values())
            total_excluded += fold_excluded
            train_count = sum(bar["time"] < beginning.timestamp() for bar in bars)
            evaluated_count = sum(beginning.timestamp() <= bar["time"] < end.timestamp() for bar in bars)
            coverage_issues = yearly_coverage_issues({"M15": bars, **context}, beginning, end)
            if train_count < 128 or evaluated_count < 2 or coverage_issues:
                folds.append({"year": year, "status": "INSUFFICIENT_HISTORY",
                              "prior_warmup_m15": train_count, "holdout_m15": evaluated_count,
                              "coverage_issues": coverage_issues,
                              "input_sha256": hashes, "data_quality": quality})
                continue
            try:
                result = replay_strategy(
                    factory, bars, context_bars=context, account=account,
                    spread_points=opts["spread_points"],
                    slippage_points=opts["slippage_points"],
                    commission_points=opts["commission_points"],
                    max_holding_bars=opts["holding_bars"],
                    evaluation_start_time=beginning.timestamp(),
                    evaluation_end_time=end.timestamp(),
                    warmup_bars=128)
            except (KeyError, TypeError, ValueError) as exc:
                raise CommandError(f"Year {year}: {exc}") from exc
            folds.append({
                "year": year, "status": ("DESCRIPTIVE_FILTERED_OFF_GRID_NOT_VALIDATED" if fold_excluded
                                          else "DESCRIPTIVE_HOLDOUT_NO_FITTING"),
                "prior_warmup_m15": train_count, "holdout_m15": evaluated_count,
                "context_counts": {tf: len(v) for tf, v in context.items()},
                "input_sha256": hashes, "data_quality": quality, "result": result,
                "descriptive_performance": grouped_summary(result["trades"])})
        result = {
            "validation": ("DESCRIPTIVE_FILTERED_HISTORY_NOT_FOR_STRATEGY_VALIDATION"
                           if total_excluded else "CALENDAR_YEAR_HOLDOUT_FIXED_POLICY_NOT_PROOF_OF_OUT_OF_SAMPLE_EDGE"),
            "data_quality": {"off_grid_excluded_across_folds": total_excluded,
                             "includes_overlapping_warmup_rows": True,
                             "session_calendar_coverage_verified": False,
                             "allow_off_grid": opts["allow_off_grid"]},
            "execution": "READ_ONLY_NO_COMMANDS", "profile": "standard",
            "source": opts["source"], "symbol": opts["symbol"],
            "policy_sha256": policy_hash, "profiles": profiles,
            "costs": {"spread_points": costs[0], "slippage_points_each_side": costs[1],
                      "commission_points_round_trip": costs[2]},
            "folds": folds,
            "limitations": [
                "No training or tuning happens here; if strategy thresholds were selected using these years, these are NOT independent out-of-sample results.",
                "Costs are fixed assumptions, not broker historical quotes; OHLC does not reconstruct ticks.",
                "Yearly starting balance resets; results cannot be concatenated into a real equity curve.",
                "Previous-year candles only warm up indicators; year-end exits are bounded to holdout candles.",
                "Session and per-setup inference requires further independent validation.",
                "Filtered off-grid rows can break candle continuity; filtered results are only diagnostic and cannot validate strategy eligibility.",
                "A seven-day year-boundary check detects obvious truncation only; internal gaps, broker sessions, source provenance and completeness remain unverified.",
            ],
        }
        self.stdout.write(json.dumps(result, indent=2, allow_nan=False))
