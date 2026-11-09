from datetime import datetime, timedelta, timezone

from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from src.local_models import Calificacion


class TaskNotFound(Exception):
    pass


async def claim(modelo: str, worker_id: str, lease_seconds: int) -> dict | None:
    """Reclama una tarea con lock de fila. La transacción dura milisegundos."""
    async with in_transaction("default") as conn:
        ahora = datetime.now(timezone.utc)
        row = await (
            Calificacion.filter(
                Q(modelo=modelo)
                & (
                    Q(estado="pending")
                    | (Q(estado="processing") & Q(lease_until__lt=ahora))
                )
            )
            .order_by("-envio_id", "metrica")
            .select_for_update(skip_locked=True)
            .using_db(conn)
            .first()
        )
        if row is None:
            return None

        row.estado = "processing"
        row.worker_id = worker_id
        row.lease_until = ahora + timedelta(seconds=lease_seconds)
        row.attempts += 1
        row.updated_at = ahora
        await row.save(
            using_db=conn,
            update_fields=["estado", "worker_id", "lease_until", "attempts", "updated_at"],
        )
        return {
            "id": row.id,
            "envio_id": row.envio_id,
            "metrica": row.metrica,
            "nivel": row.nivel,
            "modelo": row.modelo,
            "attempts": row.attempts,
        }


async def mark_dead(envio_id: int, metrica: str, modelo: str, error: str) -> None:
    await Calificacion.filter(envio_id=envio_id, metrica=metrica, modelo=modelo).update(
        estado="dead",
        error=error,
        lease_until=None,
        updated_at=datetime.now(timezone.utc),
    )


async def complete(task_id: str, payload: dict, max_attempts: int) -> bool:
    """Guarda el resultado. Devuelve True si ya existía un 'ok' (duplicado)."""
    try:
        envio_id_text, metrica, modelo = task_id.split(":", 2)
        envio_id = int(envio_id_text)
    except ValueError as exc:
        raise TaskNotFound(task_id) from exc

    row = await Calificacion.filter(
        envio_id=envio_id, metrica=metrica, modelo=modelo
    ).first()
    if row is None:
        raise TaskNotFound(task_id)

    ok = payload["status"] == "ok"
    if ok:
        estado = "done"
    elif row.attempts < max_attempts:
        estado = "pending"
    else:
        estado = "dead"

    valores = {
        "task_id": task_id,
        "node_id": payload.get("node_id"),
        "output": payload.get("output") if ok else None,
        "raw_text": payload.get("raw_text"),
        "error": None if ok else payload.get("error"),
        "usage": payload.get("usage"),
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "duration_s": payload.get("duration_s"),
        "estado": estado,
        "lease_until": None,
        "updated_at": datetime.now(timezone.utc),
    }
    # El primer ok gana: un resultado posterior no puede pisar una fila done.
    afectadas = await Calificacion.filter(
        envio_id=envio_id, metrica=metrica, modelo=modelo
    ).exclude(estado="done").update(**valores)
    return not afectadas
