from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('ea_bridge', '0019_alter_learningtrainingjob_symbol'),
    ]

    operations = [
        migrations.AlterField(
            model_name='learningtrainingjob',
            name='symbol',
            field=models.CharField(default='XAUUSD_l', max_length=32),
        ),
    ]
