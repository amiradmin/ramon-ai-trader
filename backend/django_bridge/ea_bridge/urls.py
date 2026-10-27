from django.urls import path
from . import views
from . import decision as decision_views

urlpatterns = [
    path("handshake", views.handshake, name="ea-handshake"),
    path("heartbeat", views.heartbeat, name="ea-heartbeat"),
    path("events", views.events, name="ea-events"),
    path("decision", decision_views.decision, name="ea-decision"),
    path("status", views.status, name="ea-status"),
    path("dashboard", views.dashboard, name="ramon-dashboard"),
    path("dashboard-data", views.dashboard_data, name="ramon-dashboard-data"),
]
