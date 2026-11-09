from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('local', '0002_auto_20261108_2138')]

    initial = False

    operations = [
        ops.CreateModel(
            name='NodoEstado',
            fields=[
                ('node_id', fields.CharField(primary_key=True, unique=True, db_index=True, max_length=64)),
                ('state', fields.CharField(null=True, max_length=32)),
                ('agent_version', fields.CharField(null=True, max_length=32)),
                ('loaded_model', fields.CharField(null=True, max_length=64)),
                ('slots_total', fields.IntField(default=0)),
                ('slots_busy', fields.IntField(default=0)),
                ('last_error', fields.TextField(null=True, unique=False)),
                ('last_seen', fields.DatetimeField(auto_now=True, auto_now_add=False)),
            ],
            options={'table': 'nodo_estado', 'app': 'local', 'pk_attr': 'node_id'},
            bases=['Model'],
        ),
        ops.AddField(
            model_name='Modelo',
            name='prioridad',
            field=fields.IntField(default=0, null=True),
        ),
        ops.RunSQL(
            sql="UPDATE modelo SET prioridad = 0 WHERE prioridad IS NULL",
            reverse_sql="SELECT 1",
        ),
        ops.AlterField(
            model_name='Modelo',
            name='prioridad',
            field=fields.IntField(default=0),
        ),
    ]
