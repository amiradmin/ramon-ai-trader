from django.db import migrations, models

class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(name="EAHeartbeat", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("instance_id", models.CharField(max_length=128, unique=True)), ("account_login", models.CharField(blank=True, default="", max_length=32)), ("server_name", models.CharField(blank=True, default="", max_length=128)), ("symbol", models.CharField(blank=True, default="", max_length=32)), ("ea_version", models.CharField(blank=True, default="", max_length=32)), ("status", models.CharField(blank=True, default="UNKNOWN", max_length=64)), ("terminal_connected", models.BooleanField(default=False)), ("trade_allowed", models.BooleanField(default=False)), ("last_seen", models.DateTimeField(auto_now=True)), ("payload", models.JSONField(blank=True, default=dict))], options={"indexes": [models.Index(fields=["-last_seen"], name="ea_bridge_e_last_se_4b6f70_idx")]}),
        migrations.CreateModel(name="EAEvent", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("instance_id", models.CharField(db_index=True, max_length=128)), ("event_id", models.CharField(max_length=128, unique=True)), ("event_type", models.CharField(db_index=True, max_length=64)), ("occurred_at", models.DateTimeField(blank=True, null=True)), ("received_at", models.DateTimeField(auto_now_add=True)), ("payload", models.JSONField(blank=True, default=dict))], options={"indexes": [models.Index(fields=["-received_at"], name="ea_bridge_e_receive_2e64a1_idx")]}),
    ]
