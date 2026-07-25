"""Tests directos de :func:`extract_json_value`."""

from __future__ import annotations

from lavox.infrastructure.llm._parsing import extract_json_value


class TestExtractJsonValue:
    def test_texto_vacio_devuelve_none(self) -> None:
        assert extract_json_value("") is None
        assert extract_json_value("   \n  ") is None

    def test_objeto_json_directo(self) -> None:
        assert extract_json_value('{"a": 1}') == {"a": 1}

    def test_array_json_directo(self) -> None:
        assert extract_json_value("[1, 2, 3]") == [1, 2, 3]

    def test_objeto_envuelto_en_texto_y_markdown(self) -> None:
        texto = 'Aquí tienes el resultado:\n```json\n{"a": 1, "b": 2}\n```\nEspero que ayude.'
        assert extract_json_value(texto) == {"a": 1, "b": 2}

    def test_array_envuelto_en_texto(self) -> None:
        texto = 'Las queries son: ["uno", "dos"] segun mi analisis.'
        assert extract_json_value(texto) == ["uno", "dos"]

    def test_sin_ningun_json_devuelve_none(self) -> None:
        assert extract_json_value("Lo siento, no puedo ayudar con eso.") is None

    def test_escalar_suelto_no_se_considera_json_valido(self) -> None:
        # Los prompts del dominio siempre piden un objeto o un array; un
        # escalar top-level (string/num/bool) no es una forma útil.
        assert extract_json_value("42") is None
        assert extract_json_value('"solo un string"') is None

    def test_json_malformado_devuelve_none(self) -> None:
        assert extract_json_value("{esto no es json valido,,,}") is None
