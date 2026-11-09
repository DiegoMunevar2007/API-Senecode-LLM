"""Tablas del calificador original. Solo lectura."""

from tortoise import fields
from tortoise.models import Model


class Problema(Model):
    id = fields.IntField(primary_key=True)
    titulo = fields.CharField(max_length=120)
    descripcion = fields.TextField(null=True)
    funcion = fields.CharField(max_length=120)
    retorno_tipo = fields.CharField(max_length=120)
    retorno_descripcion = fields.TextField(null=True)
    nivel = fields.IntField()
    solucion_referencia = fields.TextField(null=True)

    class Meta:
        table = "dashboard_problema"


class Envio(Model):
    id = fields.IntField(primary_key=True)
    numero = fields.IntField()
    timestamp = fields.DatetimeField()
    avance = fields.FloatField()
    estudiante_id = fields.IntField(null=True)
    problema_id = fields.IntField(null=True)
    estado = fields.CharField(max_length=2)
    contenido = fields.TextField()
    slug = fields.CharField(max_length=50)

    class Meta:
        table = "dashboard_envio"
