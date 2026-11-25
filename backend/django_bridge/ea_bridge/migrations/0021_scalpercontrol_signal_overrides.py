from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('ea_bridge', '0020_alter_learningtrainingjob_symbol')]
    operations = [migrations.AddField(model_name='scalpercontrol', name='signal_overrides',
        field=models.JSONField(default=dict, blank=True))]
