import json

from django.contrib import admin

from .models import (
    AccountDailyBaseline,
    EAEvent,
    EAHeartbeat,
    MarketBar,
    OrderIntentRecord,
    RiskVerdictRecord,
    StrategyDecisionRecord,
)


@admin.register(AccountDailyBaseline)
class AccountDailyBaselineAdmin(admin.ModelAdmin):
    list_display = ("broker_day", "account_login", "server_name", "opening_balance", "source_instance_id", "captured_at")
    list_filter = ("server_name", "broker_day")
    search_fields = ("account_login", "server_name", "source_instance_id")
    readonly_fields = ("account_login", "server_name", "broker_day", "opening_balance", "source_instance_id", "captured_at")
    ordering = ("-broker_day", "-captured_at")
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MarketBar)
class MarketBarAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "source", "received_at")
    list_filter = ("symbol", "timeframe", "source")
    search_fields = ("symbol", "timeframe", "source")
    readonly_fields = ("source", "symbol", "timeframe", "timestamp", "open", "high", "low", "close", "volume", "received_at")
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)
    list_per_page = 100
    show_full_result_count = False

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return True

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EAHeartbeat)
class EAHeartbeatAdmin(admin.ModelAdmin):
    list_display = (
        "instance_id", "status", "symbol", "ea_version", "last_seen",
        "terminal_connected", "trade_allowed",
    )
    list_filter = ("status", "terminal_connected", "trade_allowed", "symbol")
    search_fields = ("instance_id", "account_login", "symbol", "server_name")
    readonly_fields = ("last_seen", "payload")
    list_per_page = 50
    ordering = ("-last_seen",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return True

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EAEvent)
class EAEventAdmin(admin.ModelAdmin):
    """Read-only browser for EA snapshots and other telemetry events."""

    list_display = (
        "received_at",
        "occurred_at",
        "instance_id",
        "event_type",
        "payload_symbol",
        "payload_timeframe",
        "payload_market_value",
        "event_id",
        "payload_preview",
    )
    list_filter = ("event_type", "received_at")
    search_fields = ("instance_id", "event_id", "event_type")
    readonly_fields = (
        "instance_id", "event_id", "event_type", "occurred_at",
        "received_at", "payload",
    )
    date_hierarchy = "received_at"
    list_per_page = 50
    list_max_show_all = 200
    ordering = ("-received_at",)
    show_full_result_count = False

    @staticmethod
    def _payload_data(obj):
        payload = obj.payload
        if not isinstance(payload, dict):
            return {}
        for key in ("snapshot", "data", "market_snapshot"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                return nested
        return payload

    @classmethod
    def _payload_value(cls, obj, *keys):
        payload = cls._payload_data(obj)
        for key in keys:
            value = payload.get(key)
            if value is not None and value != "":
                return value
        return "—"

    @admin.display(description="Symbol")
    def payload_symbol(self, obj):
        return self._payload_value(obj, "symbol", "Symbol", "instrument")

    @admin.display(description="Timeframe")
    def payload_timeframe(self, obj):
        return self._payload_value(obj, "timeframe", "time_frame", "period", "tf")

    @admin.display(description="Price / Bid")
    def payload_market_value(self, obj):
        return self._payload_value(obj, "bid", "price", "close", "last", "ask")

    @admin.display(description="Snapshot payload")
    def payload_preview(self, obj):
        try:
            text = json.dumps(obj.payload, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(obj.payload)
        return text[:180] + ("…" if len(text) > 180 else "")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return True

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StrategyDecisionRecord)
class StrategyDecisionRecordAdmin(admin.ModelAdmin):
    list_display = ("created_at", "symbol", "action", "confidence", "rationale_code", "model_version")
    list_filter = ("symbol", "action", "model_version", "created_at")
    search_fields = ("decision_id", "symbol", "rationale_code", "model_version")
    readonly_fields = (
        "decision_id", "symbol", "action", "confidence", "rationale_code",
        "model_version", "features", "created_at",
    )
    ordering = ("-created_at",)
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return True

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RiskVerdictRecord)
class RiskVerdictRecordAdmin(admin.ModelAdmin):
    list_display = ("created_at", "decision_id", "approved", "reason_code", "approved_volume")
    list_filter = ("approved", "reason_code", "created_at")
    search_fields = ("decision_id", "reason_code")
    readonly_fields = ("decision_id", "approved", "reason_code", "approved_volume", "stop_loss", "take_profit", "details", "created_at")
    ordering = ("-created_at",)
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return True

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OrderIntentRecord)
class OrderIntentRecordAdmin(admin.ModelAdmin):
    list_display = ("created_at", "intent_id", "decision_id", "instance_id", "symbol", "side", "volume", "state")
    list_filter = ("state", "symbol", "side", "created_at")
    search_fields = ("intent_id", "decision_id", "instance_id", "symbol", "broker_ticket")
    readonly_fields = ("intent_id", "decision_id", "instance_id", "symbol", "side", "volume", "stop_loss", "take_profit", "state", "broker_ticket", "expires_at", "created_at", "updated_at")
    ordering = ("-created_at",)
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return True

    def has_delete_permission(self, request, obj=None):
        return False
