"""Proveedor de audio local: implementa `AudioProviderPort` vía `ffprobe`."""

from __future__ import annotations

from pathlib import Path

import structlog

from lavox.domain.exceptions import AssemblyError
from lavox.infrastructure._ffprobe import probe_duration_seconds

__all__ = ["LocalAudioProvider"]

logger = structlog.get_logger(__name__)


class LocalAudioProvider:
    """Implementación de `AudioProviderPort` que lee archivos del disco local."""

    def __init__(self, *, probe_binary: str = "ffprobe", timeout: float = 30.0) -> None:
        """Inicializa el proveedor.

        Args:
            probe_binary: nombre o ruta del ejecutable ``ffprobe``.
            timeout: tiempo máximo de espera de la inspección, en segundos.
        """
        self._probe_binary = probe_binary
        self._timeout = timeout

    async def get_duration(self, audio_path: Path) -> float:
        """Ver :meth:`AudioProviderPort.get_duration`."""
        try:
            duracion = await probe_duration_seconds(
                audio_path, probe_binary=self._probe_binary, timeout=self._timeout
            )
        except FileNotFoundError as exc:
            raise AssemblyError(str(exc)) from exc
        except RuntimeError as exc:
            raise AssemblyError(f"No se pudo leer la duración de {audio_path}: {exc}") from exc

        logger.debug("duracion_audio_leida", audio_path=str(audio_path), duracion=duracion)
        return duracion
