"""Copia envíos y problemas de MySQL a Postgres y arma la cola de calificación."""

import argparse
import asyncio
import logging
from datetime import datetime, timezone

from tortoise import Tortoise

from src.config import TORTOISE_CONFIG, settings
from src.local_models import (
    Calificacion,
    EnvioSnapshot,
    Modelo,
    ProblemaSnapshot,
    SyncState,
)
from src.prompts import PromptSpec, aplicables, load_prompts
from src.source_models import Envio, Problema

WATERMARK = "ultimo_envio_id"
logger = logging.getLogger("ingesta")


async def _bulk(model, objs: list, size: int = 1000) -> None:
    for i in range(0, len(objs), size):
        await model.bulk_create(objs[i:i + size], ignore_conflicts=True)


async def ingest_batch(
    limit: int, specs: dict[str, PromptSpec], desde: int | None = None, hasta: int | None = None
) -> int:
    modelos = [
        modelo.modelo_id
        for modelo in await Modelo.filter(enabled=True).order_by("modelo_id")
    ]
    if not modelos:
        logger.warning("no hay modelos habilitados en la tabla modelo; no se ingiere")
        return 0

    state = await SyncState.filter(clave=WATERMARK).first()
    start = max(int(state.valor) if state else settings.sync_from_id, desde or 0)
    query = Envio.filter(id__gt=start)
    if hasta is not None:
        query = query.filter(id__lte=hasta)
    envios = await query.order_by("id").limit(limit)
    if not envios:
        return 0

    pids = {envio.problema_id for envio in envios if envio.problema_id}
    problemas = {p.id: p for p in await Problema.filter(id__in=list(pids))} if pids else {}

    if problemas:
        for p in problemas.values():
            await ProblemaSnapshot.update_or_create(
                problema_id=p.id,
                defaults={
                    "titulo": p.titulo,
                    "descripcion": p.descripcion,
                    "funcion": p.funcion,
                    "retorno_tipo": p.retorno_tipo,
                    "retorno_descripcion": p.retorno_descripcion,
                    "nivel": p.nivel,
                    "solucion_referencia": p.solucion_referencia,
                },
            )

    con_problema = [envio for envio in envios if envio.problema_id in problemas]
    await _bulk(
        EnvioSnapshot,
        [
            EnvioSnapshot(
                envio_id=envio.id,
                numero=envio.numero,
                timestamp=envio.timestamp,
                avance=envio.avance,
                estudiante_id=envio.estudiante_id,
                problema_id=envio.problema_id,
                estado=envio.estado,
                contenido=envio.contenido,
                slug=envio.slug,
            )
            for envio in con_problema
        ],
    )

    calificaciones = []
    por_nivel = {nivel: aplicables(specs, nivel) for nivel in (1, 2, 3, 4)}
    for envio in con_problema:
        nivel = problemas[envio.problema_id].nivel
        for spec in por_nivel.get(nivel, aplicables(specs, nivel)):
            restringido = settings.metricas_modelo.get(spec.codigo)
            if restringido:
                if restringido not in modelos:
                    continue
                modelos_spec = [restringido]
            else:
                modelos_spec = modelos
            for modelo in modelos_spec:
                calificaciones.append(
                    Calificacion(
                        envio_id=envio.id,
                        problema_id=envio.problema_id,
                        estudiante_id=envio.estudiante_id,
                        metrica=spec.codigo,
                        nivel=nivel,
                        modelo=modelo,
                    )
                )
    await _bulk(Calificacion, calificaciones)

    await SyncState.update_or_create(
        clave=WATERMARK, defaults={"valor": str(envios[-1].id)}
    )
    return len(envios)


async def _fecha(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def _resolve_desde(value: str | None) -> int | None:
    if not value:
        return None
    first = (
        await Envio.filter(timestamp__gte=await _fecha(value))
        .order_by("id")
        .first()
    )
    return first.id - 1 if first else None


async def _resolve_hasta(value: str | None) -> int | None:
    if not value:
        return None
    last = (
        await Envio.filter(timestamp__lte=await _fecha(value))
        .order_by("-id")
        .first()
    )
    return last.id if last else None


async def run(desde: str | None, hasta: str | None, lote: int, reiniciar: bool) -> None:
    await Tortoise.init(config=TORTOISE_CONFIG)
    try:
        if reiniciar:
            await SyncState.filter(clave=WATERMARK).delete()
        specs = load_prompts(settings.prompts_path)
        start = await _resolve_desde(desde)
        end = await _resolve_hasta(hasta)
        total = 0
        while True:
            n = await ingest_batch(lote, specs, desde=start, hasta=end)
            total += n
            print(f"ingeridos {total} envíos", flush=True)
            start = None
            if n == 0:
                break
        print(f"listo: {total} envíos", flush=True)
    finally:
        await Tortoise.close_connections()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--desde", help="fecha ISO, por ejemplo 2026-01-01")
    parser.add_argument("--hasta", help="fecha ISO")
    parser.add_argument("--lote", type=int, default=500)
    parser.add_argument(
        "--reiniciar",
        action="store_true",
        help="relee desde el primer envío (útil al agregar un modelo nuevo)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.desde, args.hasta, args.lote, args.reiniciar))


if __name__ == "__main__":
    main()
