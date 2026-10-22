from django.contrib import admin
from .models import EAEvent, EAHeartbeat

@admin.register(EAHeartbeat)
class EAHeartbeatAdmin(admin.ModelAdmin):
    list_display = ("instance_id", "status", "symbol", "ea_version", "last_seen", "terminal_connected", "trade_allowed")
    search_fields = ("instance_id", "account_login", "symbol", "server_name")
    readonly_fields = ("last_seen",)

@admin.register(EAEvent)
class EAEventAdmin(admin.ModelAdmin):
    list_display = ("received_at", "instance_id", "event_type", "event_id")
    list_filter = ("event_type",)
    search_fields = ("instance_id", "event_id")
