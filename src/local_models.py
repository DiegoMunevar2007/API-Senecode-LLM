"""Base nueva: snapshot de envíos/problemas, cola de trabajo y respuestas."""

from tortoise import fields
from tortoise.models import Model


class EnvioSnapshot(Model):
    envio_id = fields.IntField(primary_key=True, generated=False)
    numero = fields.IntField()
    timestamp = fields.DatetimeField()
    avance = fields.FloatField()
    estudiante_id = fields.IntField(null=True)
    problema_id = fields.IntField()
    estado = fields.CharField(max_length=2)
    contenido = fields.TextField()
    slug = fields.CharField(max_length=50)

    class Meta:
        table = "envio"


class ProblemaSnapshot(Model):
    problema_id = fields.IntField(primary_key=True, generated=False)
    titulo = fields.CharField(max_length=120)
    descripcion = fields.TextField(null=True)
    funcion = fields.CharField(max_length=120)
    retorno_tipo = fields.CharField(max_length=120)
    retorno_descripcion = fields.TextField(null=True)
    nivel = fields.IntField()
    solucion_referencia = fields.TextField(null=True)

    class Meta:
        table = "problema"


class Calificacion(Model):
    id = fields.BigIntField(primary_key=True)
    envio_id = fields.IntField()
    problema_id = fields.IntField(null=True)
    estudiante_id = fields.IntField(null=True)
    metrica = fields.CharField(max_length=8)
    nivel = fields.IntField()
    modelo = fields.CharField(max_length=64)
    estado = fields.CharField(max_length=16, default="pending")
    worker_id = fields.CharField(max_length=64, null=True)
    lease_until = fields.DatetimeField(null=True)
    attempts = fields.IntField(default=0)
    task_id = fields.CharField(max_length=160, null=True)
    node_id = fields.CharField(max_length=64, null=True)
    output = fields.JSONField(null=True)
    raw_text = fields.TextField(null=True)
    error = fields.TextField(null=True)
    usage = fields.JSONField(null=True)
    started_at = fields.DatetimeField(null=True)
    finished_at = fields.DatetimeField(null=True)
    duration_s = fields.FloatField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "calificacion"
        # shortcut: un resultado por (envío, métrica, modelo); para comparar
        # prompts o params de un mismo modelo falta una columna de experimento.
        unique_together = (("envio_id", "metrica", "modelo"),)


class SyncState(Model):
    clave = fields.CharField(max_length=64, primary_key=True)
    valor = fields.CharField(max_length=255)

    class Meta:
        table = "sync_state"


class Modelo(Model):
    modelo_id = fields.CharField(max_length=64, primary_key=True)
    filename = fields.CharField(max_length=255, unique=True)
    sha256 = fields.CharField(max_length=64)
    size_bytes = fields.BigIntField()
    download_url = fields.TextField()
    context_size = fields.IntField(default=8192)
    parallel = fields.IntField(default=1)
    extra_args = fields.JSONField(default=list)
    enabled = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "modelo"


class NodoModelo(Model):
    node_id = fields.CharField(max_length=64, primary_key=True)
    modelo_id = fields.CharField(max_length=64)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "nodo_modelo"


class NodoEstado(Model):
    node_id = fields.CharField(max_length=64, primary_key=True)
    state = fields.CharField(max_length=32, null=True)
    agent_version = fields.CharField(max_length=32, null=True)
    loaded_model = fields.CharField(max_length=64, null=True)
    slots_total = fields.IntField(default=0)
    slots_busy = fields.IntField(default=0)
    last_error = fields.TextField(null=True)
    last_seen = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "nodo_estado"


