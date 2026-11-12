import asyncio
import hmac
import logging
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict
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
    node_id: str
    model_id: str


class TaskParams(BaseModel):
    temperature: float = 0
    seed: int = 42
    max_tokens: int = 2048


class TaskResponse(BaseModel):
    task_id: str
    model_id: str
    messages: list[Message]
    params: TaskParams
    response_schema: dict
    lease_seconds: int


class ModelInfo(BaseModel):
    id: str
    sha256: str | None = None


class ResultRequest(BaseModel):
    node_id: str
    status: Literal["ok", "error"]
    output: dict | None = None
    raw_text: str | None = None
    error: str | None = None
    attempts: int = 1
    usage: dict | None = None
    model: ModelInfo | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_s: float | None = None


class ResultResponse(BaseModel):
    accepted: bool
    duplicate: bool


class HealthResponse(BaseModel):
    status: str
    postgres: bool
    mysql: bool


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    node_id: str
    state: str | None = None
    agent_version: str | None = None
    loaded_model: dict | None = None
    slots_total: int = 0
    slots_busy: int = 0
    last_error: str | None = None


class DesiredModel(BaseModel):
    id: str
    filename: str
    sha256: str
    size_bytes: int
    download_url: str
    context_size: int
    parallel: int
    extra_args: list[str] = []


class HeartbeatResponse(BaseModel):
    desired_model: DesiredModel | None
    paused: bool
    heartbeat_interval_s: int


class ModeloStats(BaseModel):
    modelo: str
    enabled: bool
    pending: int = 0
    processing: int = 0
    done: int = 0
    dead: int = 0


class NodoStats(BaseModel):
    node_id: str
    state: str | None
    agent_version: str | None
    loaded_model: str | None
    slots_total: int
    slots_busy: int
    last_error: str | None
    last_seen: datetime | None


class StatsResponse(BaseModel):
    modelos: list[ModeloStats]
    nodos: list[NodoStats]


bearer = HTTPBearer(auto_error=False)


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


app = FastAPI(title="Calificador LLM", lifespan=lifespan)


@app.get("/api/v1/health", response_model=HealthResponse)
async def health():
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
)
async def heartbeat(req: HeartbeatRequest):
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
            download_url=f"/api/v1/models/{quote(modelo.filename)}",
            context_size=modelo.context_size,
            parallel=modelo.parallel,
            extra_args=modelo.extra_args or [],
        )
    return HeartbeatResponse(
        desired_model=desired,
        paused=paused,
        heartbeat_interval_s=settings.heartbeat_interval_s,
    )


@app.get("/api/v1/models/{filename}", dependencies=[Depends(auth)])
async def model_file(filename: str):
    modelo = await Modelo.filter(filename=filename, enabled=True).first()
    if not modelo:
        raise HTTPException(status_code=404, detail="model not found")
    # El agente sigue el redirect; requests quita el Bearer al cambiar de host.
    return RedirectResponse(modelo.download_url, status_code=307)


@app.get("/api/v1/stats", response_model=StatsResponse, dependencies=[Depends(auth)])
async def stats():
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


@app.post("/api/v1/tasks/next", response_model=TaskResponse, dependencies=[Depends(auth)])
async def next_task(req: TaskRequest, request: Request):
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
)
async def task_result(task_id: str, payload: ResultRequest):
    try:
        duplicate = await complete(task_id, payload.model_dump(), settings.max_attempts)
    except TaskNotFound:
        raise HTTPException(status_code=404, detail="task not found")
    return ResultResponse(accepted=True, duplicate=duplicate)
