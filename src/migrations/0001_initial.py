from tortoise import migrations
from tortoise.migrations import operations as ops
import functools
from json import dumps, loads
from tortoise import fields

class Migration(migrations.Migration):
    initial = True

    operations = [
        ops.CreateModel(
            name='Calificacion',
            fields=[
                ('id', fields.BigIntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('envio_id', fields.IntField()),
                ('problema_id', fields.IntField(null=True)),
                ('estudiante_id', fields.IntField(null=True)),
                ('metrica', fields.CharField(max_length=8)),
                ('nivel', fields.IntField()),
                ('modelo', fields.CharField(max_length=64)),
                ('estado', fields.CharField(default='pending', max_length=16)),
                ('worker_id', fields.CharField(null=True, max_length=64)),
                ('lease_until', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('attempts', fields.IntField(default=0)),
                ('task_id', fields.CharField(null=True, max_length=160)),
                ('node_id', fields.CharField(null=True, max_length=64)),
                ('output', fields.JSONField(null=True, encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
                ('raw_text', fields.TextField(null=True, unique=False)),
                ('error', fields.TextField(null=True, unique=False)),
                ('usage', fields.JSONField(null=True, encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
                ('started_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('finished_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('duration_s', fields.FloatField(null=True)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
                ('updated_at', fields.DatetimeField(auto_now=True, auto_now_add=False)),
            ],
            options={'table': 'calificacion', 'app': 'local', 'unique_together': (('envio_id', 'metrica', 'modelo'),), 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='EnvioSnapshot',
            fields=[
                ('envio_id', fields.IntField(primary_key=True, generated=False, unique=True, db_index=True)),
                ('numero', fields.IntField()),
                ('timestamp', fields.DatetimeField(auto_now=False, auto_now_add=False)),
                ('avance', fields.FloatField()),
                ('estudiante_id', fields.IntField(null=True)),
                ('problema_id', fields.IntField()),
                ('estado', fields.CharField(max_length=2)),
                ('contenido', fields.TextField(unique=False)),
                ('slug', fields.CharField(max_length=50)),
            ],
            options={'table': 'envio', 'app': 'local', 'pk_attr': 'envio_id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='ProblemaSnapshot',
            fields=[
                ('problema_id', fields.IntField(primary_key=True, generated=False, unique=True, db_index=True)),
                ('titulo', fields.CharField(max_length=120)),
                ('descripcion', fields.TextField(null=True, unique=False)),
                ('funcion', fields.CharField(max_length=120)),
                ('retorno_tipo', fields.CharField(max_length=120)),
                ('retorno_descripcion', fields.TextField(null=True, unique=False)),
                ('nivel', fields.IntField()),
                ('solucion_referencia', fields.TextField(null=True, unique=False)),
            ],
            options={'table': 'problema', 'app': 'local', 'pk_attr': 'problema_id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='SyncState',
            fields=[
                ('clave', fields.CharField(primary_key=True, unique=True, db_index=True, max_length=64)),
                ('valor', fields.CharField(max_length=255)),
            ],
            options={'table': 'sync_state', 'app': 'local', 'pk_attr': 'clave'},
            bases=['Model'],
        ),
        ops.RunSQL(
            sql="CREATE INDEX calificacion_pendientes_idx "
            "ON calificacion (modelo, envio_id DESC) "
            "WHERE estado IN ('pending', 'processing')",
            reverse_sql="DROP INDEX IF EXISTS calificacion_pendientes_idx",
        ),
    ]
