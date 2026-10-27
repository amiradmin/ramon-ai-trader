from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ea_bridge", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="EACommand",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("command_id", models.CharField(max_length=128, unique=True)),
                ("instance_id", models.CharField(db_index=True, max_length=128)),
                ("account_login", models.CharField(max_length=32)),
                ("symbol", models.CharField(blank=True, default="", max_length=32)),
                ("action", models.CharField(choices=[("NOOP", "No operation"), ("OPEN", "Open position"), ("CLOSE", "Close position"), ("MODIFY_SLTP", "Modify stop loss/take profit")], max_length=16)),
                ("payload", models.JSONField(default=dict)),
                ("decision_id", models.CharField(blank=True, default="", max_length=128)),
                ("risk_verdict_id", models.CharField(blank=True, default="", max_length=128)),
                ("execution_authorized", models.BooleanField(default=False)),
                ("state", models.CharField(choices=[("PENDING", "Pending"), ("CLAIMED", "Claimed by EA"), ("RECEIVED", "Received by EA"), ("EXECUTING", "Executing"), ("EXECUTED", "Executed"), ("REJECTED", "Rejected"), ("FAILED", "Failed"), ("EXPIRED", "Expired"), ("RECONCILIATION_REQUIRED", "Reconciliation required")], db_index=True, default="PENDING", max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("claimed_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("result", models.JSONField(blank=True, default=dict)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["instance_id", "account_login", "state", "created_at"], name="ea_cmd_scope_state_idx"),
                    models.Index(fields=["state", "expires_at"], name="ea_cmd_state_exp_idx"),
                ],
            },
        ),
    ]
