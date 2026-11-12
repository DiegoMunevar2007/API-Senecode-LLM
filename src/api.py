import asyncio
import hmac
import logging
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from tortoise.contrib.fastapi import RegisterTortoise
from tortoise.functions import Count

from src.config import TORTOISE_CONFIG, settings
from src.ingest import ingest_batch
from src.local_models import (
    Calificacion,
    EnvioSnapshot,
    Modelo,
    NodoEstado,
    ProblemaSnapshot,
)
from src.modelos import elegir_modelo, esta_pausado
from src.prompts import Message, build_contexto, load_prompts, render, response_schema
from src.queue import TaskNotFound, claim, complete, mark_dead
from src.source_models import Envio

logger = logging.getLogger(__name__)


class TaskRequest(BaseModel):
    node_id: str = Field(description="Identificador único del nodo (hostname del PC).")
    model_id: str = Field(description="Modelo que el nodo tiene cargado, tal como lo devolvió el heartbeat.")


class TaskParams(BaseModel):
    temperature: float = Field(default=0, description="Temperatura de muestreo. 0 para respuestas deterministas.")
    seed: int = Field(default=42, description="Semilla para reproducibilidad.")
    max_tokens: int = Field(default=2048, ge=1, description="Máximo de tokens de salida del modelo.")


class TaskResponse(BaseModel):
    task_id: str = Field(description="Identificador de la tarea con formato envio:metrica:modelo.")
    model_id: str = Field(description="Modelo con el que debe calificarse la tarea.")
    messages: list[Message] = Field(description="Mensajes en formato chat de OpenAI para el agente.")
    params: TaskParams = Field(description="Parámetros de muestreo para llamafile/llama.cpp.")
    response_schema: dict = Field(description="JSON Schema que el agente usa para forzar y validar la salida.")
    lease_seconds: int = Field(description="Segundos de lease; si el master no recibe el resultado, la tarea vuelve a la cola.")


class ModelInfo(BaseModel):
    id: str = Field(description="Identificador del modelo que generó la respuesta.")
    sha256: str | None = Field(default=None, description="SHA-256 del archivo del modelo, si el agente lo conoce.")


class ResultRequest(BaseModel):
    node_id: str = Field(description="Nodo que entrega el resultado.")
    status: Literal["ok", "error"] = Field(description="ok si la calificación fue válida; error si falló la inferencia o el JSON.")
    output: dict | None = Field(default=None, description="JSON validado contra response_schema. Obligatorio cuando status=ok.")
    raw_text: str | None = Field(default=None, description="Texto crudo del modelo, útil para diagnóstico.")
    error: str | None = Field(default=None, description="Descripción del error cuando status=error.")
    attempts: int = Field(default=1, ge=0, description="Intentos de inferencia que hizo el agente.")
    usage: dict | None = Field(default=None, description="Conteo de tokens devuelto por el servidor de inferencia.")
    model: ModelInfo | None = Field(default=None, description="Modelo utilizado por el agente.")
    started_at: datetime | None = Field(default=None, description="Inicio de la inferencia en ISO 8601 UTC.")
    finished_at: datetime | None = Field(default=None, description="Fin de la inferencia en ISO 8601 UTC.")
    duration_s: float | None = Field(default=None, ge=0, description="Duración de la inferencia en segundos.")


class ResultResponse(BaseModel):
    accepted: bool = Field(description="True si el resultado fue aceptado o ya existía.")
    duplicate: bool = Field(description="True si ya había un resultado ok para esa tarea y modelo.")


class HealthResponse(BaseModel):
    status: str = Field(description="ok si Postgres responde; degraded si no.")
    postgres: bool = Field(description="Conexión con la base nueva (cola y resultados).")
    mysql: bool = Field(description="Conexión con la base original de envíos (solo lectura).")


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    node_id: str = Field(description="Identificador único del nodo.")
    state: str | None = Field(default=None, description="Estado del agente: starting, idle, loading, ready, busy, paused o error.")
    agent_version: str | None = Field(default=None, description="Versión del agente que reporta el nodo.")
    loaded_model: dict | None = Field(default=None, description="Modelo cargado: {id, sha256}. null si no hay ninguno.")
    slots_total: int = Field(default=0, ge=0, description="Slots de inferencia que el agente tiene configurados.")
    slots_busy: int = Field(default=0, ge=0, description="Slots ocupados en este momento.")
    last_error: str | None = Field(default=None, description="Último error reportado por el nodo, si hay.")


class DesiredModel(BaseModel):
    id: str = Field(description="Identificador del modelo que el nodo debe cargar.")
    filename: str = Field(description="Nombre exacto del archivo que debe existir en models_dir.")
    sha256: str = Field(description="SHA-256 esperado del archivo; el agente lo verifica.")
    size_bytes: int = Field(description="Tamaño esperado en bytes.")
    download_url: str = Field(description="URL absoluta de descarga; actualmente el link directo de Hugging Face.")
    context_size: int = Field(description="Contexto por slot. El agente solicita context_size * parallel.")
    parallel: int = Field(description="Slots de inferencia que debe abrir llamafile.")
    extra_args: list[str] = Field(default_factory=list, description="Argumentos adicionales para llamafile.")


class HeartbeatResponse(BaseModel):
    desired_model: DesiredModel | None = Field(description="Modelo que el nodo debe cargar; null para liberar la GPU.")
    paused: bool = Field(description="Si es true, el nodo debe descargar el modelo y quedar idle.")
    heartbeat_interval_s: int = Field(description="Segundos hasta el próximo heartbeat.")


class ModeloStats(BaseModel):
    modelo: str = Field(description="Identificador del modelo.")
    enabled: bool = Field(description="Si el modelo está habilitado para recibir tareas.")
    pending: int = Field(default=0, description="Tareas en espera.")
    processing: int = Field(default=0, description="Tareas reclamadas por un nodo.")
    done: int = Field(default=0, description="Tareas con resultado ok.")
    dead: int = Field(default=0, description="Tareas que agotaron los reintentos.")


class NodoStats(BaseModel):
    node_id: str = Field(description="Identificador del nodo.")
    state: str | None = Field(description="Último estado reportado por el agente.")
    agent_version: str | None = Field(description="Versión del agente.")
    loaded_model: str | None = Field(description="Modelo que el nodo reporta cargado.")
    slots_total: int = Field(description="Slots configurados.")
    slots_busy: int = Field(description="Slots ocupados.")
    last_error: str | None = Field(description="Último error reportado.")
    last_seen: datetime | None = Field(description="Último heartbeat recibido.")


class StatsResponse(BaseModel):
    modelos: list[ModeloStats] = Field(description="Estado de cada modelo registrado.")
    nodos: list[NodoStats] = Field(description="Último estado conocido de cada nodo.")


bearer = HTTPBearer(
    auto_error=False,
    description="Token compartido del master. Enviar como `Authorization: Bearer <token>`.",
)


async def auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    if (
        not settings.api_token
        or credentials is None
        or credentials.scheme.lower() != "bearer"
        or not hmac.compare_digest(credentials.credentials, settings.api_token)
    ):
        raise HTTPException(
            status_code=401,
            detail="unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def _poll(app: FastAPI) -> None:
    while True:
        try:
            await ingest_batch(settings.sync_batch, app.state.specs)
        except Exception:
            logger.exception("falló la ingesta incremental")
        await asyncio.sleep(settings.sync_interval_s)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with RegisterTortoise(app, config=TORTOISE_CONFIG):
        app.state.specs = load_prompts(settings.prompts_path)
        poller = asyncio.create_task(_poll(app)) if settings.sync_enabled else None
        try:
            yield
        finally:
            if poller:
                poller.cancel()
                with suppress(asyncio.CancelledError):
                    await poller


tags_metadata = [
    {"name": "Salud", "description": "Estado de las conexiones con Postgres y MySQL."},
    {"name": "Nodos", "description": "Heartbeat y asignación del modelo que debe cargar cada nodo."},
    {"name": "Modelos", "description": "Descarga y redirección de los archivos de modelos."},
    {"name": "Tareas", "description": "Cola de calificación: reclamar tareas y entregar resultados."},
    {"name": "Administración", "description": "Estado agregado de la cola y de los nodos."},
]

app = FastAPI(
    title="Calificador LLM",
    version="0.1.0",
    description=(
        "Master que entrega tareas de calificación a nodos con GPU, "
        "asigna modelos y guarda los resultados en Postgres."
    ),
    lifespan=lifespan,
    openapi_tags=tags_metadata,
)


@app.get(
    "/api/v1/health",
    response_model=HealthResponse,
    tags=["Salud"],
    summary="Estado de las conexiones",
    response_description="Estado agregado y conectividad de cada base.",
)
async def health():
    """
    Entrada: ninguna.

    Salida (`HealthResponse`):
    - `status`: `ok` si Postgres responde; `degraded` si no.
    - `postgres`: conectividad con la base nueva (cola y resultados).
    - `mysql`: conectividad con la base original de envíos.
    """
    estado = {"postgres": False, "mysql": False}
    try:
        await EnvioSnapshot.all().exists()
        estado["postgres"] = True
    except Exception:
        logger.warning("sin conexión a postgres")
    try:
        await Envio.all().exists()
        estado["mysql"] = True
    except Exception:
        logger.warning("sin conexión a mysql")
    return HealthResponse(
        status="ok" if estado["postgres"] else "degraded", **estado
    )


@app.post(
    "/api/v1/heartbeat",
    response_model=HeartbeatResponse,
    dependencies=[Depends(auth)],
    tags=["Nodos"],
    summary="Heartbeat y asignación de modelo",
    response_description="Modelo deseado, pausa e intervalo del próximo heartbeat.",
)
async def heartbeat(req: HeartbeatRequest):
    """
    Entrada (`HeartbeatRequest`):
    - `node_id`: identificador único del nodo.
    - `state`: `starting`, `idle`, `loading`, `ready`, `busy`, `paused` o `error`.
    - `agent_version`: versión del agente.
    - `loaded_model`: `{id, sha256}` del modelo cargado, o `null`.
    - `slots_total`, `slots_busy`: slots de inferencia configurados y ocupados.
    - `last_error`: último error reportado por el nodo.

    Salida (`HeartbeatResponse`):
    - `desired_model` (`DesiredModel` o `null`): `id`, `filename`, `sha256`,
      `size_bytes`, `download_url` (link directo de Hugging Face), `context_size`,
      `parallel`, `extra_args`.
    - `paused`: `true` si el nodo debe descargar el modelo y liberar la GPU.
    - `heartbeat_interval_s`: segundos hasta el próximo heartbeat.
    """
    await NodoEstado.update_or_create(
        node_id=req.node_id,
        defaults={
            "state": req.state,
            "agent_version": req.agent_version,
            "loaded_model": (req.loaded_model or {}).get("id"),
            "slots_total": req.slots_total,
            "slots_busy": req.slots_busy,
            "last_error": req.last_error,
        },
    )
    paused = await esta_pausado()
    modelo = None if paused else await elegir_modelo(req.node_id, req.loaded_model)
    desired = None
    if modelo:
        desired = DesiredModel(
            id=modelo.modelo_id,
            filename=modelo.filename,
            sha256=modelo.sha256,
            size_bytes=modelo.size_bytes,
            download_url=modelo.download_url,
            context_size=modelo.context_size,
            parallel=modelo.parallel,
            extra_args=modelo.extra_args or [],
        )
    return HeartbeatResponse(
        desired_model=desired,
        paused=paused,
        heartbeat_interval_s=settings.heartbeat_interval_s,
    )


@app.get(
    "/api/v1/models/{filename}",
    dependencies=[Depends(auth)],
    tags=["Modelos"],
    summary="Redirigir al archivo del modelo",
    response_description="Redirección al archivo o 404 si el modelo no está registrado.",
    responses={
        307: {"description": "Redirección al archivo del modelo."},
        404: {"description": "Modelo no registrado o deshabilitado."},
    },
)
async def model_file(
    filename: Annotated[
        str,
        Path(description="Nombre exacto del archivo, por ejemplo gpt-oss-20b-Q5_K_S.llamafile."),
    ],
):
    """
    Entrada (path):
    - `filename`: nombre exacto del archivo, por ejemplo `gpt-oss-20b-Q5_K_S.llamafile`.

    Salida:
    - `307`: redirección al link de Hugging Face guardado en `modelo.download_url`.
    - `404`: el modelo no existe o está deshabilitado.
    """
    modelo = await Modelo.filter(filename=filename, enabled=True).first()
    if not modelo:
        raise HTTPException(status_code=404, detail="model not found")
    # El agente sigue el redirect; requests quita el Bearer al cambiar de host.
    return RedirectResponse(modelo.download_url, status_code=307)


@app.get(
    "/api/v1/stats",
    response_model=StatsResponse,
    dependencies=[Depends(auth)],
    tags=["Administración"],
    summary="Estado de la cola y de los nodos",
    response_description="Resumen por modelo y por nodo.",
)
async def stats():
    """
    Entrada: ninguna.

    Salida (`StatsResponse`):
    - `modelos` (`list[ModeloStats]`): `modelo`, `enabled`, `pending`, `processing`,
      `done`, `dead`.
    - `nodos` (`list[NodoStats]`): `node_id`, `state`, `agent_version`,
      `loaded_model`, `slots_total`, `slots_busy`, `last_error`, `last_seen`.
    """
    filas = await (
        Calificacion.all()
        .group_by("modelo", "estado")
        .annotate(n=Count("id"))
        .values("modelo", "estado", "n")
    )
    conteos: dict[str, dict[str, int]] = {}
    for fila in filas:
        conteos.setdefault(fila["modelo"], {})[fila["estado"]] = fila["n"]

    modelos = await Modelo.all().order_by("modelo_id")
    resumen = [
        ModeloStats(
            modelo=m.modelo_id,
            enabled=m.enabled,
            pending=conteos.get(m.modelo_id, {}).get("pending", 0),
            processing=conteos.get(m.modelo_id, {}).get("processing", 0),
            done=conteos.get(m.modelo_id, {}).get("done", 0),
            dead=conteos.get(m.modelo_id, {}).get("dead", 0),
        )
        for m in modelos
    ]
    nodos = [
        NodoStats(
            node_id=n.node_id,
            state=n.state,
            agent_version=n.agent_version,
            loaded_model=n.loaded_model,
            slots_total=n.slots_total,
            slots_busy=n.slots_busy,
            last_error=n.last_error,
            last_seen=n.last_seen,
        )
        for n in await NodoEstado.all().order_by("node_id")
    ]
    return StatsResponse(modelos=resumen, nodos=nodos)


@app.post(
    "/api/v1/tasks/next",
    response_model=TaskResponse,
    dependencies=[Depends(auth)],
    tags=["Tareas"],
    summary="Reclamar la siguiente tarea",
    response_description="Tarea con mensajes y esquema, o 204 sin trabajo.",
    responses={
        204: {"description": "No hay tareas pendientes para el modelo solicitado."},
    },
)
async def next_task(req: TaskRequest, request: Request):
    """
    Entrada (`TaskRequest`):
    - `node_id`: nodo que pide trabajo.
    - `model_id`: modelo cargado; el master solo entrega tareas de ese modelo.

    Salida 200 (`TaskResponse`):
    - `task_id`: identificador con formato `envio:metrica:modelo`.
    - `model_id`: modelo con el que se debe calificar.
    - `messages` (`list[Message]`): `role` y `content` para el chat.
    - `params` (`TaskParams`): `temperature`, `seed`, `max_tokens`.
    - `response_schema`: JSON Schema para forzar y validar la salida.
    - `lease_seconds`: segundos de lease de la tarea.

    Salida 204: sin cuerpo; no hay tareas pendientes para ese modelo.
    """
    row = await claim(req.model_id, req.node_id, settings.lease_seconds)
    if row is None:
        return Response(status_code=204)

    envio = await EnvioSnapshot.filter(envio_id=row["envio_id"]).first()
    problema = (
        await ProblemaSnapshot.filter(problema_id=envio.problema_id).first()
        if envio
        else None
    )
    spec = request.app.state.specs.get(row["metrica"])
    if not envio or not problema or not spec:
        await mark_dead(
            row["envio_id"], row["metrica"], row["modelo"], "snapshot o métrica ausente"
        )
        return Response(status_code=204)

    return TaskResponse(
        task_id=f"{envio.envio_id}:{spec.codigo}:{row['modelo']}",
        model_id=row["modelo"],
        messages=render(spec, build_contexto(problema, spec.codigo), envio.contenido),
        params=TaskParams(),
        response_schema=response_schema(spec.forma),
        lease_seconds=settings.lease_seconds,
    )


@app.post(
    "/api/v1/tasks/{task_id}/result",
    response_model=ResultResponse,
    dependencies=[Depends(auth)],
    tags=["Tareas"],
    summary="Entregar el resultado de una tarea",
    response_description="Resultado aceptado y si era duplicado.",
    responses={
        404: {"description": "task_id desconocido o tarea eliminada."},
        422: {"description": "Payload inválido."},
    },
)
async def task_result(
    task_id: Annotated[
        str,
        Path(description="Identificador con formato envio:metrica:modelo."),
    ],
    payload: ResultRequest,
):
    """
    Entrada (path):
    - `task_id`: identificador con formato `envio:metrica:modelo`.

    Entrada (`ResultRequest`):
    - `node_id`: nodo que entrega el resultado.
    - `status`: `ok` si la calificación fue válida; `error` si falló.
    - `output`: JSON validado contra `response_schema`; obligatorio si `status=ok`.
    - `raw_text`: texto crudo del modelo.
    - `error`: descripción del error si `status=error`.
    - `attempts`: intentos de inferencia del agente.
    - `usage`: conteo de tokens del servidor de inferencia.
    - `model` (`ModelInfo`): `id` y `sha256` del modelo usado.
    - `started_at`, `finished_at`, `duration_s`: tiempos de la inferencia.

    Salida 200 (`ResultResponse`):
    - `accepted`: `true` si se procesó.
    - `duplicate`: `true` si ya existía un `ok` para esa tarea y modelo.

    Salida 404: `task_id` desconocido. Salida 422: payload inválido.
    """
    try:
        duplicate = await complete(task_id, payload.model_dump(), settings.max_attempts)
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="task not found")
    return ResultResponse(accepted=True, duplicate=duplicate)
