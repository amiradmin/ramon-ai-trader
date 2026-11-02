from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ea_bridge", "0004_accountdailybaseline"),
    ]

    operations = [
        migrations.AddField(
            model_name="accountdailybaseline",
            name="capture_method",
            field=models.CharField(
                choices=[
                    ("BROKER_HISTORY_DERIVED", "Broker history derived"),
                    ("ROLLOVER_CONTINUITY", "Continuous rollover"),
                    ("MANUAL_BROKER_VERIFIED", "Manual broker verified"),
                ],
                default="ROLLOVER_CONTINUITY",
                max_length=32,
            ),
        ),
    ]
