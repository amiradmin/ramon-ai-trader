from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies=[('ea_bridge','0017_trend_paper_candidate')]
    operations=[migrations.CreateModel(name='StrategyWorkerControl',fields=[
        ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
        ('setup',models.CharField(max_length=64,unique=True)),
        ('source',models.CharField(max_length=128)),('account_login',models.CharField(max_length=32)),
        ('server_name',models.CharField(max_length=128)),('symbol',models.CharField(max_length=32)),
        ('automatic',models.BooleanField(default=True)),('last_seen',models.DateTimeField(null=True,blank=True)),
        ('state',models.CharField(max_length=128,default='STARTING')),('latest',models.JSONField(default=dict)),
    ])]
