from django.db import models


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
    training_started_at = models.DateTimeField(null=True, blank=True)
    training_finished_at = models.DateTimeField(null=True, blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    validation = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-training_started_at", "-id"]
        indexes = [models.Index(fields=["model_key", "-training_finished_at"], name="learn_model_run_latest_idx")]
