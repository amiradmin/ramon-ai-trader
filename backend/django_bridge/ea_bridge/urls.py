from django.urls import path
from . import views
from . import decision as decision_views
from . import commands as command_views
from . import telemetry as telemetry_views

urlpatterns = [
    path("handshake", views.handshake, name="ea-handshake"),
    path("heartbeat", views.heartbeat, name="ea-heartbeat"),
    path("events", views.events, name="ea-events"),
    path("decision", decision_views.decision, name="ea-decision"),
    path("telemetry", telemetry_views.telemetry, name="ea-telemetry"),
    path("commands/next", command_views.next_command, name="ea-command-next"),
    path("commands/ack", command_views.acknowledge_command, name="ea-command-ack"),
    path("status", views.status, name="ea-status"),
    path("dashboard", views.dashboard, name="ramon-dashboard"),
    path("dashboard-data", views.dashboard_data, name="ramon-dashboard-data"),
]
