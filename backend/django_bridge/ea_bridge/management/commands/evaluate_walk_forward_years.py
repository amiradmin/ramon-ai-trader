"""Read-only calendar-year holdout evaluation of a single frozen Ramon policy.

No model fitting, parameter search, DB writes or trading commands. Each year is
an independent holdout fold with preceding completed candles for warm-up.
"""
import hashlib
import json
import math
from datetime import datetime, timezone
from django.core.management.base import BaseCommand, CommandError
from django.test import override_settings
from ea_bridge.decision import _build_pipeline
from ea_bridge.models import EAHeartbeat, MarketBar
from ea_bridge.policy_snapshot import effective_profiles, fingerprint
from ramon_pipeline.signalflow.replay import replay_strategy

PERIODS = {"M15": 900, "M5": 300, "H1": 3600}


def read_bars(*, source, symbol, timeframe, start, end, maximum):
    rows = MarketBar.objects.filter(
        source=source, symbol=symbol, timeframe=timeframe,
        timestamp__gte=start, timestamp__lt=end,
    ).order_by("timestamp")
    candles = []
    digest = hashlib.sha256()
    previous = None
    for row in rows.iterator(chunk_size=5000):
        ts = row.timestamp
        stamp = ts.timestamp()
        if ts.microsecond or int(stamp) % PERIODS[timeframe] or stamp <= 0:
            raise CommandError(f"OFF_GRID_{timeframe}: {ts.isoformat()} - repair/reimport source, do not round")
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
        digest.update(json.dumps([ts.isoformat(), *map(str, (row.open, row.high, row.low, row.close))],
                                 separators=(",", ":")).encode("utf-8") + b"\n")
    return candles, digest.hexdigest()


def broker_contract(source, symbol, balance):
    heartbeat = EAHeartbeat.objects.filter(instance_id=source, symbol=symbol).first()
    payload = heartbeat.payload if heartbeat and isinstance(heartbeat.payload, dict) else {}
    if type(payload.get("account_trade_mode")) is not int or payload["account_trade_mode"] != 0:
        raise CommandError("A matching explicitly demo EA heartbeat is required for symbol metadata.")
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
        raise CommandError(f"Invalid demo broker symbol metadata: {exc}") from exc
    return contract


class Command(BaseCommand):
    help = "Read-only, frozen-policy, non-overlapping yearly holdout replays of persisted XAUUSD bars."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--symbol", default="XAUUSD_o")
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
        for year in range(first, last + 1):
            beginning = datetime(year, 1, 1, tzinfo=timezone.utc)
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            # Include preceding M15 completed bars; never include future bars.
            from datetime import timedelta
            warmup_start = beginning - timedelta(seconds=opts["warmup_bars"] * 900 * 4)
            bars, m15_sha = read_bars(
                source=opts["source"], symbol=opts["symbol"], timeframe="M15",
                start=warmup_start, end=end, maximum=opts["max_bars_per_timeframe"])
            context = {}
            hashes = {"M15": m15_sha}
            for tf in ("M5", "H1"):
                context[tf], hashes[tf] = read_bars(
                    source=opts["source"], symbol=opts["symbol"], timeframe=tf,
                    start=warmup_start, end=end, maximum=opts["max_bars_per_timeframe"])
            train_count = sum(bar["time"] < beginning.timestamp() for bar in bars)
            evaluated_count = sum(beginning.timestamp() <= bar["time"] < end.timestamp() for bar in bars)
            if train_count < 128 or evaluated_count < 2:
                folds.append({"year": year, "status": "INSUFFICIENT_HISTORY",
                              "prior_warmup_m15": train_count, "holdout_m15": evaluated_count,
                              "input_sha256": hashes})
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
                "year": year, "status": "DESCRIPTIVE_HOLDOUT_NO_FITTING",
                "prior_warmup_m15": train_count, "holdout_m15": evaluated_count,
                "context_counts": {tf: len(v) for tf, v in context.items()},
                "input_sha256": hashes, "result": result})
        result = {
            "validation": "CALENDAR_YEAR_HOLDOUT_FIXED_POLICY_NOT_PROOF_OF_OUT_OF_SAMPLE_EDGE",
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
            ],
        }
        self.stdout.write(json.dumps(result, indent=2, allow_nan=False))
