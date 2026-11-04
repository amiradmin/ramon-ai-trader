# Generated for persistent learning training jobs and per-model validation history.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ea_bridge", "0007_learning_engine")]

    operations = [
        migrations.CreateModel(
            name="LearningTrainingJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("symbol", models.CharField(default="XAUUSD_o", max_length=32)),
                ("timeframe", models.CharField(default="M15", max_length=16)),
                ("source", models.CharField(blank=True, default="", max_length=128)),
                ("status", models.CharField(choices=[("QUEUED", "Queued"), ("RUNNING", "Running"), ("COMPLETED", "Completed"), ("PARTIAL", "Partially completed"), ("FAILED", "Failed")], db_index=True, default="QUEUED", max_length=16)),
                ("requested_models", models.JSONField(default=list)),
                ("created_by", models.CharField(blank=True, default="", max_length=150)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("error", models.TextField(blank=True, default="")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="LearningModelRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("model_key", models.CharField(db_index=True, max_length=32)),
                ("model_name", models.CharField(max_length=80)),
                ("status", models.CharField(choices=[("QUEUED", "Queued"), ("RUNNING", "Running"), ("PASSED", "Validation passed"), ("FAILED", "Failed"), ("REJECTED", "Validation failed")], db_index=True, default="QUEUED", max_length=16)),
                ("version", models.CharField(blank=True, default="", max_length=64)),
                ("training_started_at", models.DateTimeField(blank=True, null=True)),
                ("training_finished_at", models.DateTimeField(blank=True, null=True)),
                ("metrics", models.JSONField(blank=True, default=dict)),
                ("validation", models.JSONField(blank=True, default=dict)),
                ("error", models.TextField(blank=True, default="")),
                ("job", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="model_runs", to="ea_bridge.learningtrainingjob")),
            ],
            options={"ordering": ["-training_started_at", "-id"]},
        ),
        migrations.AddIndex(model_name="learningtrainingjob", index=models.Index(fields=["status", "-created_at"], name="learn_train_job_status_idx")),
        migrations.AddIndex(model_name="learningmodelrun", index=models.Index(fields=["model_key", "-training_finished_at"], name="learn_model_run_latest_idx")),
    ]
