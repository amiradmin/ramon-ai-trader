"""Authenticated telemetry ingestion for the dedicated command EA.

Telemetry is observational only and never grants trading authorization.
"""
import json
from datetime import date, timedelta
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
from .market_bars import validate_bar_timestamp


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
        bars_by_timeframe = p.get("bars_by_timeframe")
        legacy_bars = p.get("bars", [])
        if not instance_id or len(instance_id) > 128:
            raise ValueError("valid instance_id required")
        if not account_login or len(account_login) > 32:
            raise ValueError("valid account_login required")
        if not symbol or len(symbol) > 32 or not timeframe or len(timeframe) > 16:
            raise ValueError("valid symbol and timeframe required")
        if not isinstance(quote, dict):
            raise ValueError("quote object required")
        if bars_by_timeframe is None:
            if not isinstance(legacy_bars, list) or len(legacy_bars) > 512:
                raise ValueError("at most 512 legacy bars required")
            grouped_bars = {timeframe.upper(): legacy_bars}
        else:
            if not isinstance(bars_by_timeframe, dict) or not bars_by_timeframe:
                raise ValueError("bars_by_timeframe must be a non-empty object")
            grouped_bars = {}
            total_bars = 0
            for raw_tf, tf_bars in bars_by_timeframe.items():
                tf = str(raw_tf).strip().upper()
                if tf not in {"M1", "M5", "M15", "H1"}:
                    raise ValueError(f"unsupported telemetry timeframe: {tf}")
                if not isinstance(tf_bars, list) or len(tf_bars) > 512:
                    raise ValueError(f"bars_by_timeframe.{tf} must be an array of at most 512 bars")
                total_bars += len(tf_bars)
                grouped_bars[tf] = tf_bars
            if total_bars > 1024:
                raise ValueError("at most 1024 bars may be submitted per telemetry request")
        bid = _decimal(quote.get("bid"), "quote.bid")
        ask = _decimal(quote.get("ask"), "quote.ask")
        point = _decimal(quote.get("point"), "quote.point")
        if bid <= 0 or ask < bid or point <= 0:
            raise ValueError("invalid quote values")
        normalized_bars = []
        for bar_timeframe, tf_bars in grouped_bars.items():
            for index, bar in enumerate(tf_bars):
                field = f"bars_by_timeframe.{bar_timeframe}[{index}]"
                if not isinstance(bar, dict) or bar.get("closed") is not True:
                    raise ValueError(f"{field} must be a completed candle")
                stamp = parse_datetime(str(bar.get("timestamp", "")))
                if stamp is None or stamp.tzinfo is None:
                    raise ValueError(f"{field}.timestamp must include timezone")
                stamp = validate_bar_timestamp(stamp, bar_timeframe)
                o = _decimal(bar.get("open"), f"{field}.open")
                h = _decimal(bar.get("high"), f"{field}.high")
                lo = _decimal(bar.get("low"), f"{field}.low")
                c = _decimal(bar.get("close"), f"{field}.close")
                vol = _decimal(bar.get("volume", 0), f"{field}.volume")
                if min(o, h, lo, c) <= 0 or vol < 0 or h < max(o, c, lo) or lo > min(o, c, h):
                    raise ValueError(f"{field} has invalid OHLCV values")
                normalized_bars.append((bar_timeframe, stamp, o, h, lo, c, vol))
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
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    with transaction.atomic():
        # Establish a baseline only across an observed, continuous broker-day rollover.
        # A missed rollover leaves the new day unbaselined and therefore fail-closed.
        previous = EAHeartbeat.objects.select_for_update().filter(instance_id=instance_id).first()
        if previous is not None and broker_day is not None and server_name and account_balance is not None:
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
                    account_login=account_login,
                    server_name=server_name,
                    broker_day=broker_day,
                    defaults={
                        "opening_balance": previous_balance,
                        "source_instance_id": instance_id,
                    },
                )

        for bar_timeframe, stamp, o, h, lo, c, vol in normalized_bars:
            MarketBar.objects.update_or_create(
                source=instance_id, symbol=symbol, timeframe=bar_timeframe, timestamp=stamp,
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
                    "broker_time": p.get("broker_time"),
                    "account": account,
                    "positions": positions,
                    "last_command_id": str(p.get("last_command_id", ""))[:128],
                },
            },
        )
    return JsonResponse({
        "ok": True, "stored_bars": len(normalized_bars), "stored_timeframes": sorted(grouped_bars),
        "telemetry_only": True, "trading_authorization": False,
    })
