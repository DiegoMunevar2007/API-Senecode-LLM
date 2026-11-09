from tortoise import migrations
from tortoise.migrations import operations as ops

class Migration(migrations.Migration):
    dependencies = [('local', '0003_auto_20261108_2159')]

    initial = False

    operations = [
        ops.RemoveField(model_name='Modelo', name='prioridad'),
    ]
