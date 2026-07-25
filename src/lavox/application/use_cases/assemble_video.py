"""Caso de uso: ensamblar el video final a partir de los clips descargados.

Reimplementa ``ensamblar_video.py``: calcula cuánto debe durar cada escena
(duración total del audio de narración dividida entre el número de
escenas) y delega el ensamblaje propiamente dicho en un
:class:`~lavox.domain.ports.video_assembler_port.VideoAssemblerPort`.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from lavox.domain.entities.scene import Scene
from lavox.domain.exceptions import AssemblyError, ConfigError
from lavox.domain.ports.audio_provider_port import AudioProviderPort
from lavox.domain.ports.video_assembler_port import VideoAssemblerPort

__all__ = ["AssembleVideoUseCase"]

logger = structlog.get_logger(__name__)


class AssembleVideoUseCase:
    """Calcula la duración por escena y ensambla el video final."""

    def __init__(self, assembler: VideoAssemblerPort, audio_provider: AudioProviderPort) -> None:
        """Inicializa el caso de uso.

        Args:
            assembler: puerto que realiza el ensamblaje (típicamente FFmpeg).
            audio_provider: puerto que reporta la duración del audio.
        """
        self._assembler = assembler
        self._audio_provider = audio_provider

    async def execute(
        self, scenes_path: Path, clips_dir: Path, audio_path: Path, output_path: Path
    ) -> Path:
        """Ensambla el video final a partir de ``scenes_path`` y ``clips_dir``.

        Args:
            scenes_path: ruta del checkpoint JSON con las escenas curadas.
            clips_dir: carpeta con los clips descargados.
            audio_path: ruta del audio de narración a mezclar.
            output_path: ruta donde debe quedar el video final.

        Returns:
            La ruta del video final ensamblado.

        Raises:
            ConfigError: si falta el checkpoint de escenas o el audio.
            AssemblyError: si el checkpoint está vacío o el ensamblaje falla.
        """
        if not scenes_path.exists():
            raise ConfigError(
                f"No se encontró '{scenes_path}'. Ejecuta primero el análisis del guion."
            )
        if not audio_path.exists():
            raise ConfigError(f"No se encuentra el audio: {audio_path}")

        escenas = self._cargar_escenas(scenes_path)
        if not escenas:
            raise AssemblyError("El checkpoint de escenas está vacío; no hay nada que ensamblar.")

        duracion_total = await self._audio_provider.get_duration(audio_path)
        duracion_por_escena = duracion_total / len(escenas)
        logger.info(
            "ensamblaje_iniciando",
            escenas=len(escenas),
            duracion_audio=duracion_total,
            duracion_por_escena=duracion_por_escena,
        )

        resultado = await self._assembler.assemble(
            escenas,
            clips_dir=clips_dir,
            audio_path=audio_path,
            output_path=output_path,
            duracion_por_escena=duracion_por_escena,
        )
        logger.info("ensamblaje_completado", output_path=str(resultado))
        return resultado

    @staticmethod
    def _cargar_escenas(scenes_path: Path) -> list[Scene]:
        contenido = scenes_path.read_text(encoding="utf-8")
        datos = json.loads(contenido)
        return [Scene.from_dict(item) for item in datos]
