"""Puerto :class:`VideoAssemblerPort`: contrato para ensamblar el video final."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from lavox.domain.entities.scene import Scene

__all__ = ["VideoAssemblerPort"]


@runtime_checkable
class VideoAssemblerPort(Protocol):
    """Contrato para el ensamblador de video final a partir de clips + audio."""

    async def assemble(
        self,
        scenes: Sequence[Scene],
        *,
        clips_dir: Path,
        audio_path: Path,
        output_path: Path,
        duracion_por_escena: float,
    ) -> Path:
        """Ensambla los clips de ``scenes`` en un único video con audio.

        Args:
            scenes: escenas ya curadas y con clip descargado, en orden.
            clips_dir: carpeta donde están los clips descargados
                (``escena_<numero>.mp4``).
            audio_path: ruta del archivo de audio (narración) a mezclar.
            output_path: ruta donde debe quedar el video final.
            duracion_por_escena: duración objetivo (segundos) de cada escena
                en el video final, ya calculada por el caso de uso.

        Returns:
            La ruta del video final ensamblado (igual a ``output_path``).

        Raises:
            AssemblyError: si el ensamblaje falla (FFmpeg, archivos
                faltantes, etc.).
        """
        ...
