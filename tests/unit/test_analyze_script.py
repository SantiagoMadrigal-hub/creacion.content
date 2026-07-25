"""Tests de :class:`Script` (agrupación) y :class:`AnalyzeScriptUseCase`.

Casos de uso cubiertos: `AnalyzeScript | agrupación líneas, análisis escena,
checkpoint resume`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lavox.application.use_cases.analyze_script import AnalyzeScriptUseCase
from lavox.domain.entities.clip import Clip
from lavox.domain.entities.scene import Scene
from lavox.domain.entities.script import Script
from lavox.domain.exceptions import ConfigError, LLMError


class TestAgruparLineas:
    def test_no_agrupa_si_ya_esta_bajo_el_umbral(self) -> None:
        script = Script.from_text("línea uno\nlínea dos\nlínea tres")
        resultado = script.agrupar(min_length=60, max_scenes_threshold=45)
        assert resultado == ("línea uno", "línea dos", "línea tres")

    def test_fusiona_lineas_cortas_cuando_supera_el_umbral(self) -> None:
        lineas_cortas = "\n".join(["hola"] * 50)  # 50 líneas > umbral de 45
        script = Script.from_text(lineas_cortas)

        resultado = script.agrupar(min_length=60, max_scenes_threshold=45)

        assert len(resultado) < 50
        assert all(len(grupo) >= 60 or grupo == resultado[-1] for grupo in resultado)

    def test_script_vacio_no_falla(self) -> None:
        script = Script.from_text("")
        assert script.agrupar() == ()

    def test_from_text_maneja_terminadores_crlf_y_lineas_vacias(self) -> None:
        script = Script.from_text("línea uno\r\n\r\nlínea dos\r\n   \r\nlínea tres\r\n")
        assert script.lineas == ("línea uno", "línea dos", "línea tres")


class ScriptedContextLLM:
    """Doble de `LLMPort`: una respuesta (o excepción) de contexto por escena, en orden."""

    def __init__(self, respuestas: list[Any]) -> None:
        self._respuestas = list(respuestas)
        self.prompts: list[str] = []

    async def complete(self, prompt: str, *, temperature: float = 0.5) -> str:
        raise NotImplementedError("Este doble solo implementa complete_json")

    async def complete_json(self, prompt: str, *, temperature: float = 0.5) -> Any:
        self.prompts.append(prompt)
        item = self._respuestas.pop(0) if self._respuestas else {}
        if isinstance(item, Exception):
            raise item
        return item


class FakeSceneCurator:
    """Doble de `SceneCurator`: devuelve un resultado programado por número de escena."""

    def __init__(self, resultado_por_numero: dict[int, tuple[Clip | None, list[str]]]) -> None:
        self._resultado_por_numero = resultado_por_numero
        self.escenas_recibidas: list[Scene] = []

    async def select_best_clip(self, scene: Scene) -> tuple[Clip | None, list[str]]:
        self.escenas_recibidas.append(scene)
        return self._resultado_por_numero.get(scene.numero, (None, []))


def _escribir_guion(path: Path, lineas: list[str]) -> None:
    path.write_text("\n\n".join(lineas), encoding="utf-8")


class TestAnalyzeScriptUseCaseExecute:
    async def test_analiza_todas_las_escenas_desde_cero(
        self, tmp_path: Path, make_clip: Any
    ) -> None:
        guion = tmp_path / "guion.txt"
        _escribir_guion(guion, ["Primera línea narrativa.", "Segunda línea narrativa."])
        checkpoint = tmp_path / "escenas.json"

        clip = make_clip()
        llm = ScriptedContextLLM(
            [
                {
                    "tema_principal": "tema uno",
                    "emocion_tono": "intriga",
                    "tipo_escena": "hook_inicio",
                    "elementos_clave": ["a", "b"],
                },
                {
                    "tema_principal": "tema dos",
                    "emocion_tono": "reflexivo",
                    "tipo_escena": "explicacion",
                    "elementos_clave": ["c", "d"],
                },
            ]
        )
        curador = FakeSceneCurator({1: (clip, ["q1"]), 2: (None, ["q2", "q3"])})
        caso_de_uso = AnalyzeScriptUseCase(
            llm, curador, min_line_length_for_grouping=1, max_scenes_before_grouping=1000
        )

        escenas = await caso_de_uso.execute(guion, checkpoint)

        assert len(escenas) == 2
        assert escenas[0].tema_principal == "tema uno"
        assert escenas[0].clip_seleccionado is not None
        assert escenas[1].tema_principal == "tema dos"
        assert escenas[1].clip_seleccionado is None
        assert checkpoint.exists()

    async def test_guarda_checkpoint_incrementalmente_tras_cada_escena(
        self, tmp_path: Path, make_clip: Any
    ) -> None:
        """Si el proceso se interrumpe en la escena 2, la 1 ya debe estar persistida."""
        guion = tmp_path / "guion.txt"
        _escribir_guion(guion, ["Línea 1.", "Línea 2."])
        checkpoint = tmp_path / "escenas.json"

        llm = ScriptedContextLLM([{"tema_principal": "t1"}, {"tema_principal": "t2"}])

        class CuradorQueFallaEnLaSegunda(FakeSceneCurator):
            async def select_best_clip(self, scene: Scene) -> tuple[Clip | None, list[str]]:
                if scene.numero == 2:
                    raise RuntimeError("se cae el proceso justo aquí")
                return await super().select_best_clip(scene)

        curador = CuradorQueFallaEnLaSegunda({1: (make_clip(), [])})
        caso_de_uso = AnalyzeScriptUseCase(
            llm, curador, min_line_length_for_grouping=1, max_scenes_before_grouping=1000
        )

        with pytest.raises(RuntimeError):
            await caso_de_uso.execute(guion, checkpoint)

        # A pesar del "crash" en la escena 2, la escena 1 ya quedó guardada en disco.
        datos_parciales = json.loads(checkpoint.read_text(encoding="utf-8"))
        assert len(datos_parciales) == 1
        assert datos_parciales[0]["tema_principal"] == "t1"

    async def test_reanuda_desde_checkpoint_existente(self, tmp_path: Path, make_clip: Any) -> None:
        guion = tmp_path / "guion.txt"
        _escribir_guion(guion, ["Línea 1.", "Línea 2.", "Línea 3."])
        checkpoint = tmp_path / "escenas.json"

        escena_previa = Scene(numero=1, narracion="Línea 1.", tema_principal="ya procesada")
        checkpoint.write_text(json.dumps([escena_previa.to_dict()]), encoding="utf-8")

        llm = ScriptedContextLLM([{"tema_principal": "t2"}, {"tema_principal": "t3"}])
        curador = FakeSceneCurator({2: (make_clip(), []), 3: (make_clip(), [])})
        caso_de_uso = AnalyzeScriptUseCase(
            llm, curador, min_line_length_for_grouping=1, max_scenes_before_grouping=1000
        )

        escenas = await caso_de_uso.execute(guion, checkpoint, resume=True)

        assert len(escenas) == 3
        assert escenas[0].tema_principal == "ya procesada"
        # Solo se le pidió contexto a las escenas 2 y 3, no de nuevo a la 1.
        assert len(llm.prompts) == 2
        assert [escena.numero for escena in curador.escenas_recibidas] == [2, 3]

    async def test_no_reanuda_si_resume_es_false(self, tmp_path: Path, make_clip: Any) -> None:
        guion = tmp_path / "guion.txt"
        _escribir_guion(guion, ["Línea 1."])
        checkpoint = tmp_path / "escenas.json"
        escena_previa = Scene(numero=1, narracion="Línea 1.", tema_principal="vieja")
        checkpoint.write_text(json.dumps([escena_previa.to_dict()]), encoding="utf-8")

        llm = ScriptedContextLLM([{"tema_principal": "nueva"}])
        curador = FakeSceneCurator({1: (make_clip(), [])})
        caso_de_uso = AnalyzeScriptUseCase(
            llm, curador, min_line_length_for_grouping=1, max_scenes_before_grouping=1000
        )

        escenas = await caso_de_uso.execute(guion, checkpoint, resume=False)

        assert escenas[0].tema_principal == "nueva"

    async def test_falla_si_no_existe_el_guion(self, tmp_path: Path) -> None:
        caso_de_uso = AnalyzeScriptUseCase(ScriptedContextLLM([]), FakeSceneCurator({}))
        with pytest.raises(ConfigError):
            await caso_de_uso.execute(tmp_path / "no_existe.txt", tmp_path / "out.json")

    async def test_fallo_de_llm_en_analisis_de_contexto_usa_valores_por_defecto(
        self, tmp_path: Path, make_clip: Any
    ) -> None:
        guion = tmp_path / "guion.txt"
        _escribir_guion(guion, ["Única línea."])
        checkpoint = tmp_path / "escenas.json"

        llm = ScriptedContextLLM([LLMError("el proveedor está caído")])
        curador = FakeSceneCurator({1: (make_clip(), [])})
        caso_de_uso = AnalyzeScriptUseCase(
            llm, curador, min_line_length_for_grouping=1, max_scenes_before_grouping=1000
        )

        escenas = await caso_de_uso.execute(guion, checkpoint)

        assert len(escenas) == 1
        assert escenas[0].tema_principal == "video scene"  # valor por defecto
        assert escenas[0].clip_seleccionado is not None  # el pipeline igual continuó

    async def test_invoca_el_callback_de_progreso_por_cada_escena(
        self, tmp_path: Path, make_clip: Any
    ) -> None:
        guion = tmp_path / "guion.txt"
        _escribir_guion(guion, ["Línea 1.", "Línea 2."])
        checkpoint = tmp_path / "escenas.json"

        llm = ScriptedContextLLM([{}, {}])
        curador = FakeSceneCurator({1: (make_clip(), []), 2: (make_clip(), [])})
        caso_de_uso = AnalyzeScriptUseCase(
            llm, curador, min_line_length_for_grouping=1, max_scenes_before_grouping=1000
        )

        avances: list[tuple[int, int]] = []
        await caso_de_uso.execute(
            guion, checkpoint, on_scene_processed=lambda n, t: avances.append((n, t))
        )

        assert avances == [(1, 2), (2, 2)]
