from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ea_bridge", "0005_accountdailybaseline_capture_method")]

    operations = [
        migrations.CreateModel(
            name="DecisionThresholdConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("profile", models.CharField(max_length=16, unique=True)),
                ("values", models.JSONField(default=dict, blank=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
