"""Caso de uso: analizar el guion y curar el mejor clip para cada escena.

Reimplementa la lógica de ``ejercicio1.py``: agrupa las líneas del guion en
escenas, le pide al LLM contexto narrativo por escena, delega en
:class:`~lavox.domain.services.scene_curator.SceneCurator` la selección del
mejor clip, y persiste un checkpoint JSON tras cada escena para poder
reanudar la ejecución si el proceso se interrumpe.

El checkpoint usa el mismo esquema que el ``escenas_con_videos.json`` del
pipeline original, así que un archivo generado por la versión anterior
sigue siendo un punto de reanudación válido.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from lavox.domain.entities.scene import Scene
from lavox.domain.entities.script import Script
from lavox.domain.exceptions import ConfigError, LLMError
from lavox.domain.ports.llm_port import LLMPort
from lavox.domain.services.scene_curator import SceneCurator

__all__ = ["AnalyzeScriptUseCase"]

logger = structlog.get_logger(__name__)

_PROMPT_ANALISIS_ESCENA = """Eres un director de video. Analiza esta línea de narración
y extrae información para crear una escena de video impactante.

LÍNEA {numero}/{total_escenas}: "{narracion}"

CONTEXTO: {contexto_narrativo}

Responde SOLO con un JSON object:
- "tema_principal": string (2-5 palabras, tema central)
- "emocion_tono": string (intriga, sorpresa, humor, educativo, reflexivo, misterio, contraste)
- "tipo_escena": string (hook_inicio, explicacion, ejemplo_historico,
   transicion, giro_revelacion, reflexion_final, pregunta_cierre)
- "elementos_clave": array de 3-5 strings en INGLÉS (elementos visuales CONCRETOS)

Ejemplo:
{{"tema_principal": "origen de la palabra hamburguesa",
  "emocion_tono": "sorpresa educativa",
  "tipo_escena": "ejemplo_historico",
  "elementos_clave": ["hamburger", "german immigrants cooking", "old kitchen",
    "Hamburg city map", "steak on plate"]}}"""

_DEFAULTS: dict[str, Any] = {
    "tema_principal": "video scene",
    "emocion_tono": "neutral",
    "tipo_escena": "explicacion",
    "elementos_clave": ["video footage"],
}


class AnalyzeScriptUseCase:
    """Lee el guion, lo agrupa en escenas y cura el mejor clip para cada una."""

    def __init__(
        self,
        llm: LLMPort,
        scene_curator: SceneCurator,
        *,
        min_line_length_for_grouping: int = 60,
        max_scenes_before_grouping: int = 45,
        contexto_narrativo: str = "Documental sobre palabras en inglés con orígenes engañosos.",
    ) -> None:
        """Inicializa el caso de uso.

        Args:
            llm: proveedor LLM usado para el análisis narrativo por escena.
            scene_curator: servicio de dominio que selecciona el mejor clip.
            min_line_length_for_grouping: ver :meth:`Script.agrupar`.
            max_scenes_before_grouping: ver :meth:`Script.agrupar`.
            contexto_narrativo: descripción del tema del video, usada para
                darle contexto al LLM al analizar cada escena.
        """
        self._llm = llm
        self._scene_curator = scene_curator
        self._min_line_length = min_line_length_for_grouping
        self._max_scenes_threshold = max_scenes_before_grouping
        self._contexto_narrativo = contexto_narrativo

    async def execute(
        self,
        script_path: Path,
        output_path: Path,
        *,
        resume: bool = True,
        on_scene_processed: Callable[[int, int], None] | None = None,
    ) -> list[Scene]:
        """Analiza el guion en ``script_path`` y guarda checkpoints en ``output_path``.

        Args:
            script_path: ruta del archivo de guion (líneas de narración).
            output_path: ruta del checkpoint JSON de escenas ya procesadas.
            resume: si es ``True`` (por defecto) y ``output_path`` ya existe
                con contenido válido, continúa desde donde se quedó en vez
                de reprocesar desde cero.
            on_scene_processed: callback opcional invocado con
                ``(numero_de_escena, total_de_escenas)`` después de procesar
                cada escena. Permite a la capa de presentación (p. ej. una
                barra de progreso en la CLI) reflejar avance sin que este
                caso de uso dependa de ninguna librería de presentación.

        Returns:
            Lista completa de escenas analizadas y curadas, en orden.

        Raises:
            ConfigError: si ``script_path`` no existe.
        """
        if not script_path.exists():
            raise ConfigError(f"No se encuentra el guion: {script_path}")

        script = Script.from_text(script_path.read_text(encoding="utf-8"))
        lineas = script.agrupar(
            min_length=self._min_line_length,
            max_scenes_threshold=self._max_scenes_threshold,
        )
        total = len(lineas)
        logger.info("guion_cargado", total_escenas=total, script_path=str(script_path))

        escenas = self._cargar_checkpoint(output_path) if resume else []
        if escenas:
            logger.info("checkpoint_encontrado", escenas_ya_procesadas=len(escenas))

        for indice in range(len(escenas), total):
            narracion = lineas[indice]
            numero = indice + 1

            contexto = await self._analizar_contexto(narracion, numero, total)
            escena = Scene(
                numero=numero,
                narracion=narracion,
                tema_principal=contexto.get("tema_principal", _DEFAULTS["tema_principal"]),
                emocion_tono=contexto.get("emocion_tono", _DEFAULTS["emocion_tono"]),
                tipo_escena=contexto.get("tipo_escena", _DEFAULTS["tipo_escena"]),
                elementos_clave=list(contexto.get("elementos_clave", _DEFAULTS["elementos_clave"])),
            )

            mejor_clip, variaciones = await self._scene_curator.select_best_clip(escena)
            escena.variaciones_intentadas = variaciones
            escena.clip_seleccionado = mejor_clip

            escenas.append(escena)
            self._guardar_checkpoint(output_path, escenas)

            logger.info(
                "escena_procesada",
                scene_id=numero,
                total=total,
                tiene_clip=escena.tiene_clip,
                relevance_score=mejor_clip.relevancia if mejor_clip else None,
            )
            if on_scene_processed is not None:
                on_scene_processed(numero, total)

        logger.info("analisis_completado", total_escenas=len(escenas), output_path=str(output_path))
        return escenas

    async def _analizar_contexto(self, narracion: str, numero: int, total: int) -> dict[str, Any]:
        """Pide al LLM el contexto narrativo de una escena.

        Si el LLM falla de forma no recuperable, se registra una advertencia
        y se continúa con valores por defecto en vez de abortar todo el
        análisis por una única escena problemática (misma filosofía de
        resiliencia que ``SceneCurator`` aplica a sus propias llamadas LLM).
        """
        prompt = _PROMPT_ANALISIS_ESCENA.format(
            numero=numero,
            total_escenas=total,
            narracion=narracion,
            contexto_narrativo=self._contexto_narrativo,
        )
        try:
            resultado = await self._llm.complete_json(prompt, temperature=0.5)
        except LLMError as exc:
            logger.warning("analisis_contexto_fallo", scene_id=numero, error=str(exc))
            return {}

        return resultado if isinstance(resultado, dict) else {}

    @staticmethod
    def _cargar_checkpoint(output_path: Path) -> list[Scene]:
        if not output_path.exists():
            return []
        contenido = output_path.read_text(encoding="utf-8").strip()
        if not contenido:
            return []
        datos = json.loads(contenido)
        return [Scene.from_dict(item) for item in datos]

    @staticmethod
    def _guardar_checkpoint(output_path: Path, escenas: list[Scene]) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        datos = [escena.to_dict() for escena in escenas]
        output_path.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
