from django.urls import path
from . import views
from . import decision as decision_views
from . import commands as command_views
from . import telemetry as telemetry_views
from . import learning_views
from . import forensic_views
from . import forecast_views
from . import economic_calendar
from . import candidate_observer
from . import execution_observer
from . import reliability
from . import scalp_observer
from . import scalper_views
from . import trend_observer
from . import strategy_views
from . import live_strategy_settings
from . import time_status
from . import validation_views
from . import replay_views
from . import replay_backtest
from . import replay_jobs

urlpatterns = [
    path("dashboard/live-strategy-settings", live_strategy_settings.live_strategy_settings, name="ramon-live-strategy-settings"),
    path("dashboard/live-scalper-risk", live_strategy_settings.live_scalper_risk, name="ramon-live-scalper-risk"),
    path("simulator", replay_views.simulator, name="ramon-simulator"),
    path("simulator/bars", replay_views.simulator_bars, name="ramon-simulator-bars"),
    path("simulator/recorded-stats", replay_views.simulator_recorded_stats, name="ramon-simulator-recorded-stats"),
    path("simulator/backtest", replay_backtest.backtest, name="ramon-simulator-backtest"),
    path("simulator/backtest/jobs", replay_jobs.create_job, name="ramon-simulator-backtest-job-create"),
    path("simulator/backtest/jobs/<str:job_id>", replay_jobs.job_status, name="ramon-simulator-backtest-job-status"),
    path("time-status", time_status.time_status, name="ramon-time-status"),
    path("strategies", strategy_views.strategies_status, name="strategy-workers-status"),
    path("strategies/<slug:slug>", strategy_views.strategy_console, name="strategy-worker-console"),
    path("strategies/<slug:slug>/entries", strategy_views.strategy_entries_control, name="strategy-worker-entries"),
    path("research/trend", trend_observer.trend_status, name="ramon-trend-shadow"),
    path("scalper", scalper_views.scalper_console, name="ramon-scalper-console"),
    path("scalper/status", scalper_views.scalper_status, name="ramon-scalper-status"),
    path("scalper/entries", scalper_views.scalper_entries_control, name="ramon-scalper-entries"),
    path("research/scalping", scalp_observer.scalp_status, name="ramon-scalping-shadow"),
    path("reliability", reliability.reliability_status, name="ramon-reliability"),
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
    path("strategy-validation/", validation_views.strategy_validation, name="ramon-strategy-validation"),
    path("dashboard-data", views.dashboard_data, name="ramon-dashboard-data"),
    path("forecast/arima", forecast_views.arima_status, name="ramon-arima-shadow-status"),
    path("research/economic-calendar", economic_calendar.calendar_status, name="ramon-economic-calendar"),
    path("research/paper-results", views.paper_results, name="ramon-paper-results"),
    path("research/candidates", candidate_observer.candidate_status, name="ramon-candidate-observer"),
    path("research/execution", execution_observer.execution_status, name="ramon-execution-observer"),
    path("dashboard/pnl-summary", views.dashboard_pnl_summary, name="ramon-dashboard-pnl-summary"),
    path("dashboard-snapshot", views.dashboard_snapshot, name="ramon-dashboard-snapshot"),
    path("decision-thresholds", views.decision_thresholds, name="ramon-decision-thresholds"),
    path("learning/dashboard", learning_views.learning_dashboard, name="ramon-learning-dashboard"),
    path("learning/forensics", forensic_views.forensic_dashboard, name="ramon-trade-forensics"),
    path("learning/forensics/data", forensic_views.forensic_data, name="ramon-trade-forensics-data"),
    path("learning/training/status", learning_views.training_status, name="ramon-learning-training-status"),
    path("learning/training/start", learning_views.start_training, name="ramon-learning-training-start"),
    path("learning/outcomes/recent", learning_views.recent_learning_outcomes, name="ramon-learning-recent-outcomes"),
    path("learning/overview", learning_views.learning_overview, name="ramon-learning-overview"),
    path("learning/outcomes", learning_views.record_outcome, name="ramon-learning-outcomes"),
    path("learning/recommendations/generate", learning_views.generate_recommendations, name="ramon-learning-generate"),
    path("learning/recommendations/<int:recommendation_id>/review", learning_views.review_recommendation, name="ramon-learning-review"),
]
