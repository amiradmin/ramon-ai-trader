from django.db import models

class EAHeartbeat(models.Model):
    instance_id = models.CharField(max_length=128, unique=True)
    account_login = models.CharField(max_length=32, blank=True, default="")
    server_name = models.CharField(max_length=128, blank=True, default="")
    symbol = models.CharField(max_length=32, blank=True, default="")
    ea_version = models.CharField(max_length=32, blank=True, default="")
    status = models.CharField(max_length=64, blank=True, default="UNKNOWN")
    terminal_connected = models.BooleanField(default=False)
    trade_allowed = models.BooleanField(default=False)
    last_seen = models.DateTimeField(auto_now=True)
    payload = models.JSONField(default=dict, blank=True)
    class Meta:
        indexes = [models.Index(fields=["-last_seen"], name="ea_bridge_e_last_se_4b6f70_idx")]


class AccountDailyBaseline(models.Model):
    """Immutable opening balance for one exact broker account/server/day.

    Baselines are captured from complete broker deal history when available,
    otherwise from a continuous observed rollover or explicit broker verification.
    Missing or incomplete history never falls back to current balance/equity.
    """
    CAPTURE_METHOD_CHOICES = [
        ("BROKER_HISTORY_DERIVED", "Broker history derived"),
        ("ROLLOVER_CONTINUITY", "Continuous rollover"),
        ("MANUAL_BROKER_VERIFIED", "Manual broker verified"),
    ]

    account_login = models.CharField(max_length=32)
    server_name = models.CharField(max_length=128)
    broker_day = models.DateField()
    opening_balance = models.DecimalField(max_digits=24, decimal_places=2)
    source_instance_id = models.CharField(max_length=128)
    capture_method = models.CharField(
        max_length=32,
        choices=CAPTURE_METHOD_CHOICES,
        default="ROLLOVER_CONTINUITY",
    )
    captured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["account_login", "server_name", "broker_day"],
                name="ea_daily_baseline_account_day_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["account_login", "server_name", "-broker_day"], name="ea_daily_base_lookup_idx"),
        ]

class EAEvent(models.Model):
    instance_id = models.CharField(max_length=128, db_index=True)
    event_id = models.CharField(max_length=128, unique=True)
    event_type = models.CharField(max_length=64, db_index=True)
    occurred_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    payload = models.JSONField(default=dict, blank=True)
    class Meta:
        indexes = [models.Index(fields=["-received_at"], name="ea_bridge_e_receive_2e64a1_idx")]

class MarketBar(models.Model):
    source = models.CharField(max_length=128)
    symbol = models.CharField(max_length=32)
    timeframe = models.CharField(max_length=16)
    timestamp = models.DateTimeField()
    open = models.DecimalField(max_digits=24, decimal_places=10)
    high = models.DecimalField(max_digits=24, decimal_places=10)
    low = models.DecimalField(max_digits=24, decimal_places=10)
    close = models.DecimalField(max_digits=24, decimal_places=10)
    volume = models.DecimalField(max_digits=28, decimal_places=10, default=0)
    received_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["source", "symbol", "timeframe", "timestamp"], name="ramon_marketbar_source_symbol_tf_ts_uniq")]
        indexes = [models.Index(fields=["symbol", "timeframe", "-timestamp"], name="ea_mbar_sym_tf_ts_7a6f91")]

class StrategyDecisionRecord(models.Model):
    decision_id = models.CharField(max_length=128, unique=True)
    symbol = models.CharField(max_length=32)
    action = models.CharField(max_length=8)
    confidence = models.FloatField()
    rationale_code = models.CharField(max_length=128, blank=True, default="")
    model_version = models.CharField(max_length=128)
    features = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        indexes = [models.Index(fields=["symbol", "-created_at"], name="ea_sdec_sym_created_3bfc20")]

class RiskVerdictRecord(models.Model):
    decision_id = models.CharField(max_length=128, db_index=True)
    approved = models.BooleanField(default=False)
    reason_code = models.CharField(max_length=128)
    approved_volume = models.DecimalField(max_digits=18, decimal_places=8, default=0)
    stop_loss = models.DecimalField(max_digits=24, decimal_places=10, null=True, blank=True)
    take_profit = models.DecimalField(max_digits=24, decimal_places=10, null=True, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        indexes = [models.Index(fields=["decision_id", "-created_at"], name="ea_risk_dec_created_64d31a")]

class OrderIntentRecord(models.Model):
    class State(models.TextChoices):
        PENDING = "PENDING", "Pending"
        DELIVERED = "DELIVERED", "Delivered"
        ACKNOWLEDGED = "ACKNOWLEDGED", "Acknowledged"
        FILLED = "FILLED", "Filled"
        REJECTED = "REJECTED", "Rejected"
        EXPIRED = "EXPIRED", "Expired"
    intent_id = models.CharField(max_length=128, unique=True)
    decision_id = models.CharField(max_length=128, db_index=True)
    instance_id = models.CharField(max_length=128, db_index=True)
    symbol = models.CharField(max_length=32)
    side = models.CharField(max_length=8)
    volume = models.DecimalField(max_digits=18, decimal_places=8)
    stop_loss = models.DecimalField(max_digits=24, decimal_places=10)
    take_profit = models.DecimalField(max_digits=24, decimal_places=10, null=True, blank=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.PENDING, db_index=True)
    broker_ticket = models.CharField(max_length=64, blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        indexes = [models.Index(fields=["instance_id", "state", "created_at"], name="ea_ord_inst_state_2f2c11")]


class EACommand(models.Model):
    """Durable, account-scoped command. Execution is opt-in and fail-closed."""

    class Action(models.TextChoices):
        NOOP = "NOOP", "No operation"
        OPEN = "OPEN", "Open position"
        CLOSE = "CLOSE", "Close position"
        MODIFY_SLTP = "MODIFY_SLTP", "Modify stop loss/take profit"

    class State(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CLAIMED = "CLAIMED", "Claimed by EA"
        RECEIVED = "RECEIVED", "Received by EA"
        EXECUTING = "EXECUTING", "Executing"
        EXECUTED = "EXECUTED", "Executed"
        REJECTED = "REJECTED", "Rejected"
        FAILED = "FAILED", "Failed"
        EXPIRED = "EXPIRED", "Expired"
        RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED", "Reconciliation required"

    command_id = models.CharField(max_length=128, unique=True)
    instance_id = models.CharField(max_length=128, db_index=True)
    account_login = models.CharField(max_length=32)
    symbol = models.CharField(max_length=32, blank=True, default="")
    action = models.CharField(max_length=16, choices=Action.choices)
    payload = models.JSONField(default=dict)
    decision_id = models.CharField(max_length=128, blank=True, default="")
    risk_verdict_id = models.CharField(max_length=128, blank=True, default="")
    execution_authorized = models.BooleanField(default=False)
    state = models.CharField(max_length=32, choices=State.choices, default=State.PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["instance_id", "account_login", "state", "created_at"], name="ea_cmd_scope_state_idx"),
            models.Index(fields=["state", "expires_at"], name="ea_cmd_state_exp_idx"),
        ]


class DecisionThresholdConfig(models.Model):
    """Persistent, profile-specific editable strategy thresholds owned by Django."""
    profile = models.CharField(max_length=16, unique=True)
    values = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["profile"]


class LearningTradeOutcome(models.Model):
    """Immutable, idempotently ingested closed-trade outcome used by assisted learning."""
    trade_id = models.CharField(max_length=128, unique=True)
    decision_id = models.CharField(max_length=128, blank=True, default="", db_index=True)
    instance_id = models.CharField(max_length=128, blank=True, default="", db_index=True)
    account_login = models.CharField(max_length=32, blank=True, default="")
    symbol = models.CharField(max_length=32, blank=True, default="", db_index=True)
    realized_pnl = models.DecimalField(max_digits=24, decimal_places=8)
    risk_amount = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    r_multiple = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    tags = models.JSONField(default=dict, blank=True)
    execution_metrics = models.JSONField(default=dict, blank=True)
    decision_snapshot = models.JSONField(default=dict, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-closed_at", "-received_at"]
        indexes = [
            models.Index(fields=["symbol", "-closed_at"], name="learn_out_symbol_close_idx"),
            models.Index(fields=["decision_id"], name="learn_out_decision_idx"),
        ]


class LearningRecommendation(models.Model):
    """Human-reviewed learning hypothesis. Acceptance only authorizes a backtest."""
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        ACCEPTED_FOR_BACKTEST = "ACCEPTED_FOR_BACKTEST", "Accepted for backtest"
        REJECTED = "REJECTED", "Rejected"

    fingerprint = models.CharField(max_length=64, unique=True)
    finding_code = models.CharField(max_length=96, db_index=True)
    severity = models.CharField(max_length=16, default="INFO")
    title = models.CharField(max_length=180)
    detail = models.TextField()
    evidence = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.OPEN, db_index=True)
    reviewer = models.CharField(max_length=150, blank=True, default="")
    review_note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"], name="learn_reco_status_idx")]


class LearningTrainingJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        RUNNING = "RUNNING", "Running"
        COMPLETED = "COMPLETED", "Completed"
        PARTIAL = "PARTIAL", "Partially completed"
        FAILED = "FAILED", "Failed"

    symbol = models.CharField(max_length=32, default="XAUUSD_o")
    timeframe = models.CharField(max_length=16, default="M15")
    source = models.CharField(max_length=128, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True)
    requested_models = models.JSONField(default=list)
    created_by = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"], name="learn_train_job_status_idx")]


class LearningModelRun(models.Model):
    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        RUNNING = "RUNNING", "Running"
        PASSED = "PASSED", "Validation passed"
        FAILED = "FAILED", "Failed"
        REJECTED = "REJECTED", "Validation failed"

    job = models.ForeignKey(LearningTrainingJob, on_delete=models.CASCADE, related_name="model_runs")
    model_key = models.CharField(max_length=32, db_index=True)
    model_name = models.CharField(max_length=80)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True)
    version = models.CharField(max_length=64, blank=True, default="")
    artifact_path = models.CharField(max_length=512, blank=True, default="")
    artifact_sha256 = models.CharField(max_length=64, blank=True, default="")
    artifact_size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    artifact_format = models.CharField(max_length=16, blank=True, default="joblib")
    training_started_at = models.DateTimeField(null=True, blank=True)
    training_finished_at = models.DateTimeField(null=True, blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    validation = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-training_started_at", "-id"]
        indexes = [models.Index(fields=["model_key", "-training_finished_at"], name="learn_model_run_latest_idx")]


class ArimaShadowForecast(models.Model):
    """Immutable forecast inputs and forward outcomes; never trading authority."""

    class Status(models.TextChoices):
        RUNNING = "RUNNING", "Calculating"
        READY = "READY", "Awaiting target candle"
        SCORED = "SCORED", "Compared with actual close"
        SKIPPED = "SKIPPED", "Not eligible for forward scoring"
        FAILED = "FAILED", "Forecast failed"

    source = models.CharField(max_length=128)
    symbol = models.CharField(max_length=32)
    timeframe = models.CharField(max_length=16, default="M15")
    model_version = models.CharField(max_length=64)
    origin_bar_time = models.DateTimeField()
    target_bar_time = models.DateTimeField()
    target_close_time = models.DateTimeField()
    origin_close = models.DecimalField(max_digits=24, decimal_places=10)
    forecast_close = models.DecimalField(max_digits=24, decimal_places=10, null=True, blank=True)
    lower_95 = models.DecimalField(max_digits=24, decimal_places=10, null=True, blank=True)
    upper_95 = models.DecimalField(max_digits=24, decimal_places=10, null=True, blank=True)
    actual_close = models.DecimalField(max_digits=24, decimal_places=10, null=True, blank=True)
    observations = models.PositiveIntegerField(default=0)
    input_sha256 = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    reason = models.CharField(max_length=128, blank=True, default="")
    diagnostics = models.JSONField(default=dict)
    started_at = models.DateTimeField()
    generated_at = models.DateTimeField(null=True, blank=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["source", "symbol", "timeframe", "origin_bar_time", "model_version"],
            name="arima_shadow_origin_uniq",
        )]
        indexes = [
            models.Index(fields=["source", "symbol", "model_version", "-origin_bar_time"], name="arima_shadow_scope_latest"),
            models.Index(fields=["status", "target_close_time"], name="arima_shadow_due_idx"),
        ]
