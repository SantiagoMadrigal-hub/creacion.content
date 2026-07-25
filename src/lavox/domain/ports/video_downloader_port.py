"""Puerto :class:`VideoDownloaderPort`: contrato para descarga de video."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = ["DownloadResult", "VideoDownloaderPort"]


@dataclass(frozen=True, slots=True)
class DownloadResult:
    """Resultado de una descarga individual.

    Attributes:
        destino: ruta local donde quedó el archivo descargado.
        tamano_bytes: tamaño final del archivo, en bytes.
        omitido: ``True`` si el archivo ya existía y era válido, por lo que
            se omitió la descarga (soporte de reanudación).
    """

    destino: Path
    tamano_bytes: int
    omitido: bool = False


@runtime_checkable
class VideoDownloaderPort(Protocol):
    """Contrato para cualquier mecanismo de descarga de archivos de video."""

    async def download(self, url: str, destino: Path) -> DownloadResult:
        """Descarga ``url`` hacia ``destino``, con reanudación y validación.

        Raises:
            DownloadError: si la descarga falla de forma no recuperable o el
                archivo resultante no supera la validación mínima de tamaño.
        """
        ...

    async def download_many(
        self,
        items: Sequence[tuple[str, Path]],
        *,
        max_concurrency: int = 5,
        on_complete: Callable[[int, int], None] | None = None,
    ) -> list[DownloadResult | BaseException]:
        """Descarga varios archivos con concurrencia acotada.

        Un fallo individual no debe cancelar el resto de descargas: cada
        posición del resultado es un :class:`DownloadResult` si tuvo éxito,
        o la excepción capturada si falló (equivalente a
        ``asyncio.gather(..., return_exceptions=True)``).

        Args:
            items: pares ``(url, destino)`` a descargar.
            max_concurrency: número máximo de descargas simultáneas.
            on_complete: callback opcional invocado con
                ``(completados, total)`` cada vez que una descarga termina
                (con éxito o con error), para reportar progreso.
        """
        ...
