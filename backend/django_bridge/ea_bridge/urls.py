from django.urls import path
from . import views
from . import decision as decision_views
from . import commands as command_views
from . import telemetry as telemetry_views
from . import learning_views

urlpatterns = [
    path("handshake", views.handshake, name="ea-handshake"),
    path("heartbeat", views.heartbeat, name="ea-heartbeat"),
    path("events", views.events, name="ea-events"),
    path("decision", decision_views.decision, name="ea-decision"),
    path("telemetry", telemetry_views.telemetry, name="ea-telemetry"),
    path("market-data/status", telemetry_views.market_data_status, name="ea-market-data-status"),
    path("commands/next", command_views.next_command, name="ea-command-next"),
    path("commands/ack", command_views.acknowledge_command, name="ea-command-ack"),
    path("status", views.status, name="ea-status"),
    path("dashboard", views.dashboard, name="ramon-dashboard"),
    path("dashboard-data", views.dashboard_data, name="ramon-dashboard-data"),
    path("dashboard/pnl-summary", views.dashboard_pnl_summary, name="ramon-dashboard-pnl-summary"),
    path("dashboard-snapshot", views.dashboard_snapshot, name="ramon-dashboard-snapshot"),
    path("decision-thresholds", views.decision_thresholds, name="ramon-decision-thresholds"),
    path("learning/dashboard", learning_views.learning_dashboard, name="ramon-learning-dashboard"),
    path("learning/training/status", learning_views.training_status, name="ramon-learning-training-status"),
    path("learning/training/start", learning_views.start_training, name="ramon-learning-training-start"),
    path("learning/outcomes/recent", learning_views.recent_learning_outcomes, name="ramon-learning-recent-outcomes"),
    path("learning/overview", learning_views.learning_overview, name="ramon-learning-overview"),
    path("learning/outcomes", learning_views.record_outcome, name="ramon-learning-outcomes"),
    path("learning/recommendations/generate", learning_views.generate_recommendations, name="ramon-learning-generate"),
    path("learning/recommendations/<int:recommendation_id>/review", learning_views.review_recommendation, name="ramon-learning-review"),
]
