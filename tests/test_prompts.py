from pathlib import Path

from src.prompts import load_prompts, number_code, render, response_schema

ROOT = Path(__file__).parents[1]
PROMPTS = ROOT / "src" / "prompts.json"


def test_metricas_y_formas():
    specs = load_prompts(PROMPTS)
    assert len(specs) == 17
    formas = [spec.forma for spec in specs.values()]
    assert formas.count("binary") == 13
    assert formas.count("count") == 3
    assert formas.count("class") == 1
    assert [spec.codigo for spec in specs.values() if spec.desde == 3] == [
        "M06",
        "M10",
        "M11",
        "M13",
        "M14",
    ]


def test_placeholders():
    for spec in load_prompts(PROMPTS).values():
        assert "{contexto}" in spec.prompt
        assert "{codigo_numerado}" in spec.prompt
        assert "\n\n\n" not in spec.prompt


def test_render_numera_y_no_rompe_llaves():
    spec = load_prompts(PROMPTS)["M01"]
    messages = render(spec, "contexto de prueba", "x = 1\nreturn x")
    assert len(messages) == 1
    content = messages[0].content
    assert "contexto de prueba" in content
    assert "   1  x = 1" in content
    assert "   2  return x" in content
    assert "{contexto}" not in content
    assert '"veredicto": "SI" | "NO"' in content


def test_number_code_respeta_crlf():
    assert number_code("a\r\nb\r\n") == "   1  a\n   2  b"


def test_response_schema_por_forma():
    assert set(response_schema("binary")["required"]) == {"veredicto", "evidencia", "razon"}
    assert "n" in response_schema("count")["required"]
    clase = response_schema("class")["required"]
    assert "clase_estudiante" in clase and "clase_referencia" in clase
