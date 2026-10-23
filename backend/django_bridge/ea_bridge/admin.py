import json

from django.contrib import admin

from .models import EAEvent, EAHeartbeat


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
        # Permit opening the detail page; all fields remain read-only.
        return True

    def has_delete_permission(self, request, obj=None):
        return False
