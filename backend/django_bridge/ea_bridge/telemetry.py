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
from django.views.decorators.http import require_POST

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


M15_INTERVAL_SECONDS = 15 * 60
M15_TIMESTAMP_TOLERANCE_SECONDS = 120


def _normalize_m15_timestamp(stamp, field_name):
    """Canonicalize small timestamp jitter without accepting a wrong candle."""
    epoch = stamp.timestamp()
    boundary_epoch = int((epoch + M15_INTERVAL_SECONDS / 2) // M15_INTERVAL_SECONDS) * M15_INTERVAL_SECONDS
    drift_seconds = abs(epoch - boundary_epoch)
    if drift_seconds > M15_TIMESTAMP_TOLERANCE_SECONDS:
        raise ValueError(
            f"{field_name} must be within "
            f"{M15_TIMESTAMP_TOLERANCE_SECONDS} seconds of an M15 boundary"
        )
    return datetime.fromtimestamp(boundary_epoch, tz=dt_timezone.utc)


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
        normalized_bars = []
        m15_bars_by_timestamp = {}
        is_m15 = timeframe.upper() == "M15"
        for index, bar in enumerate(bars):
            if not isinstance(bar, dict) or bar.get("closed") is not True:
                raise ValueError(f"bars[{index}] must be a completed candle")
            stamp = parse_datetime(str(bar.get("timestamp", "")))
            if stamp is None or stamp.tzinfo is None:
                raise ValueError(f"bars[{index}].timestamp must include timezone")
            o = _decimal(bar.get("open"), f"bars[{index}].open")
            h = _decimal(bar.get("high"), f"bars[{index}].high")
            lo = _decimal(bar.get("low"), f"bars[{index}].low")
            c = _decimal(bar.get("close"), f"bars[{index}].close")
            vol = _decimal(bar.get("volume", 0), f"bars[{index}].volume")
            if min(o, h, lo, c) <= 0 or vol < 0 or h < max(o, c, lo) or lo > min(o, c, h):
                raise ValueError(f"bars[{index}] has invalid OHLCV values")
            values = (o, h, lo, c, vol)
            if is_m15:
                stamp = _normalize_m15_timestamp(stamp, f"bars[{index}].timestamp")
                previous_values = m15_bars_by_timestamp.get(stamp)
                if previous_values is not None and previous_values != values:
                    raise ValueError(
                        f"bars[{index}] conflicts with another candle at "
                        f"normalized M15 timestamp {stamp.isoformat()}"
                    )
                m15_bars_by_timestamp[stamp] = values
            else:
                normalized_bars.append((stamp, *values))
        if is_m15:
            normalized_bars.extend(
                (stamp, *values)
                for stamp, values in sorted(m15_bars_by_timestamp.items())
            )
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

        for stamp, o, h, lo, c, vol in normalized_bars:
            MarketBar.objects.update_or_create(
                source=instance_id, symbol=symbol, timeframe=timeframe, timestamp=stamp,
                defaults={"open": o, "high": h, "low": lo, "close": c, "volume": vol},
            )
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
                    "broker_day": broker_day.isoformat() if broker_day else None,
                    "broker_day_history_complete": broker_day_history_complete,
                    "broker_day_opening_balance": (
                        str(broker_day_opening_balance) if broker_day_opening_balance is not None else None
                    ),
                    "broker_day_deal_count": broker_day_deal_count,
                    "broker_time": p.get("broker_time"),
                    "account": account,
                    "positions": positions,
                    "last_command_id": str(p.get("last_command_id", ""))[:128],
                },
            },
        )
    return JsonResponse({
        "ok": True, "stored_bars": len(normalized_bars),
        "telemetry_only": True, "trading_authorization": False,
    })
