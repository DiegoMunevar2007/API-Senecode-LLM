"""Selección automática del modelo que el master asigna a cada nodo."""

import zlib
from datetime import datetime, timedelta, timezone

from tortoise.functions import Count

from src.config import settings
from src.local_models import Calificacion, Modelo, NodoEstado, NodoModelo, SyncState

PAUSA = "pausado"
TRABAJO = ("pending", "processing")


async def esta_pausado() -> bool:
    row = await SyncState.filter(clave=PAUSA).first()
    return bool(row and row.valor.lower() == "true")


async def tiene_trabajo(modelo_id: str) -> bool:
    return await Calificacion.filter(modelo=modelo_id, estado__in=TRABAJO).exists()


async def _habilitados() -> list[Modelo]:
    return await Modelo.filter(enabled=True).order_by("modelo_id")


async def _nodos_por_modelo() -> dict[str, int]:
    """Nodos activos por modelo; los modelos no son prioridades, se reparten."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.heartbeat_timeout_s)
    filas = await (
        NodoEstado.filter(last_seen__gte=cutoff, loaded_model__isnull=False)
        .group_by("loaded_model")
        .annotate(n=Count("node_id"))
        .values("loaded_model", "n")
    )
    return {fila["loaded_model"]: fila["n"] for fila in filas}


async def elegir_modelo(node_id: str, loaded: dict | None = None) -> Modelo | None:
    """Pin > modelo cargado con trabajo > lote por creación o menos nodos."""
    pin = await NodoModelo.filter(node_id=node_id).first()
    if pin:
        modelo = await Modelo.filter(modelo_id=pin.modelo_id, enabled=True).first()
        if modelo:
            return modelo

    cargado = None
    if loaded and loaded.get("id"):
        cargado = await Modelo.filter(modelo_id=loaded["id"], enabled=True).first()

    if not settings.auto_reasignar:
        if cargado:
            return cargado
        modelos = await _habilitados()
        return modelos[0] if modelos else None

    if cargado and await tiene_trabajo(cargado.modelo_id):
        return cargado

    if settings.batch_por_modelo:
        # Lote: todos los nodos al primer modelo con trabajo, en orden de creación.
        for modelo in await Modelo.filter(enabled=True).order_by("created_at", "modelo_id"):
            if await tiene_trabajo(modelo.modelo_id):
                return modelo
        if settings.mantener_modelo_sin_trabajo:
            if cargado:
                return cargado
            modelos = await Modelo.filter(enabled=True).order_by("created_at", "modelo_id")
            return modelos[0] if modelos else None
        return None

    candidatos = [
        modelo for modelo in await _habilitados()
        if await tiene_trabajo(modelo.modelo_id)
    ]
    if candidatos:
        nodos = await _nodos_por_modelo()
        # Menos nodos primero; empate repartido por nodo con un hash estable.
        return min(
            candidatos,
            key=lambda m: (
                nodos.get(m.modelo_id, 0),
                zlib.crc32(f"{node_id}:{m.modelo_id}".encode()),
            ),
        )

    if settings.mantener_modelo_sin_trabajo:
        if cargado:
            return cargado
        modelos = await _habilitados()
        return modelos[0] if modelos else None
    return None
