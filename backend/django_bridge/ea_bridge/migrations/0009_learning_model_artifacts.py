from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ea_bridge", "0008_learning_training")]

    operations = [
        migrations.AddField(
            model_name="learningmodelrun",
            name="artifact_path",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
        migrations.AddField(
            model_name="learningmodelrun",
            name="artifact_sha256",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="learningmodelrun",
            name="artifact_size_bytes",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="learningmodelrun",
            name="artifact_format",
            field=models.CharField(blank=True, default="joblib", max_length=16),
        ),
    ]
