from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

Veredicto = Literal["SI", "NO", "NO_APLICA", "NO_DECIDIBLE"]
Clase = Literal[
    "O(1)",
    "O(log n)",
    "O(n)",
    "O(n log n)",
    "O(n^2)",
    "O(n^2 log n)",
    "O(n^3+)",
    "otra",
    "desconocida",
]


class Evidencia(BaseModel):
    model_config = ConfigDict(extra="forbid")
    linea: int
    elemento: str


class RespuestaBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    veredicto: Veredicto
    evidencia: list[Evidencia]
    razon: str


class RespuestaBinaria(RespuestaBase):
    pass


class RespuestaConteo(RespuestaBase):
    n: int | None


class RespuestaClase(RespuestaBase):
    clase_estudiante: Clase
    clase_referencia: Clase


RESPUESTAS = {
    "binary": RespuestaBinaria,
    "count": RespuestaConteo,
    "class": RespuestaClase,
}


class PromptSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    codigo: str
    titulo: str
    forma: Literal["binary", "count", "class"]
    desde: int
    verificador: str = ""
    oraculo: str = ""
    prompt: str


class PromptsFile(BaseModel):
    version: int
    metricas: list[PromptSpec]


class Message(BaseModel):
    role: str
    content: str


@lru_cache
def load_prompts(path: str | Path = "prompts.json") -> dict[str, PromptSpec]:
    data = PromptsFile.model_validate_json(Path(path).read_text(encoding="utf-8"))
    return {spec.codigo: spec for spec in data.metricas}


def aplicables(specs: dict[str, PromptSpec], nivel: int) -> list[PromptSpec]:
    return [spec for _, spec in sorted(specs.items()) if nivel >= spec.desde]


def number_code(code: str) -> str:
    return "\n".join(f"{i:4d}  {line}" for i, line in enumerate(code.splitlines(), 1))


def build_contexto(problema, metrica: str) -> str:
    parts = [
        f"Título: {problema.titulo}",
        f"Firma: def {problema.funcion}(...) -> {problema.retorno_tipo}",
        f"Nivel: N{problema.nivel}",
    ]
    if problema.retorno_descripcion:
        parts.append(f"Retorno: {problema.retorno_descripcion}")
    parts += ["", "Descripción:", problema.descripcion or "(sin descripción)"]
    if metrica == "M13" and problema.solucion_referencia:
        parts += ["", "Solución de referencia:", "```python", problema.solucion_referencia, "```"]
    return "\n".join(parts)


def render(spec: PromptSpec, contexto: str, codigo: str) -> list[Message]:
    content = spec.prompt.replace("{contexto}", contexto).replace(
        "{codigo_numerado}", number_code(codigo)
    )
    return [Message(role="user", content=content)]


def response_schema(forma: str) -> dict:
    return RESPUESTAS[forma].model_json_schema()
