# Calificador LLM

Maestro FastAPI que entrega trabajos de calificación a un pool de nodos con GPU.
Lee los envíos de MySQL (solo `SELECT`), guarda un snapshot en Postgres y expone
los endpoints del contrato para pedir tareas y devolver resultados.

## Arranque

```bash
cp .env.example .env          # completar credenciales
docker compose up -d db       # Postgres en localhost:5433

# Migraciones nativas de Tortoise (solo tocan Postgres)
uv run tortoise -c src.config.MIGRATIONS_CONFIG migrate local

# Backfill de envíos (la ingesta incremental está apagada por defecto)
uv run python -m src.ingest --desde 2026-01-01 --lote 500

# API
uv run uvicorn src.api:app --host 0.0.0.0 --port 8080
```

`SYNC_ENABLED=true` activa la ingesta incremental cada `SYNC_INTERVAL_S` segundos.

## Modelos

Los modelos se toman de la tabla `modelo`: se encola una tarea por
(envío, métrica, modelo) para cada fila con `enabled = true`. Los metadatos
(nombre, hash, contexto, slots) también viven ahí; no hay lista de modelos en el `.env`.

Al agregar un modelo nuevo:

1. Insertarlo en `modelo` (o en `src/seed_modelos.py` y correr el seed).
2. Reencolar el histórico:

```bash
uv run python -m src.ingest --reiniciar
```

Ojo: con 3 modelos, el histórico completo son ~30M de tareas.

`METRICAS_MODELO` permite restringir una métrica a un modelo, por ejemplo
`{"M13":"gemma-4-31b-q4"}`. Si el modelo no está habilitado, esa métrica no se
encola. Si está vacío, todas las métricas van a todos los modelos habilitados.

### Registro de modelos y heartbeat

Los metadatos viven en Postgres (`modelo`). No hace falta alojar los archivos ni
usar S3: `download_url` guarda el link directo de Hugging Face.

```bash
uv run python -m src.seed_modelos
```

| ID | Archivo | Contexto | Slots |
|---|---|---:|---:|
| `gpt-oss-20b-q5` | `gpt-oss-20b-Q5_K_S.llamafile` | 16384 | 1 |
| `qwen3.8-27b-q4` | `Qwen3.8-27B-UD-Q4_K_XL.llamafile` | 8192 | 1 |
| `gemma-4-31b-q4` | `gemma-4-31b-it-q4_k_m.llamafile` | 8192 | 1 |

Sin pin, el master reparte los nodos entre los modelos habilitados que tengan
trabajo. Para fijar un nodo a un modelo:

```sql
INSERT INTO nodo_modelo (node_id, modelo_id, updated_at)
VALUES ('PC-01', 'gemma-4-31b-q4', now())
ON CONFLICT (node_id) DO UPDATE
  SET modelo_id = EXCLUDED.modelo_id, updated_at = now();
```

### Reasignación automática

Con `AUTO_REASIGNAR=true` (default), en cada heartbeat el master:

1. Respeta un pin de `nodo_modelo` (pin duro, aunque el modelo esté sin trabajo).
2. Mantiene el modelo que el nodo ya tiene cargado si quedan tareas `pending` o `processing`.
3. Si no, asigna un modelo con trabajo, eligiendo el que tenga **menos nodos activos** (`nodo_estado`, últimos `HEARTBEAT_TIMEOUT_S` segundos). En empate reparte por un hash estable del `node_id`.
4. Si ningún modelo tiene trabajo, devuelve `desired_model: null` y el agente libera la GPU.

No hay prioridad entre modelos: los que tienen trabajo se procesan en paralelo y el
master reparte los nodos entre ellos. El cambio se dispara cuando el modelo se
queda sin `pending`/`processing`: el agente drena lo que tenga en vuelo,
descarga/carga el nuevo modelo y empieza a pedir tareas de ese `model_id`. No se
reenvían tareas terminadas; las `dead` no cuentan como trabajo.

- `MANTENER_MODELO_SIN_TRABAJO=true` evita descargar el modelo cuando no hay trabajo.
- `AUTO_REASIGNAR=false` vuelve al modo manual (pin, modelo cargado o default).
- `HEARTBEAT_TIMEOUT_S=60`: un nodo sin heartbeat en ese tiempo no cuenta para el reparto.

Para que la reasignación funcione, las tareas del siguiente modelo deben existir
en `calificacion`. Si solo ingestaste un modelo, el master no puede cambiarse a
otro: hay que encolar los 3 (o correr la ingesta del siguiente modelo).

Para pausar la sala (el agente libera la GPU):

```sql
INSERT INTO sync_state (clave, valor) VALUES ('pausado', 'true')
ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor;
-- reanudar: valor = 'false'
```

El heartbeat devuelve el `download_url` directo de Hugging Face que está guardado
en `modelo.download_url`. El endpoint `/api/v1/models/<archivo>` queda disponible
como redirect alternativo.

## Endpoints

- `POST /api/v1/heartbeat` — modelo deseado, pausa y `heartbeat_interval_s`.
- `GET /api/v1/models/{filename}` — redirige al archivo del modelo (alternativo al link directo).
- `GET /api/v1/stats` — pendientes/procesando/terminadas por modelo y último heartbeat por nodo.
- `POST /api/v1/tasks/next` — `{node_id, model_id}`; `204` si no hay trabajo.
- `POST /api/v1/tasks/{task_id}/result` — idempotente por modelo; el primer `ok` gana.
- `GET /api/v1/health`

Todos los endpoints de trabajo llevan `Authorization: Bearer <API_TOKEN>`.

## Pruebas

Las pruebas borran las tablas; usa una base aparte para no perder el registro de
modelos ni la cola:

```bash
docker compose exec db createdb -U llm calificador_llm_test
TEST_LOCAL_DB_URL=postgres://llm:llm@localhost:5433/calificador_llm_test uv run pytest
```

Las pruebas de cola requieren el Postgres del compose; las de prompts no.
