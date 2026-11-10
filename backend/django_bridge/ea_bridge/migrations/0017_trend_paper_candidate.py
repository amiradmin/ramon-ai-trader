from django.db import migrations, models


def protect(apps, schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        schema_editor.execute("""
        CREATE FUNCTION trend_preserve_inputs() RETURNS trigger AS $$
        BEGIN
            IF (to_jsonb(NEW)-'status'-'outcome'-'settled_at') IS DISTINCT FROM
               (to_jsonb(OLD)-'status'-'outcome'-'settled_at') OR
               (OLD.status IN ('SCORED','SKIPPED','NOT_ENTERED') AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD))
            THEN RAISE EXCEPTION 'Trend inputs and terminal outcomes are immutable'; END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER trend_inputs_immutable BEFORE UPDATE ON ea_bridge_trendobservation
        FOR EACH ROW EXECUTE FUNCTION trend_preserve_inputs();
        """)
    elif schema_editor.connection.vendor=='sqlite':
        fields=['source','account_login','server_name','symbol','version','signal_bar_time','recorded_at',
                'evaluation_start','evaluation_end','action','reason','snapshot']
        changes=' OR '.join(f'NEW.{f} IS NOT OLD.{f}' for f in fields)
        terminal="OLD.status IN ('SCORED','SKIPPED','NOT_ENTERED') AND (NEW.status IS NOT OLD.status OR NEW.outcome IS NOT OLD.outcome OR NEW.settled_at IS NOT OLD.settled_at)"
        schema_editor.execute(f"CREATE TRIGGER trend_inputs_immutable BEFORE UPDATE ON ea_bridge_trendobservation WHEN ({changes}) OR ({terminal}) BEGIN SELECT RAISE(ABORT,'Immutable trend inputs and outcomes'); END")


def unprotect(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('DROP TRIGGER trend_inputs_immutable ON ea_bridge_trendobservation')
        schema_editor.execute('DROP FUNCTION trend_preserve_inputs()')
    elif schema_editor.connection.vendor=='sqlite':
        schema_editor.execute('DROP TRIGGER trend_inputs_immutable')


class Migration(migrations.Migration):
    dependencies=[('ea_bridge','0016_scalp_audit_journal')]
    operations=[
        migrations.CreateModel(name='TrendWorkerState',fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('source',models.CharField(max_length=128,unique=True)),
            ('updated_at',models.DateTimeField()),('reason',models.CharField(max_length=128)),
        ]),
        migrations.CreateModel(name='TrendObservation',fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('source',models.CharField(max_length=128)),('account_login',models.CharField(max_length=32)),
            ('server_name',models.CharField(max_length=128)),('symbol',models.CharField(max_length=32)),
            ('version',models.CharField(max_length=64)),('signal_bar_time',models.DateTimeField()),
            ('recorded_at',models.DateTimeField()),('evaluation_start',models.DateTimeField()),
            ('evaluation_end',models.DateTimeField()),('action',models.CharField(max_length=8)),
            ('reason',models.CharField(max_length=128)),('status',models.CharField(max_length=16,db_index=True)),
            ('snapshot',models.JSONField(default=dict)),('outcome',models.JSONField(default=dict)),
            ('settled_at',models.DateTimeField(null=True,blank=True)),
        ],options={'constraints':[models.UniqueConstraint(fields=('source','account_login','server_name','symbol','version','signal_bar_time'),name='trend_signal_scope_unique')]}),
        migrations.RunPython(protect,unprotect),
    ]
