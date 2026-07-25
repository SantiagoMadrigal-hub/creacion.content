"""Orquestador del pipeline completo: análisis -> descarga -> ensamblaje.

Reimplementa ``crear_contenido.py``, que originalmente lanzaba tres scripts
por separado con ``subprocess``. Aquí las tres fases son casos de uso
llamados directamente en proceso, lo que permite compartir configuración,
logging estructurado con un ``correlation_id`` común, y un resultado
tipado en vez de códigos de salida de subprocesos.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

from lavox.application.use_cases.analyze_script import AnalyzeScriptUseCase
from lavox.application.use_cases.assemble_video import AssembleVideoUseCase
from lavox.application.use_cases.download_clips import DownloadClipsUseCase, DownloadSummary
from lavox.domain.entities.scene import Scene

__all__ = ["PipelineOrchestrator", "PipelineResult"]

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Resultado consolidado de una ejecución completa del pipeline.

    Un resultado siempre representa una ejecución que llegó a producir un
    video final; los fallos que impiden llegar hasta ahí se comunican como
    excepciones (``ConfigError``, ``LLMError``, ``DownloadError``,
    ``AssemblyError``), no como parte de este resultado.

    Attributes:
        escenas: todas las escenas analizadas, con o sin clip.
        download_summary: resumen cuantitativo de la fase de descarga.
        output_video_path: ruta del video final ensamblado.
    """

    escenas: list[Scene]
    download_summary: DownloadSummary
    output_video_path: Path

    @property
    def escenas_sin_clip(self) -> int:
        """Número de escenas que quedaron sin ningún clip seleccionado."""
        return sum(1 for escena in self.escenas if not escena.tiene_clip)

    @property
    def es_parcial(self) -> bool:
        """``True`` si el video se produjo, pero con degradación conocida.

        Degradación = alguna escena sin clip seleccionado, o alguna
        descarga individual fallida (aunque no todas: eso ya sería un
        ``DownloadError`` propagado desde ``DownloadClipsUseCase``).
        """
        return self.escenas_sin_clip > 0 or self.download_summary.fallidos > 0


class PipelineOrchestrator:
    """Ejecuta las tres fases del pipeline (análisis, descarga, ensamblaje)."""

    def __init__(
        self,
        analyze_use_case: AnalyzeScriptUseCase,
        download_use_case: DownloadClipsUseCase,
        assemble_use_case: AssembleVideoUseCase,
    ) -> None:
        """Inicializa el orquestador con sus tres casos de uso."""
        self._analyze = analyze_use_case
        self._download = download_use_case
        self._assemble = assemble_use_case

    async def run(
        self,
        *,
        script_path: Path,
        scenes_output_path: Path,
        clips_dir: Path,
        audio_path: Path,
        final_output_path: Path,
        resume: bool = True,
    ) -> PipelineResult:
        """Ejecuta el pipeline completo: análisis -> descarga -> ensamblaje.

        Args:
            script_path: ruta del guion de entrada.
            scenes_output_path: ruta del checkpoint JSON de escenas.
            clips_dir: carpeta donde guardar/leer los clips descargados.
            audio_path: ruta del audio de narración.
            final_output_path: ruta donde debe quedar el video final.
            resume: si continuar desde un checkpoint de escenas existente.

        Returns:
            El resultado consolidado del pipeline. Revisa
            ``resultado.es_parcial`` para saber si hubo degradación.

        Raises:
            ConfigError: si falta algún archivo de entrada requerido.
            LLMError: si el análisis narrativo falla de forma no recuperable.
            DownloadError: si absolutamente ninguna descarga tuvo éxito.
            AssemblyError: si el ensamblaje final falla.
        """
        logger.info("pipeline_iniciado", script_path=str(script_path), resume=resume)

        escenas = await self._analyze.execute(script_path, scenes_output_path, resume=resume)
        resumen_descarga = await self._download.execute(scenes_output_path, clips_dir)
        video_final = await self._assemble.execute(
            scenes_output_path, clips_dir, audio_path, final_output_path
        )

        resultado = PipelineResult(
            escenas=escenas,
            download_summary=resumen_descarga,
            output_video_path=video_final,
        )

        if resultado.es_parcial:
            logger.warning(
                "pipeline_completado_con_degradacion",
                escenas_sin_clip=resultado.escenas_sin_clip,
                descargas_fallidas=resumen_descarga.fallidos,
            )
        else:
            logger.info("pipeline_completado", output_video_path=str(video_final))

        return resultado
