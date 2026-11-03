from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ea_bridge", "0006_decisionthresholdconfig")]

    operations = [
        migrations.CreateModel(
            name="LearningTradeOutcome",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("trade_id", models.CharField(max_length=128, unique=True)),
                ("decision_id", models.CharField(blank=True, db_index=True, default="", max_length=128)),
                ("instance_id", models.CharField(blank=True, db_index=True, default="", max_length=128)),
                ("account_login", models.CharField(blank=True, default="", max_length=32)),
                ("symbol", models.CharField(blank=True, db_index=True, default="", max_length=32)),
                ("realized_pnl", models.DecimalField(decimal_places=8, max_digits=24)),
                ("risk_amount", models.DecimalField(blank=True, decimal_places=8, max_digits=24, null=True)),
                ("r_multiple", models.DecimalField(blank=True, decimal_places=8, max_digits=18, null=True)),
                ("opened_at", models.DateTimeField(blank=True, null=True)),
                ("closed_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("tags", models.JSONField(blank=True, default=dict)),
                ("execution_metrics", models.JSONField(blank=True, default=dict)),
                ("decision_snapshot", models.JSONField(blank=True, default=dict)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-closed_at", "-received_at"]},
        ),
        migrations.AddIndex(model_name="learningtradeoutcome", index=models.Index(fields=["symbol", "-closed_at"], name="learn_out_symbol_close_idx")),
        migrations.AddIndex(model_name="learningtradeoutcome", index=models.Index(fields=["decision_id"], name="learn_out_decision_idx")),
        migrations.CreateModel(
            name="LearningRecommendation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fingerprint", models.CharField(max_length=64, unique=True)),
                ("finding_code", models.CharField(db_index=True, max_length=96)),
                ("severity", models.CharField(default="INFO", max_length=16)),
                ("title", models.CharField(max_length=180)),
                ("detail", models.TextField()),
                ("evidence", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("OPEN", "Open"), ("ACCEPTED_FOR_BACKTEST", "Accepted for backtest"), ("REJECTED", "Rejected")], db_index=True, default="OPEN", max_length=32)),
                ("reviewer", models.CharField(blank=True, default="", max_length=150)),
                ("review_note", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(model_name="learningrecommendation", index=models.Index(fields=["status", "-created_at"], name="learn_reco_status_idx")),
    ]
