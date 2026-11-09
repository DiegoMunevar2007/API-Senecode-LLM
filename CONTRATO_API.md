# Contrato de API: agente de nodos ↔ master

Versión 0.1 — borrador para acordar entre el equipo del master y el del agente.

## Principios

- **Los nodos piden trabajo (pull).** El master nunca se conecta a los PCs. Los PCs solo necesitan poder salir hacia el master por un puerto del rango 8080–8090.
- **llamafile escucha solo en `127.0.0.1`** en cada PC. Nadie fuera del PC puede usarlo.
- **El master arma los prompts.** El agente es un ejecutor genérico: recibe mensajes, los pasa al modelo, valida el JSON y devuelve el resultado. La lógica de calificación (rúbrica, empaquetado del código, defensas contra inyección) vive en el master.
- **El master decide el modelo.** Cada nodo carga el modelo que el master indica en la respuesta al heartbeat.
- Todo es JSON sobre HTTP. Fechas en ISO 8601 UTC.

## Autenticación

Todas las peticiones llevan el encabezado:

```
Authorization: Bearer <token>
```

Para la v1 basta un token compartido por toda la sala (configurable en el master y en `config.toml` de cada nodo). El master responde `401` si no coincide. Más adelante puede pasarse a un token por nodo.

## 1. Heartbeat

`POST /api/v1/heartbeat` — cada nodo lo envía cada `heartbeat_interval_s` (10 s por defecto).

### Petición

```json
{
  "node_id": "SALA-PC07",
  "agent_version": "0.1.0",
  "state": "ready",
  "loaded_model": { "id": "qwen-coder-14b-q8", "sha256": "ab12..." },
  "slots_total": 2,
  "slots_busy": 1,
  "gpu": [
    { "name": "NVIDIA GeForce RTX 4090", "mem_used_mb": 18000, "mem_total_mb": 24564,
      "util_pct": 97, "temp_c": 71 }
  ],
  "last_error": null,
  "timestamp": "2026-11-05T20:15:00+00:00"
}
```

`state` puede ser: `starting`, `idle` (sin modelo asignado), `paused`, `downloading`, `loading`, `ready`, `busy`, `error`.
`gpu` es `null` si `nvidia-smi` no está disponible.

### Respuesta `200`

```json
{
  "desired_model": {
    "id": "qwen-coder-14b-q8",
    "filename": "qwen-coder-14b-q8.gguf",
    "sha256": "ab12...",
    "size_bytes": 15700000000,
    "download_url": "/models/qwen-coder-14b-q8.gguf",
    "context_size": 16384,
    "parallel": 2,
    "extra_args": []
  },
  "paused": false,
  "heartbeat_interval_s": 10
}
```

- `desired_model: null` → el nodo descarga su modelo de la GPU y queda en `idle`.
- `paused: true` → igual, libera la GPU (útil fuera de las franjas reservadas).
- `context_size` es **por calificación simultánea**. El agente reserva `context_size × parallel` en llamafile.
- `download_url` puede ser relativa al master o absoluta.
- `extra_args` son argumentos adicionales para llamafile (opcional).
- Si cambia cualquiera de `id`, `sha256`, `context_size`, `parallel` o `extra_args`, el nodo termina las calificaciones en curso, reinicia llamafile y vuelve a `ready`.

El master puede usar los heartbeats para el monitoreo: un nodo sin heartbeat en 30–60 s se considera caído.

## 2. Descarga de modelos

`GET <download_url>` — con el mismo encabezado de autenticación.

- El master **debe** soportar `Range: bytes=N-` y responder `206` para reanudar descargas cortadas (o `200` con el archivo completo si no lo soporta; el agente reinicia la descarga).
- El agente verifica el `sha256` al terminar y descarta el archivo si no coincide.
- Recomendado: que el master limite las descargas simultáneas (por ejemplo, 4) y responda `503` al resto; el agente reintenta con espera creciente.

## 3. Pedir una tarea

`POST /api/v1/tasks/next`

### Petición

```json
{ "node_id": "SALA-PC07", "model_id": "qwen-coder-14b-q8" }
```

### Respuestas

- `204` sin cuerpo → no hay tareas para ese modelo. El agente espera unos segundos y vuelve a pedir.
- `200` con la tarea:

```json
{
  "task_id": "entrega-2026-0042",
  "model_id": "qwen-coder-14b-q8",
  "messages": [
    { "role": "system", "content": "Eres un asistente que califica..." },
    { "role": "user", "content": "Rúbrica: ...\n<entrega>\n...código...\n</entrega>" }
  ],
  "params": { "temperature": 0, "seed": 42, "max_tokens": 2048 },
  "response_schema": { "type": "object", "properties": { "...": {} }, "required": ["..."] },
  "lease_seconds": 900
}
```

- `messages` sigue el formato de chat de OpenAI.
- `params` admite `temperature`, `seed`, `max_tokens`, `top_p`, `top_k`, `repeat_penalty`.
- `response_schema` (opcional) es un JSON Schema. Si viene, el agente fuerza la salida en JSON con ese esquema, la valida y reintenta una vez si es inválida.
- `lease_seconds`: si el master no recibe el resultado en ese tiempo, vuelve a poner la tarea en la cola. **El master debe aceptar resultados duplicados** de una misma tarea (tomar el primero e ignorar los demás).
- El master solo debe entregar tareas cuyo `model_id` coincida con el que reporta el nodo.

## 4. Devolver el resultado

`POST /api/v1/tasks/{task_id}/result`

### Petición

```json
{
  "node_id": "SALA-PC07",
  "status": "ok",
  "output": { "puntaje_total": 4.2, "criterios": [ ... ], "resumen": "..." },
  "raw_text": "{\"puntaje_total\": 4.2, ...}",
  "error": null,
  "attempts": 1,
  "usage": { "prompt_tokens": 5120, "completion_tokens": 380 },
  "model": { "id": "qwen-coder-14b-q8", "sha256": "ab12..." },
  "started_at": "2026-11-05T20:15:02+00:00",
  "finished_at": "2026-11-05T20:15:41+00:00",
  "duration_s": 39.1
}
```

- `status: "error"` con `error` describiendo el problema (`model_mismatch`, JSON inválido tras reintentar, fallo de llamafile, etc.). `output` es `null` y `raw_text` puede traer lo que respondió el modelo.
- El master responde `200` (o `204`). Si responde `4xx` (por ejemplo `409` porque la tarea ya no existe), el agente descarta el resultado. Si hay error de red o `5xx`, el agente lo guarda en disco y lo reenvía cuando el master vuelva.

## Pendiente de acordar

- [ ] Puerto definitivo del master dentro de 8080–8090.
- [ ] Token compartido o por nodo.
- [ ] Esquema JSON de la calificación (lo define el equipo del master con la rúbrica).
- [ ] Valor de `lease_seconds` según el tiempo real por entrega medido en la fase 0.
- [ ] Si el master expone `/models` en el mismo servicio o en otro servidor de archivos.
