from tortoise import migrations
from tortoise.migrations import operations as ops
import functools
from json import dumps, loads
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('local', '0001_initial')]

    initial = False

    operations = [
        ops.CreateModel(
            name='Modelo',
            fields=[
                ('modelo_id', fields.CharField(primary_key=True, unique=True, db_index=True, max_length=64)),
                ('filename', fields.CharField(unique=True, max_length=255)),
                ('sha256', fields.CharField(max_length=64)),
                ('size_bytes', fields.BigIntField()),
                ('download_url', fields.TextField(unique=False)),
                ('context_size', fields.IntField(default=8192)),
                ('parallel', fields.IntField(default=1)),
                ('extra_args', fields.JSONField(default=list, encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
                ('enabled', fields.BooleanField(default=True)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
                ('updated_at', fields.DatetimeField(auto_now=True, auto_now_add=False)),
            ],
            options={'table': 'modelo', 'app': 'local', 'pk_attr': 'modelo_id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='NodoModelo',
            fields=[
                ('node_id', fields.CharField(primary_key=True, unique=True, db_index=True, max_length=64)),
                ('modelo_id', fields.CharField(max_length=64)),
                ('updated_at', fields.DatetimeField(auto_now=True, auto_now_add=False)),
            ],
            options={'table': 'nodo_modelo', 'app': 'local', 'pk_attr': 'node_id'},
            bases=['Model'],
        ),
    ]
