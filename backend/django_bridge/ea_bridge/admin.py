import json

from django.contrib import admin
from django.utils.html import format_html

from .models import (
    EAEvent,
    EAHeartbeat,
    MarketBar,
    OrderIntentRecord,
    RiskVerdictRecord,
    StrategyDecisionRecord,
)


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


@admin.register(EAEvent)
class EAEventAdmin(admin.ModelAdmin):
    """Read-only snapshot/event browser with server-side pagination."""
    list_display = ("received_at", "occurred_at", "instance_id", "event_type", "event_id", "payload_preview")
    list_filter = ("event_type", "received_at")
    search_fields = ("instance_id", "event_id", "event_type")
    readonly_fields = ("instance_id", "event_id", "event_type", "occurred_at", "received_at", "payload")
    date_hierarchy = "received_at"
    list_per_page = 50
    list_max_show_all = 200
    ordering = ("-received_at",)
    show_full_result_count = False

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
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MarketBar)
class MarketBarAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "source", "symbol", "timeframe", "open", "high", "low", "close", "volume")
    list_filter = ("symbol", "timeframe", "source")
    search_fields = ("symbol", "source")
    date_hierarchy = "timestamp"
    list_per_page = 50
    ordering = ("-timestamp",)
    show_full_result_count = False


@admin.register(StrategyDecisionRecord)
class StrategyDecisionRecordAdmin(admin.ModelAdmin):
    list_display = ("created_at", "decision_id", "symbol", "action", "confidence", "model_version")
    list_filter = ("symbol", "action", "model_version")
    search_fields = ("decision_id", "symbol", "rationale_code", "model_version")
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"
    list_per_page = 50
    ordering = ("-created_at",)


@admin.register(RiskVerdictRecord)
class RiskVerdictRecordAdmin(admin.ModelAdmin):
    list_display = ("created_at", "decision_id", "approved", "reason_code", "approved_volume")
    list_filter = ("approved", "reason_code")
    search_fields = ("decision_id", "reason_code")
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"
    list_per_page = 50
    ordering = ("-created_at",)


@admin.register(OrderIntentRecord)
class OrderIntentRecordAdmin(admin.ModelAdmin):
    list_display = ("created_at", "intent_id", "decision_id", "instance_id", "symbol", "side", "volume", "state")
    list_filter = ("state", "symbol", "side")
    search_fields = ("intent_id", "decision_id", "instance_id", "symbol", "broker_ticket")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"
    list_per_page = 50
    ordering = ("-created_at",)
