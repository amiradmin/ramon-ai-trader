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
        indexes = [models.Index(fields=["-last_seen"])]

class EAEvent(models.Model):
    instance_id = models.CharField(max_length=128, db_index=True)
    event_id = models.CharField(max_length=128, unique=True)
    event_type = models.CharField(max_length=64, db_index=True)
    occurred_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["-received_at"])]
