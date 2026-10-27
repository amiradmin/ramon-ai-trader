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
    class Action(models.TextChoices):
        NOOP = "NOOP", "No operation"
        OPEN = "OPEN", "Open position"
        CLOSE = "CLOSE", "Close position"
        MODIFY_SLTP = "MODIFY_SLTP", "Modify stop loss/take profit"

    class State(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CLAIMED = "CLAIMED", "Claimed"
        RECEIVED = "RECEIVED", "Received"
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
