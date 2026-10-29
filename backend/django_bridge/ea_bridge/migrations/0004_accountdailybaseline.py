from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ea_bridge", "0003_merge_eacommand_ramon_pipeline"),
    ]

    operations = [
        migrations.CreateModel(
            name="AccountDailyBaseline",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("account_login", models.CharField(max_length=32)),
                ("server_name", models.CharField(max_length=128)),
                ("broker_day", models.DateField()),
                ("opening_balance", models.DecimalField(decimal_places=2, max_digits=24)),
                ("source_instance_id", models.CharField(max_length=128)),
                ("captured_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("account_login", "server_name", "broker_day"),
                        name="ea_daily_baseline_account_day_uniq",
                    ),
                ],
                "indexes": [
                    models.Index(
                        fields=["account_login", "server_name", "-broker_day"],
                        name="ea_daily_base_lookup_idx",
                    ),
                ],
            },
        ),
    ]
