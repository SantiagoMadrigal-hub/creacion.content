"""Puerto :class:`AudioProviderPort`: contrato para inspeccionar audio local."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = ["AudioProviderPort"]


@runtime_checkable
class AudioProviderPort(Protocol):
    """Contrato para obtener metadatos de un archivo de audio local."""

    async def get_duration(self, audio_path: Path) -> float:
        """Devuelve la duración de ``audio_path``, en segundos.

        Raises:
            AssemblyError: si el archivo no existe o no se puede leer su
                duración.
        """
        ...
