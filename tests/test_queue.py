import asyncio
import os
import subprocess
import sys
from datetime import datetime

import pytest
import pytest_asyncio
from tortoise import Tortoise

from src import queue
from src.local_models import (
    Calificacion,
    EnvioSnapshot,
    Modelo,
    NodoEstado,
    NodoModelo,
    SyncState,
)
from src.modelos import elegir_modelo, esta_pausado

DB_URL = os.environ.get("TEST_LOCAL_DB_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="define TEST_LOCAL_DB_URL")


@pytest_asyncio.fixture(autouse=True)
async def db():
    subprocess.run(
        [sys.executable, "-m", "tortoise", "-c", "src.config.MIGRATIONS_CONFIG", "migrate", "local"],
        check=True,
        env={**os.environ, "LOCAL_DB_URL": DB_URL},
        capture_output=True,
    )
    await Tortoise.init(
        config={
            # Dos conexiones como en producción: el claim debe pedir "default".
            "connections": {"default": DB_URL, "source": DB_URL},
            "apps": {
                "local": {
                    "models": ["src.local_models"],
                    "default_connection": "default",
                }
            },
        }
    )
    yield
    await Tortoise.close_connections()


@pytest_asyncio.fixture(autouse=True)
async def limpio(db):
    modelos = (Calificacion, EnvioSnapshot, NodoEstado, NodoModelo, Modelo, SyncState)
    for model in modelos:
        await model.all().delete()
    yield
    for model in modelos:
        await model.all().delete()


async def _envio(envio_id: int = 1) -> None:
    await EnvioSnapshot.create(
        envio_id=envio_id,
        numero=1,
        timestamp=datetime(2026, 1, 1),
        avance=100,
        estudiante_id=7,
        problema_id=3,
        estado="OK",
        contenido="x = 1",
        slug="s",
    )


async def _calificacion(envio_id: int, metrica: str, modelo: str) -> None:
    await Calificacion.create(
        envio_id=envio_id, metrica=metrica, nivel=1, modelo=modelo
    )


async def test_claim_concurrente_no_repite():
    await _envio()
    for i in range(30):
        await _calificacion(1, f"M{i:02d}", "m")
    filas = await asyncio.gather(*[queue.claim("m", f"n{i}", 60) for i in range(30)])
    ids = [fila["id"] for fila in filas if fila]
    assert len(ids) == 30
    assert len(set(ids)) == 30
    assert await queue.claim("m", "n99", 60) is None


async def test_resultado_duplicado_gana_el_primero():
    await _envio()
    await _calificacion(1, "M01", "m")
    fila = await queue.claim("m", "n1", 60)
    task_id = f"{fila['envio_id']}:{fila['metrica']}:{fila['modelo']}"
    base = {"node_id": "n1", "status": "ok", "output": {"veredicto": "NO"}}
    assert await queue.complete(task_id, base, 3) is False
    assert await queue.complete(task_id, {**base, "output": {"veredicto": "SI"}}, 3) is True
    row = await Calificacion.get(envio_id=1)
    assert row.estado == "done"
    assert row.output == {"veredicto": "NO"}
    # Un error posterior de un nodo que perdió el lease no debe bajar el estado.
    assert await queue.complete(
        task_id, {"node_id": "n2", "status": "error", "error": "late"}, 3
    ) is True
    assert (await Calificacion.get(envio_id=1)).estado == "done"


async def test_complete_concurrente_solo_un_ok():
    await _envio()
    await _calificacion(1, "M01", "m")
    fila = await queue.claim("m", "n1", 60)
    task_id = f"{fila['envio_id']}:{fila['metrica']}:{fila['modelo']}"
    resultados = await asyncio.gather(
        queue.complete(task_id, {"status": "ok", "output": {"veredicto": "SI"}}, 3),
        queue.complete(task_id, {"status": "ok", "output": {"veredicto": "NO"}}, 3),
    )
    assert sorted(resultados) == [False, True]
    row = await Calificacion.get(envio_id=1)
    assert row.estado == "done"
    assert row.output in ({"veredicto": "SI"}, {"veredicto": "NO"})


async def test_cada_modelo_guarda_su_resultado():
    await _envio()
    await _calificacion(1, "M01", "modelo-a")
    await _calificacion(1, "M01", "modelo-b")
    for modelo in ("modelo-a", "modelo-b"):
        fila = await queue.claim(modelo, "n1", 60)
        task_id = f"{fila['envio_id']}:{fila['metrica']}:{fila['modelo']}"
        await queue.complete(task_id, {"node_id": "n1", "status": "ok", "output": {}}, 3)
    done = await Calificacion.filter(estado="done")
    assert len(done) == 2
    assert {row.modelo for row in done} == {"modelo-a", "modelo-b"}


async def test_error_requeue_y_muerte():
    await _envio()
    await _calificacion(1, "M01", "m")
    fila = await queue.claim("m", "n1", 60)
    task_id = f"{fila['envio_id']}:{fila['metrica']}:{fila['modelo']}"
    await queue.complete(task_id, {"node_id": "n1", "status": "error", "error": "boom"}, 3)
    row = await Calificacion.get(envio_id=1)
    assert row.estado == "pending"
    assert row.error == "boom"
    for _ in range(2):
        await queue.claim("m", "n1", 60)
        await queue.complete(task_id, {"node_id": "n1", "status": "error", "error": "boom"}, 3)
    assert (await Calificacion.get(envio_id=1)).estado == "dead"


async def test_ok_actualiza_un_error_previo():
    await _envio()
    await _calificacion(1, "M01", "m")
    fila = await queue.claim("m", "n1", 60)
    task_id = f"{fila['envio_id']}:{fila['metrica']}:{fila['modelo']}"
    await queue.complete(task_id, {"node_id": "n1", "status": "error", "error": "boom"}, 3)
    await queue.claim("m", "n1", 60)
    duplicate = await queue.complete(
        task_id, {"node_id": "n1", "status": "ok", "output": {"veredicto": "SI"}}, 3
    )
    assert duplicate is False
    row = await Calificacion.get(envio_id=1)
    assert row.estado == "done"
    assert row.output == {"veredicto": "SI"}
    assert row.error is None


async def test_heartbeat_elige_modelo():
    for modelo_id in ("modelo-a", "modelo-b"):
        await Modelo.create(
            modelo_id=modelo_id,
            filename=f"{modelo_id}.llamafile",
            sha256="x" * 64,
            size_bytes=1,
            download_url=f"https://example.com/{modelo_id}",
            context_size=8192,
            parallel=1,
            extra_args=[],
        )

    await _calificacion(1, "M01", "modelo-a")
    await _calificacion(2, "M01", "modelo-b")

    # Modelo cargado con trabajo: se mantiene.
    assert (await elegir_modelo("nodo-1", {"id": "modelo-a"})).modelo_id == "modelo-a"

    # Reparto: dos nodos activos en modelo-a, el nuevo va a modelo-b.
    await NodoEstado.create(node_id="n1", loaded_model="modelo-a", state="ready")
    await NodoEstado.create(node_id="n2", loaded_model="modelo-a", state="ready")
    assert (await elegir_modelo("nodo-3")).modelo_id == "modelo-b"

    # Pin manual: se respeta aunque no tenga trabajo.
    await NodoModelo.create(node_id="nodo-3", modelo_id="modelo-b")
    assert (await elegir_modelo("nodo-3")).modelo_id == "modelo-b"

    # Sin trabajo en ningún modelo: se libera la GPU.
    await Calificacion.all().delete()
    assert await elegir_modelo("nodo-4") is None

    assert await esta_pausado() is False
    await SyncState.create(clave="pausado", valor="true")
    assert await esta_pausado() is True
