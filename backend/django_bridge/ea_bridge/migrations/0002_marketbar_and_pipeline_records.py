from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ea_bridge", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="MarketBar",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source", models.CharField(max_length=128)),
                ("symbol", models.CharField(max_length=32)),
                ("timeframe", models.CharField(max_length=16)),
                ("timestamp", models.DateTimeField()),
                ("open", models.DecimalField(max_digits=24, decimal_places=10)),
                ("high", models.DecimalField(max_digits=24, decimal_places=10)),
                ("low", models.DecimalField(max_digits=24, decimal_places=10)),
                ("close", models.DecimalField(max_digits=24, decimal_places=10)),
                ("volume", models.DecimalField(default=0, max_digits=28, decimal_places=10)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "indexes": [models.Index(fields=["symbol", "timeframe", "-timestamp"], name="ea_mbar_sym_tf_ts_7a6f91")],
                "constraints": [models.UniqueConstraint(fields=("source", "symbol", "timeframe", "timestamp"), name="ramon_marketbar_source_symbol_tf_ts_uniq")],
            },
        ),
        migrations.CreateModel(
            name="StrategyDecisionRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("decision_id", models.CharField(max_length=128, unique=True)),
                ("symbol", models.CharField(max_length=32)),
                ("action", models.CharField(max_length=8)),
                ("confidence", models.FloatField()),
                ("rationale_code", models.CharField(blank=True, default="", max_length=128)),
                ("model_version", models.CharField(max_length=128)),
                ("features", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"indexes": [models.Index(fields=["symbol", "-created_at"], name="ea_sdec_sym_created_3bfc20")]},
        ),
        migrations.CreateModel(
            name="RiskVerdictRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("decision_id", models.CharField(db_index=True, max_length=128)),
                ("approved", models.BooleanField(default=False)),
                ("reason_code", models.CharField(max_length=128)),
                ("approved_volume", models.DecimalField(default=0, max_digits=18, decimal_places=8)),
                ("stop_loss", models.DecimalField(blank=True, max_digits=24, decimal_places=10, null=True)),
                ("take_profit", models.DecimalField(blank=True, max_digits=24, decimal_places=10, null=True)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"indexes": [models.Index(fields=["decision_id", "-created_at"], name="ea_risk_dec_created_64d31a")]},
        ),
        migrations.CreateModel(
            name="OrderIntentRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("intent_id", models.CharField(max_length=128, unique=True)),
                ("decision_id", models.CharField(db_index=True, max_length=128)),
                ("instance_id", models.CharField(db_index=True, max_length=128)),
                ("symbol", models.CharField(max_length=32)),
                ("side", models.CharField(max_length=8)),
                ("volume", models.DecimalField(max_digits=18, decimal_places=8)),
                ("stop_loss", models.DecimalField(max_digits=24, decimal_places=10)),
                ("take_profit", models.DecimalField(blank=True, max_digits=24, decimal_places=10, null=True)),
                ("state", models.CharField(choices=[("PENDING", "Pending"), ("DELIVERED", "Delivered"), ("ACKNOWLEDGED", "Acknowledged"), ("FILLED", "Filled"), ("REJECTED", "Rejected"), ("EXPIRED", "Expired")], db_index=True, default="PENDING", max_length=16)),
                ("broker_ticket", models.CharField(blank=True, default="", max_length=64)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"indexes": [models.Index(fields=["instance_id", "state", "created_at"], name="ea_ord_inst_state_2f2c11")]},
        ),
    ]
