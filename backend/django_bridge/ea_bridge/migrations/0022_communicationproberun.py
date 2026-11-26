from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('ea_bridge', '0021_scalpercontrol_signal_overrides')]
    operations = [migrations.CreateModel(
        name='CommunicationProbeRun',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('instance_id', models.CharField(max_length=128, unique=True)),
            ('account_login', models.CharField(max_length=32)),
            ('server_name', models.CharField(max_length=128)),
            ('symbol', models.CharField(max_length=32)),
            ('run_id', models.CharField(max_length=32)),
            ('started_at', models.DateTimeField()),
            ('deadline', models.DateTimeField()),
            ('samples', models.JSONField(default=list)),
        ],
    )]
