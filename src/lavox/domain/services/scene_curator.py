"""Servicio de dominio :class:`SceneCurator`: selección semántica de clips.

Reimplementa la lógica de negocio del antiguo ``curador_ia.py``: genera
queries de búsqueda visual, evalúa la relevancia semántica de los
candidatos con un LLM y selecciona el mejor clip para cada escena,
reintentando con nuevos elementos visuales si ningún candidato supera el
umbral de relevancia.

Es un servicio de dominio puro: solo depende de :class:`LLMPort` y
:class:`VideoSearchPort`, nunca de una implementación concreta (Groq,
OpenAI, Pexels, ...), lo que lo hace trivialmente testeable con dobles de
prueba.
"""

from __future__ import annotations

import json

import structlog

from lavox.domain.entities.clip import Clip
from lavox.domain.entities.scene import Scene
from lavox.domain.exceptions import LLMError
from lavox.domain.ports.llm_port import LLMPort
from lavox.domain.ports.video_search_port import VideoSearchPort

__all__ = ["SceneCurator"]

logger = structlog.get_logger(__name__)

_PROMPT_QUERIES = """Eres un curador experto de video de stock.
Genera 3 queries para buscar en Pexels.

Contexto de la escena:
- Narración: "{narracion}"
- Tema principal: {tema_principal}
- Tono emocional: {emocion_tono}
- Tipo de escena: {tipo_escena}
- Elementos visuales clave: {elementos_clave}

REGLAS:
1. Cada query: 2-4 términos visuales CONCRETOS en INGLÉS
2. Prefiere conceptos filmables: personas, objetos, escenarios, acciones
3. NO uses conceptos abstractos ("misterio" -> "mysterious person")
4. Las 3 queries deben cubrir ángulos visuales distintos
5. IMPORTANTE: Responde SOLO con un JSON array de 3 strings

Ejemplo: ["old book mysterious letters", "surprised person thinking",
"library ancient knowledge"]"""

_PROMPT_RELEVANCIA = """Evalúa la relevancia de estos clips de stock para una escena de video.

NARRACIÓN: "{narracion}"
TEMA: {tema}
TONO: {tono}

CLIPS:
{clips_json}

INSTRUCCIONES:
- relevancia (0-100): qué tan bien el contenido visual del clip representa el TEMA
- Un clip es relevante si sus TAGS tienen relación semántica con la narración
- Ignora duración o calidad técnica
- Sé crítico: 85+ = excelente, 70+ = bueno, 50+ = aceptable, <40 = irrelevante

Responde SOLO con un JSON array. Cada objeto: indice, relevancia, justificacion
Ejemplo: [{{"indice": 0, "relevancia": 85,
"justificacion": "Tags alineados con el tema de libros antiguos"}}]"""

_PROMPT_ELEMENTOS = """Extrae 3-5 elementos VISUALES concretos (objetos, personas,
acciones, escenarios) de esta frase para buscar en video de stock.

FRASE: "{narracion}"

Responde SOLO con un JSON array de strings en INGLÉS.
Ejemplo: ["old book", "person thinking", "library", "mysterious atmosphere"]"""

_JUSTIFICACION_ERROR_EVALUACION = "Error en evaluación IA"
_JUSTIFICACION_RESPUESTA_NO_INTERPRETABLE = "Respuesta LLM no interpretable"
_RELEVANCIA_POR_DEFECTO_EN_ERROR = 30


class SceneCurator:
    """Genera queries, busca clips y evalúa su relevancia semántica por escena."""

    def __init__(
        self,
        llm: LLMPort,
        video_search: VideoSearchPort,
        *,
        relevance_threshold: int = 70,
        max_attempts: int = 2,
        evaluation_batch_size: int = 8,
    ) -> None:
        """Inicializa el curador.

        Args:
            llm: proveedor LLM usado para generar queries y evaluar relevancia.
            video_search: proveedor de búsqueda de video de stock.
            relevance_threshold: puntaje mínimo (0-100) para aceptar un clip
                sin seguir reintentando.
            max_attempts: número máximo de rondas de búsqueda por escena.
            evaluation_batch_size: máximo de candidatos evaluados por query
                (limita el tamaño del prompt de evaluación).
        """
        self._llm = llm
        self._video_search = video_search
        self._relevance_threshold = relevance_threshold
        self._max_attempts = max_attempts
        self._evaluation_batch_size = evaluation_batch_size

    async def select_best_clip(self, scene: Scene) -> tuple[Clip | None, list[str]]:
        """Selecciona el mejor clip para ``scene``, reintentando si hace falta.

        Reproduce la política original: por cada intento se generan hasta 3
        queries; la primera que produzca un candidato con relevancia >=
        ``relevance_threshold`` gana de inmediato. Si ningún candidato la
        supera, se conserva el mejor visto hasta el momento y, si quedan
        intentos, se regeneran los ``elementos_clave`` de la escena para
        variar la siguiente ronda de búsqueda.

        Args:
            scene: escena a curar. Su atributo ``elementos_clave`` puede
                mutarse entre intentos (igual que en el pipeline original).

        Returns:
            Tupla ``(mejor_clip, todas_las_variaciones_de_query_probadas)``.
            ``mejor_clip`` es ``None`` únicamente si ninguna búsqueda arrojó
            resultados en ningún intento.
        """
        mejor_clip: Clip | None = None
        todas_variaciones: list[str] = []

        for intento in range(self._max_attempts):
            queries = await self.generate_queries(scene)
            todas_variaciones.extend(queries)

            for query in queries:
                candidatos = await self._video_search.search(query)
                if not candidatos:
                    continue

                evaluados = await self.evaluate_relevance(candidatos, scene)
                mejores = sorted(evaluados, key=lambda c: c.relevancia, reverse=True)
                if not mejores:
                    continue

                if mejores[0].relevancia >= self._relevance_threshold:
                    mejor_clip = mejores[0].with_query_usada(query)
                    logger.info(
                        "clip_seleccionado",
                        scene_id=scene.numero,
                        attempt=intento,
                        clip_id=mejor_clip.id,
                        relevance_score=mejor_clip.relevancia,
                        query=query,
                    )
                    return mejor_clip, todas_variaciones

                candidato = mejores[0]
                if mejor_clip is None or candidato.relevancia > mejor_clip.relevancia:
                    mejor_clip = candidato.with_query_usada(query)
                logger.debug(
                    "clip_bajo_umbral",
                    scene_id=scene.numero,
                    attempt=intento,
                    relevance_score=candidato.relevancia,
                    threshold=self._relevance_threshold,
                )

            hay_mas_intentos = intento < self._max_attempts - 1
            if hay_mas_intentos:
                scene.elementos_clave = await self._generate_concrete_elements(scene.narracion)
                logger.info(
                    "reintento_elementos_generados",
                    scene_id=scene.numero,
                    attempt=intento,
                    elementos=scene.elementos_clave,
                )

        if mejor_clip is not None:
            logger.info(
                "clip_por_defecto_usado",
                scene_id=scene.numero,
                relevance_score=mejor_clip.relevancia,
            )
        else:
            logger.warning("sin_clip_encontrado", scene_id=scene.numero)

        return mejor_clip, todas_variaciones

    async def generate_queries(self, scene: Scene) -> list[str]:
        """Genera hasta 3 queries de búsqueda visual en inglés para ``scene``."""
        prompt = _PROMPT_QUERIES.format(
            narracion=scene.narracion,
            tema_principal=scene.tema_principal,
            emocion_tono=scene.emocion_tono,
            tipo_escena=scene.tipo_escena,
            elementos_clave=scene.elementos_clave,
        )
        resultado = await self._llm.complete_json(prompt, temperature=0.7)
        if isinstance(resultado, list) and resultado:
            return [str(query) for query in resultado[:3]]
        return [f"{scene.tema_principal} stock footage"]

    async def evaluate_relevance(self, clips: list[Clip], scene: Scene) -> list[Clip]:
        """Evalúa la relevancia semántica de ``clips`` respecto a ``scene``.

        Args:
            clips: candidatos a evaluar (se toman como máximo los primeros
                ``evaluation_batch_size``).
            scene: escena contra la que se evalúa la relevancia.

        Returns:
            Copia de los clips evaluados, con ``relevancia`` y
            ``justificacion`` completados. Si la evaluación LLM falla o no
            devuelve una lista interpretable, se asigna una relevancia baja
            por defecto en vez de propagar la excepción, para no tumbar el
            resto del pipeline por una única evaluación fallida.
        """
        if not clips:
            return []

        lote = clips[: self._evaluation_batch_size]
        lista_para_prompt = [
            {"indice": i, "id": clip.id, "tags": list(clip.tags), "duracion": clip.duracion}
            for i, clip in enumerate(lote)
        ]
        prompt = _PROMPT_RELEVANCIA.format(
            narracion=scene.narracion,
            tema=scene.tema_principal,
            tono=scene.emocion_tono,
            clips_json=json.dumps(lista_para_prompt, indent=2),
        )

        try:
            evaluaciones = await self._llm.complete_json(prompt, temperature=0.2)
        except LLMError as exc:
            logger.warning("evaluacion_relevancia_fallo", scene_id=scene.numero, error=str(exc))
            return [self._clip_con_error(clip) for clip in lote]

        if not isinstance(evaluaciones, list):
            logger.warning("evaluacion_relevancia_no_es_lista", scene_id=scene.numero)
            return [
                clip.with_evaluation(
                    relevancia=_RELEVANCIA_POR_DEFECTO_EN_ERROR,
                    justificacion=_JUSTIFICACION_RESPUESTA_NO_INTERPRETABLE,
                )
                for clip in lote
            ]

        resultado = list(lote)
        for evaluacion in evaluaciones:
            if not isinstance(evaluacion, dict):
                continue
            indice = evaluacion.get("indice")
            if isinstance(indice, int) and 0 <= indice < len(resultado):
                resultado[indice] = resultado[indice].with_evaluation(
                    relevancia=int(evaluacion.get("relevancia", 0)),
                    justificacion=str(evaluacion.get("justificacion", "")),
                )
        return resultado

    async def _generate_concrete_elements(self, narracion: str) -> list[str]:
        """Extrae 3-5 elementos visuales concretos a partir de ``narracion``."""
        prompt = _PROMPT_ELEMENTOS.format(narracion=narracion)
        try:
            resultado = await self._llm.complete_json(prompt, temperature=0.6)
        except LLMError as exc:
            logger.warning("generacion_elementos_fallo", error=str(exc))
            return ["video footage"]

        if isinstance(resultado, list) and resultado:
            return [str(elemento) for elemento in resultado]
        return ["video footage"]

    @staticmethod
    def _clip_con_error(clip: Clip) -> Clip:
        return clip.with_evaluation(
            relevancia=_RELEVANCIA_POR_DEFECTO_EN_ERROR,
            justificacion=_JUSTIFICACION_ERROR_EVALUACION,
        )
