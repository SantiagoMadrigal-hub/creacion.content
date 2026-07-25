"""Tests de :class:`SceneCurator`: queries, relevancia, reintentos y umbral.

Casos de uso cubiertos: `SceneCurator | query generation, relevance scoring,
retry con nuevos elementos, umbral`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from lavox.domain.entities.clip import Clip
from lavox.domain.entities.scene import Scene
from lavox.domain.exceptions import LLMError
from lavox.domain.services.scene_curator import SceneCurator


class ScriptedLLM:
    """Doble de `LLMPort`: devuelve una secuencia programada de respuestas JSON."""

    def __init__(self, respuestas: list[Any]) -> None:
        self._respuestas = list(respuestas)
        self.prompts: list[str] = []

    async def complete(self, prompt: str, *, temperature: float = 0.5) -> str:
        raise NotImplementedError("Este doble solo implementa complete_json")

    async def complete_json(self, prompt: str, *, temperature: float = 0.5) -> Any:
        self.prompts.append(prompt)
        if not self._respuestas:
            return None
        return self._respuestas.pop(0)


class FailingLLM:
    """Doble de `LLMPort` que siempre falla con `LLMError`."""

    async def complete(self, prompt: str, *, temperature: float = 0.5) -> str:
        raise LLMError("fallo simulado")

    async def complete_json(self, prompt: str, *, temperature: float = 0.5) -> Any:
        raise LLMError("fallo simulado")


class ScriptedVideoSearch:
    """Doble de `VideoSearchPort`: devuelve candidatos programados por query."""

    def __init__(self, resultados_por_query: dict[str, list[Clip]] | None = None) -> None:
        self._por_query = resultados_por_query or {}
        self.queries_buscadas: list[str] = []

    async def search(self, query: str, *, per_page: int = 10) -> list[Clip]:
        self.queries_buscadas.append(query)
        return self._por_query.get(query, [])


class TestGenerateQueries:
    async def test_parsea_hasta_3_queries_desde_json_valido(
        self, make_scene: Callable[..., Scene]
    ) -> None:
        llm = ScriptedLLM([["a b c", "d e f", "g h i", "sobrante"]])
        curador = SceneCurator(llm, ScriptedVideoSearch())

        queries = await curador.generate_queries(make_scene())

        assert queries == ["a b c", "d e f", "g h i"]

    async def test_usa_fallback_si_la_respuesta_no_es_una_lista(
        self, make_scene: Callable[..., Scene]
    ) -> None:
        llm = ScriptedLLM([{"no": "es una lista"}])
        curador = SceneCurator(llm, ScriptedVideoSearch())

        queries = await curador.generate_queries(make_scene(tema_principal="tema x"))

        assert queries == ["tema x stock footage"]

    async def test_usa_fallback_si_la_lista_esta_vacia(
        self, make_scene: Callable[..., Scene]
    ) -> None:
        llm = ScriptedLLM([[]])
        curador = SceneCurator(llm, ScriptedVideoSearch())

        queries = await curador.generate_queries(make_scene(tema_principal="tema y"))

        assert queries == ["tema y stock footage"]


class TestEvaluateRelevance:
    async def test_asigna_relevancia_por_indice(
        self, make_scene: Callable[..., Scene], make_clip: Callable[..., Clip]
    ) -> None:
        clips = [make_clip(id=1), make_clip(id=2)]
        llm = ScriptedLLM(
            [
                [
                    {"indice": 1, "relevancia": 90, "justificacion": "muy relevante"},
                    {"indice": 0, "relevancia": 20, "justificacion": "poco relevante"},
                ]
            ]
        )
        curador = SceneCurator(llm, ScriptedVideoSearch())

        evaluados = await curador.evaluate_relevance(clips, make_scene())

        assert evaluados[0].relevancia == 20
        assert evaluados[0].justificacion == "poco relevante"
        assert evaluados[1].relevancia == 90
        assert evaluados[1].justificacion == "muy relevante"

    async def test_ignora_indices_fuera_de_rango(
        self, make_scene: Callable[..., Scene], make_clip: Callable[..., Clip]
    ) -> None:
        clips = [make_clip(id=1)]
        llm = ScriptedLLM([[{"indice": 5, "relevancia": 99, "justificacion": "x"}]])
        curador = SceneCurator(llm, ScriptedVideoSearch())

        evaluados = await curador.evaluate_relevance(clips, make_scene())

        assert evaluados[0].relevancia == 0  # sin cambios: el índice 5 no existe

    async def test_relevancia_por_defecto_si_el_llm_falla(
        self, make_scene: Callable[..., Scene], make_clip: Callable[..., Clip]
    ) -> None:
        clips = [make_clip(id=1), make_clip(id=2)]
        curador = SceneCurator(FailingLLM(), ScriptedVideoSearch())

        evaluados = await curador.evaluate_relevance(clips, make_scene())

        assert all(clip.relevancia == 30 for clip in evaluados)
        assert all(clip.justificacion == "Error en evaluación IA" for clip in evaluados)

    async def test_relevancia_por_defecto_si_la_respuesta_no_es_lista(
        self, make_scene: Callable[..., Scene], make_clip: Callable[..., Clip]
    ) -> None:
        clips = [make_clip(id=1)]
        llm = ScriptedLLM([{"no": "es una lista"}])
        curador = SceneCurator(llm, ScriptedVideoSearch())

        evaluados = await curador.evaluate_relevance(clips, make_scene())

        assert evaluados[0].relevancia == 30
        assert evaluados[0].justificacion == "Respuesta LLM no interpretable"

    async def test_lista_vacia_de_clips_no_llama_al_llm(
        self, make_scene: Callable[..., Scene]
    ) -> None:
        llm = ScriptedLLM([])
        curador = SceneCurator(llm, ScriptedVideoSearch())

        evaluados = await curador.evaluate_relevance([], make_scene())

        assert evaluados == []
        assert llm.prompts == []


class TestSelectBestClip:
    async def test_acepta_el_primer_clip_que_supera_el_umbral(
        self, make_scene: Callable[..., Scene], make_clip: Callable[..., Clip]
    ) -> None:
        clip_bueno = make_clip(id=1)
        video_search = ScriptedVideoSearch({"query 1": [clip_bueno]})
        llm = ScriptedLLM(
            [
                ["query 1", "query 2", "query 3"],
                [{"indice": 0, "relevancia": 85, "justificacion": "excelente"}],
            ]
        )
        curador = SceneCurator(llm, video_search, relevance_threshold=70, max_attempts=2)

        mejor_clip, variaciones = await curador.select_best_clip(make_scene())

        assert mejor_clip is not None
        assert mejor_clip.relevancia == 85
        assert mejor_clip.query_usada == "query 1"
        assert variaciones == ["query 1", "query 2", "query 3"]
        # No debió buscar "query 2" ni "query 3": ya encontró un clip aceptable.
        assert video_search.queries_buscadas == ["query 1"]

    async def test_umbral_exacto_es_aceptado(
        self, make_scene: Callable[..., Scene], make_clip: Callable[..., Clip]
    ) -> None:
        clip = make_clip(id=1)
        video_search = ScriptedVideoSearch({"q": [clip]})
        llm = ScriptedLLM(
            [["q"], [{"indice": 0, "relevancia": 70, "justificacion": "justo en el umbral"}]]
        )
        curador = SceneCurator(llm, video_search, relevance_threshold=70, max_attempts=1)

        mejor_clip, _ = await curador.select_best_clip(make_scene())

        assert mejor_clip is not None
        assert mejor_clip.relevancia == 70

    async def test_reintenta_con_nuevos_elementos_si_nada_supera_el_umbral(
        self, make_scene: Callable[..., Scene], make_clip: Callable[..., Clip]
    ) -> None:
        clip_bajo = make_clip(id=1)
        clip_alto = make_clip(id=2)
        video_search = ScriptedVideoSearch({"query_a1": [clip_bajo], "query_b1": [clip_alto]})
        llm = ScriptedLLM(
            [
                ["query_a1", "query_a2", "query_a3"],  # generate_queries intento 1
                [{"indice": 0, "relevancia": 40, "justificacion": "bajo"}],  # evaluate intento 1
                ["new elem 1", "new elem 2"],  # _generate_concrete_elements
                ["query_b1", "query_b2", "query_b3"],  # generate_queries intento 2
                [{"indice": 0, "relevancia": 90, "justificacion": "alto"}],  # evaluate intento 2
            ]
        )
        escena = make_scene(elementos_clave=["viejo"])
        curador = SceneCurator(llm, video_search, relevance_threshold=70, max_attempts=2)

        mejor_clip, variaciones = await curador.select_best_clip(escena)

        assert mejor_clip is not None
        assert mejor_clip.relevancia == 90
        assert mejor_clip.query_usada == "query_b1"
        assert escena.elementos_clave == ["new elem 1", "new elem 2"]
        assert variaciones == [
            "query_a1",
            "query_a2",
            "query_a3",
            "query_b1",
            "query_b2",
            "query_b3",
        ]

    async def test_sin_clip_que_supere_el_umbral_devuelve_el_mejor_visto(
        self, make_scene: Callable[..., Scene], make_clip: Callable[..., Clip]
    ) -> None:
        clip_mediocre = make_clip(id=1)
        clip_peor = make_clip(id=2)
        video_search = ScriptedVideoSearch({"q1": [clip_mediocre], "q2": [clip_peor]})
        llm = ScriptedLLM(
            [
                ["q1"],
                [{"indice": 0, "relevancia": 50, "justificacion": "mediocre"}],
                ["elementos nuevos"],
                ["q2"],
                [{"indice": 0, "relevancia": 30, "justificacion": "peor"}],
            ]
        )
        curador = SceneCurator(llm, video_search, relevance_threshold=70, max_attempts=2)

        mejor_clip, _ = await curador.select_best_clip(make_scene())

        assert mejor_clip is not None
        assert mejor_clip.relevancia == 50  # se queda con el mejor de los dos, no el último

    async def test_sin_ningun_candidato_devuelve_none(
        self, make_scene: Callable[..., Scene]
    ) -> None:
        video_search = ScriptedVideoSearch({})  # ninguna query devuelve resultados
        llm = ScriptedLLM(
            [
                ["q1", "q2", "q3"],
                ["elementos nuevos"],
                ["q4", "q5", "q6"],
            ]
        )
        curador = SceneCurator(llm, video_search, relevance_threshold=70, max_attempts=2)

        mejor_clip, variaciones = await curador.select_best_clip(make_scene())

        assert mejor_clip is None
        assert len(variaciones) == 6

    async def test_propaga_llm_error_si_generate_queries_falla(
        self, make_scene: Callable[..., Scene]
    ) -> None:
        curador = SceneCurator(FailingLLM(), ScriptedVideoSearch())

        with pytest.raises(LLMError):
            await curador.select_best_clip(make_scene())


class TestGenerateConcreteElements:
    async def test_usa_fallback_si_el_llm_falla(self, make_scene: Callable[..., Scene]) -> None:
        curador = SceneCurator(FailingLLM(), ScriptedVideoSearch())

        elementos = await curador._generate_concrete_elements("una narración cualquiera")

        assert elementos == ["video footage"]

    async def test_usa_fallback_si_la_respuesta_no_es_lista_valida(
        self, make_scene: Callable[..., Scene]
    ) -> None:
        llm = ScriptedLLM([{"no": "es una lista"}])
        curador = SceneCurator(llm, ScriptedVideoSearch())

        elementos = await curador._generate_concrete_elements("una narración cualquiera")

        assert elementos == ["video footage"]
