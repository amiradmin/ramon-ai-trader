"""Authenticated telemetry ingestion for the dedicated command EA.

Telemetry is observational only and never grants trading authorization.
"""
import json
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.http import JsonResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .auth import BearerTokenRequired
from .models import AccountDailyBaseline, EAHeartbeat, MarketBar


def _body(request):
    limit = getattr(settings, "EA_MAX_BODY_BYTES", 65536)
    if len(request.body) > limit:
        raise ValueError("request body too large")
    value = json.loads(request.body or b"{}")
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _decimal(value, name):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{name} must be numeric")
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _normalize_symbol_info(value):
    """Validate broker symbol specifications before persisting them for risk."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("symbol_info must be an object")

    required = (
        "tick_size", "tick_value", "volume_min",
        "volume_max", "volume_step", "stops_level_points",
    )
    normalized = {}
    for field in required:
        if field not in value:
            raise ValueError(f"symbol_info.{field} is required")
        number = _decimal(value[field], f"symbol_info.{field}")
        if field == "stops_level_points":
            if number < 0:
                raise ValueError("symbol_info.stops_level_points must be non-negative")
        elif number <= 0:
            raise ValueError(f"symbol_info.{field} must be positive")
        normalized[field] = str(number)

    if Decimal(normalized["volume_max"]) < Decimal(normalized["volume_min"]):
        raise ValueError("symbol_info.volume_max must be >= volume_min")
    return normalized


TIMEFRAME_INTERVAL_SECONDS = {
    "M1": 60,
    "M5": 5 * 60,
    "M15": 15 * 60,
    "H1": 60 * 60,
}


def _validate_candle_timestamp(stamp, timeframe, field_name):
    """Require an exact UTC candle boundary; never round malformed timestamps."""
    interval_seconds = TIMEFRAME_INTERVAL_SECONDS[timeframe]
    epoch_seconds = int(stamp.timestamp())
    if (
        stamp.second != 0
        or stamp.microsecond != 0
        or epoch_seconds % interval_seconds != 0
    ):
        raise ValueError(
            f"{field_name} must align exactly to a {timeframe} candle boundary "
            "(zero seconds and microseconds)"
        )
    return stamp


@csrf_exempt
@require_POST
def telemetry(request):
    denied = BearerTokenRequired.reject_if_invalid(request)
    if denied:
        return denied
    try:
        p = _body(request)
        instance_id = str(p.get("instance_id", "")).strip()
        account_login = str(p.get("account_login", "")).strip()
        symbol = str(p.get("symbol", "")).strip()
        timeframe = str(p.get("timeframe", "")).strip()
        ea_version = str(p.get("ea_version", "")).strip()[:32]
        quote = p.get("quote", {})
        bars = p.get("bars", [])
        if not instance_id or len(instance_id) > 128:
            raise ValueError("valid instance_id required")
        if not account_login or len(account_login) > 32:
            raise ValueError("valid account_login required")
        if not symbol or len(symbol) > 32 or not timeframe or len(timeframe) > 16:
            raise ValueError("valid symbol and timeframe required")
        if not isinstance(quote, dict) or not isinstance(bars, list) or len(bars) > 512:
            raise ValueError("quote object and at most 512 bars required")
        bid = _decimal(quote.get("bid"), "quote.bid")
        ask = _decimal(quote.get("ask"), "quote.ask")
        point = _decimal(quote.get("point"), "quote.point")
        if bid <= 0 or ask < bid or point <= 0:
            raise ValueError("invalid quote values")
        # New command EAs send one independent set per timeframe. Older EAs
        # remain compatible through the original top-level timeframe/bars fields.
        bar_sets = p.get("bar_sets")
        if bar_sets is None:
            bar_sets = [{"timeframe": timeframe, "bars": bars}]
        if not isinstance(bar_sets, list) or len(bar_sets) > 4:
            raise ValueError("bar_sets must contain at most four timeframe objects")

        timeframe_seconds = {
            "M1": 60, "M5": 300, "M15": 900, "H1": 3600,
        }
        normalized_bars = []
        seen_bars = {}
        total_bars = 0
        seen_timeframes = set()
        for set_index, bar_set in enumerate(bar_sets):
            if not isinstance(bar_set, dict):
                raise ValueError(f"bar_sets[{set_index}] must be an object")
            bar_timeframe = str(bar_set.get("timeframe", "")).strip().upper()
            if bar_timeframe not in timeframe_seconds:
                raise ValueError(f"bar_sets[{set_index}].timeframe must be M1, M5, M15 or H1")
            if bar_timeframe in seen_timeframes:
                raise ValueError(f"duplicate bar timeframe: {bar_timeframe}")
            seen_timeframes.add(bar_timeframe)
            set_bars = bar_set.get("bars", [])
            if not isinstance(set_bars, list) or len(set_bars) > 512:
                raise ValueError(f"bar_sets[{set_index}].bars must contain at most 512 candles")
            total_bars += len(set_bars)
            if total_bars > 2048:
                raise ValueError("at most 2048 candles may be ingested per telemetry request")

            for index, bar in enumerate(set_bars):
                field = f"bar_sets[{set_index}].bars[{index}]"
                if not isinstance(bar, dict) or bar.get("closed") is not True:
                    raise ValueError(f"{field} must be a completed candle")
                stamp = parse_datetime(str(bar.get("timestamp", "")))
                if stamp is None or stamp.tzinfo is None:
                    raise ValueError(f"{field}.timestamp must include timezone")
                stamp = stamp.astimezone(dt_timezone.utc)
                stamp = _validate_candle_timestamp(
                    stamp, bar_timeframe, f"{field}.timestamp"
                )

                o = _decimal(bar.get("open"), f"{field}.open")
                h = _decimal(bar.get("high"), f"{field}.high")
                lo = _decimal(bar.get("low"), f"{field}.low")
                c = _decimal(bar.get("close"), f"{field}.close")
                vol = _decimal(bar.get("volume", 0), f"{field}.volume")
                if min(o, h, lo, c) <= 0 or vol < 0 or h < max(o, c, lo) or lo > min(o, c, h):
                    raise ValueError(f"{field} has invalid OHLCV values")

                key = (bar_timeframe, stamp)
                values = (o, h, lo, c, vol)
                previous_values = seen_bars.get(key)
                if previous_values is not None and previous_values != values:
                    raise ValueError(
                        f"{field} conflicts with another candle at "
                        f"{bar_timeframe} timestamp {stamp.isoformat()}"
                    )
                seen_bars[key] = values

        normalized_bars = [
            (bar_timeframe, stamp, *values)
            for (bar_timeframe, stamp), values in sorted(
                seen_bars.items(), key=lambda item: (item[0][0], item[0][1])
            )
        ]
        symbol_info = _normalize_symbol_info(p.get("symbol_info"))
        account = p.get("account", {})
        positions = p.get("positions", [])
        if not isinstance(account, dict) or not isinstance(positions, list) or len(positions) > 500:
            raise ValueError("account object and positions array required")
        server_name = str(p.get("server_name", "")).strip()[:128]
        account_trade_mode_raw = p.get("account_trade_mode")
        account_trade_mode = None
        if account_trade_mode_raw is not None:
            if type(account_trade_mode_raw) is not int or account_trade_mode_raw not in (0, 1, 2):
                raise ValueError("account_trade_mode must be integer 0 (demo), 1 (contest), or 2 (real)")
            account_trade_mode = account_trade_mode_raw
        broker_day = None
        broker_day_raw = p.get("broker_day")
        if broker_day_raw not in (None, ""):
            if not isinstance(broker_day_raw, str):
                raise ValueError("broker_day must be YYYY-MM-DD")
            try:
                broker_day = date.fromisoformat(broker_day_raw)
            except ValueError:
                raise ValueError("broker_day must be YYYY-MM-DD") from None
            if broker_day.isoformat() != broker_day_raw:
                raise ValueError("broker_day must be YYYY-MM-DD")
        account_balance = None
        if broker_day is not None and server_name:
            account_balance = _decimal(account.get("balance"), "account.balance")
            if account_balance <= 0:
                raise ValueError("account.balance must be positive for daily risk tracking")

        broker_day_history_complete = p.get("broker_day_history_complete") is True
        broker_day_opening_balance = None
        broker_day_deal_count = None
        if broker_day_history_complete:
            if broker_day is None or not server_name:
                raise ValueError("complete broker-day history requires broker_day and server_name")
            broker_day_opening_balance = _decimal(
                p.get("broker_day_opening_balance"), "broker_day_opening_balance"
            )
            broker_day_deal_count = p.get("broker_day_deal_count")
            if broker_day_opening_balance <= 0:
                raise ValueError("broker_day_opening_balance must be positive")
            if type(broker_day_deal_count) is not int or broker_day_deal_count < 0:
                raise ValueError("broker_day_deal_count must be a non-negative integer")
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    with transaction.atomic():
        previous = EAHeartbeat.objects.select_for_update().filter(instance_id=instance_id).first()

        # Complete broker-side deal history can bootstrap the current day even when
        # this EA first connects after midnight. The record remains immutable and is
        # scoped to the exact login/server/day. Never substitute current balance.
        baseline_key = {
            "account_login": account_login,
            "server_name": server_name,
            "broker_day": broker_day,
        }
        if broker_day_history_complete:
            AccountDailyBaseline.objects.get_or_create(
                **baseline_key,
                defaults={
                    "opening_balance": broker_day_opening_balance,
                    "source_instance_id": instance_id,
                    "capture_method": "BROKER_HISTORY_DERIVED",
                },
            )
        # Backward-compatible fallback: only a fresh, continuous rollover observation
        # can establish the opening balance when broker history is unavailable.
        elif previous is not None and broker_day is not None and server_name and account_balance is not None:
            previous_payload = previous.payload if isinstance(previous.payload, dict) else {}
            previous_account = previous_payload.get("account", {})
            previous_day_raw = previous_payload.get("broker_day")
            previous_server = str(previous_payload.get("server_name", "")).strip()
            gap = (timezone.now() - previous.last_seen).total_seconds()
            try:
                previous_day = date.fromisoformat(previous_day_raw) if isinstance(previous_day_raw, str) else None
                previous_balance = _decimal(previous_account.get("balance"), "previous account.balance") if isinstance(previous_account, dict) else Decimal("0")
            except (ValueError, TypeError):
                previous_day, previous_balance = None, Decimal("0")
            if (
                previous.account_login == account_login
                and previous.server_name == server_name
                and previous_server == server_name
                and previous_day is not None
                and broker_day == previous_day + timedelta(days=1)
                and 0 <= gap <= 90
                and previous_balance > 0
            ):
                AccountDailyBaseline.objects.get_or_create(
                    **baseline_key,
                    defaults={
                        "opening_balance": previous_balance,
                        "source_instance_id": instance_id,
                        "capture_method": "ROLLOVER_CONTINUITY",
                    },
                )

        bar_groups = {}
        for bar_timeframe, stamp, o, h, lo, c, vol in normalized_bars:
            bar_groups.setdefault(bar_timeframe, []).append((stamp, o, h, lo, c, vol))

        timeframe_ingestion = []
        for bar_timeframe, rows in bar_groups.items():
            query = MarketBar.objects.filter(
                source=instance_id, symbol=symbol, timeframe=bar_timeframe,
            )
            previous_latest = query.order_by("-timestamp").values_list("timestamp", flat=True).first()
            candidate_times = [row[0] for row in rows]
            existing_times = set(query.filter(timestamp__in=candidate_times).values_list("timestamp", flat=True))
            new_rows = [row for row in rows if row[0] not in existing_times]
            late_arrivals = sum(
                1 for row in new_rows
                if previous_latest is not None and row[0] < previous_latest
            )
            MarketBar.objects.bulk_create(
                [
                    MarketBar(
                        source=instance_id, symbol=symbol, timeframe=bar_timeframe,
                        timestamp=stamp, open=o, high=h, low=lo, close=c, volume=vol,
                    )
                    for stamp, o, h, lo, c, vol in new_rows
                ],
                ignore_conflicts=True,
                batch_size=500,
            )
            latest = MarketBar.objects.filter(
                source=instance_id, symbol=symbol, timeframe=bar_timeframe,
            ).order_by("-timestamp").values_list("timestamp", flat=True).first()
            latest_received = MarketBar.objects.filter(
                source=instance_id, symbol=symbol, timeframe=bar_timeframe,
            ).order_by("-received_at").values_list("received_at", flat=True).first()
            timeframe_ingestion.append({
                "timeframe": bar_timeframe,
                "received_bars": len(rows),
                "inserted_bars": len(new_rows),
                "late_arrivals": late_arrivals,
                "last_successful_candle_utc": latest.isoformat() if latest else None,
                "last_received_at_utc": latest_received.isoformat() if latest_received else None,
            })

        from .reliability import record_telemetry_gap
        record_telemetry_gap(previous, account_login, server_name, p.get("quote_time"))
        EAHeartbeat.objects.update_or_create(
            instance_id=instance_id,
            defaults={
                "account_login": account_login,
                "server_name": server_name,
                "symbol": symbol,
                "ea_version": ea_version,
                "status": "TELEMETRY_OK",
                "terminal_connected": True,
                "trade_allowed": bool(p.get("terminal_trade_allowed", False)),
                "payload": {
                    "protocol_version": p.get("protocol_version", "ramon-ea-command-v1"),
                    "ea_version": ea_version,
                    "quote": {"bid": str(bid), "ask": str(ask), "point": str(point)},
                    "quote_time": p.get("quote_time"),
                    "server_name": server_name,
                    "account_trade_mode": account_trade_mode,
                    "account_margin_mode": p.get("account_margin_mode"),
                    "execution_profile": p.get("execution_profile"),
                    "broker_day": broker_day.isoformat() if broker_day else None,
                    "broker_day_history_complete": broker_day_history_complete,
                    "broker_day_opening_balance": (
                        str(broker_day_opening_balance) if broker_day_opening_balance is not None else None
                    ),
                    "broker_day_deal_count": broker_day_deal_count,
                    "broker_time": p.get("broker_time"),
                    "account": account,
                    "symbol_info": symbol_info,
                    "positions": positions,
                    "last_command_id": str(p.get("last_command_id", ""))[:128],
                },
            },
        )
    return JsonResponse({
        "ok": True,
        "stored_bars": len(normalized_bars),
        "inserted_bars": sum(item["inserted_bars"] for item in timeframe_ingestion),
        "timeframes": timeframe_ingestion,
        "telemetry_only": True,
        "trading_authorization": False,
    })



@require_GET
def market_data_status(request):
    """Read-only coverage and freshness report for persisted market candles."""
    denied = BearerTokenRequired.reject_if_invalid(request)
    if denied:
        return denied

    symbol = str(request.GET.get("symbol", "XAUUSD_o")).strip()[:32]
    source = str(request.GET.get("source", "")).strip()[:128]
    if not symbol:
        return JsonResponse({"ok": False, "error": "symbol is required"}, status=400)

    sources = list(
        MarketBar.objects.filter(symbol=symbol)
        .exclude(source="")
        .values_list("source", flat=True).distinct().order_by("source")
    )
    if source:
        sources = [item for item in sources if item == source]
        if not sources:
            sources = [source]

    intervals = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}
    reports = []
    for source_name in sources:
        for bar_timeframe, interval in intervals.items():
            query = MarketBar.objects.filter(
                source=source_name, symbol=symbol, timeframe=bar_timeframe,
            )
            count = query.count()
            latest_row = query.order_by("-timestamp").only("timestamp").first()
            last_received_at = query.order_by("-received_at").values_list("received_at", flat=True).first()
            oldest = query.order_by("timestamp").values_list("timestamp", flat=True).first()
            recent_times = list(
                query.order_by("-timestamp").values_list("timestamp", flat=True)[:500]
            )
            recent_times.reverse()
            potential_gaps = []
            for previous, current in zip(recent_times, recent_times[1:]):
                gap_seconds = int((current - previous).total_seconds())
                # Only same-weekday, same-date gaps are surfaced. Overnight,
                # weekend and broker-session closures are not called missing data.
                if (
                    gap_seconds > interval * 1.5
                    and previous.date() == current.date()
                    and previous.weekday() < 5
                    and current.weekday() < 5
                ):
                    potential_gaps.append({
                        "from_utc": previous.isoformat(),
                        "to_utc": current.isoformat(),
                        "missing_interval_seconds": gap_seconds,
                    })
            reports.append({
                "source": source_name,
                "symbol": symbol,
                "timeframe": bar_timeframe,
                "candle_count": count,
                "oldest_candle_utc": oldest.isoformat() if oldest else None,
                "last_successful_candle_utc": (
                    latest_row.timestamp.isoformat() if latest_row else None
                ),
                "last_received_at_utc": (
                    last_received_at.isoformat() if last_received_at else None
                ),
                "potential_intraday_gaps": potential_gaps,
                "status": "COLLECTING" if count else "NO_DATA",
            })

    return JsonResponse({
        "ok": True,
        "symbol": symbol,
        "timeframes": reports,
        "note": (
            "Gap entries are candidates for review, not definitive missing-candle "
            "alerts; broker maintenance and instrument-specific sessions must be considered."
        ),
    })
