"""Caso de uso: descargar los clips seleccionados para cada escena.

Reimplementa ``descargar_videos.py``, delegando en un
:class:`~lavox.domain.ports.video_downloader_port.VideoDownloaderPort` la
descarga concurrente (con reanudación y validación) de cada clip elegido
por el análisis del guion.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import structlog

from lavox.domain.entities.scene import Scene
from lavox.domain.exceptions import ConfigError, DownloadError
from lavox.domain.ports.video_downloader_port import VideoDownloaderPort

__all__ = ["DownloadClipsUseCase", "DownloadSummary"]

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DownloadSummary:
    """Resumen cuantitativo de una ejecución de descarga de clips.

    Attributes:
        total_escenas: número total de escenas en el checkpoint.
        descargados: clips descargados exitosamente en esta ejecución.
        omitidos: clips que ya existían y eran válidos (reanudación).
        fallidos: descargas que fallaron tras agotar los reintentos.
        sin_clip: escenas que no tenían ningún clip seleccionado.
    """

    total_escenas: int
    descargados: int
    omitidos: int
    fallidos: int
    sin_clip: int

    @property
    def exitosos(self) -> int:
        """Clips disponibles en disco al terminar (descargados + omitidos)."""
        return self.descargados + self.omitidos


class DownloadClipsUseCase:
    """Descarga, con concurrencia acotada, los clips seleccionados por escena."""

    def __init__(self, downloader: VideoDownloaderPort, *, max_concurrency: int = 5) -> None:
        """Inicializa el caso de uso.

        Args:
            downloader: puerto de descarga a utilizar.
            max_concurrency: número máximo de descargas simultáneas.
        """
        self._downloader = downloader
        self._max_concurrency = max_concurrency

    async def execute(
        self,
        scenes_path: Path,
        output_dir: Path,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> DownloadSummary:
        """Descarga los clips de todas las escenas en ``scenes_path``.

        Args:
            scenes_path: ruta del checkpoint JSON producido por
                ``AnalyzeScriptUseCase``.
            output_dir: carpeta donde guardar los clips (``escena_<n>.mp4``).
            on_progress: callback opcional invocado con
                ``(completados, total)`` a medida que terminan descargas.

        Returns:
            Resumen cuantitativo de la ejecución.

        Raises:
            ConfigError: si ``scenes_path`` no existe.
            DownloadError: si había clips por descargar y absolutamente
                ninguna descarga tuvo éxito (fallo catastrófico, distinto de
                un fallo parcial reflejado solo en el resumen).
        """
        if not scenes_path.exists():
            raise ConfigError(
                f"No se encontró '{scenes_path}'. Ejecuta primero el análisis del guion."
            )

        escenas = self._cargar_escenas(scenes_path)
        output_dir.mkdir(parents=True, exist_ok=True)

        pendientes: list[tuple[Scene, str, Path]] = []
        sin_clip = 0
        for escena in escenas:
            if escena.clip_seleccionado is None:
                sin_clip += 1
                logger.debug("escena_sin_clip", scene_id=escena.numero)
                continue
            destino = output_dir / f"escena_{escena.numero}.mp4"
            pendientes.append((escena, escena.clip_seleccionado.url_descarga, destino))

        logger.info(
            "descargas_iniciando", total=len(pendientes), max_concurrency=self._max_concurrency
        )

        items = [(url, destino) for _, url, destino in pendientes]
        resultados = await self._downloader.download_many(
            items, max_concurrency=self._max_concurrency, on_complete=on_progress
        )

        descargados = 0
        omitidos = 0
        fallidos = 0
        for (escena, url, _destino), resultado in zip(pendientes, resultados, strict=True):
            if isinstance(resultado, BaseException):
                fallidos += 1
                logger.error(
                    "descarga_fallida", scene_id=escena.numero, url=url, error=str(resultado)
                )
            elif resultado.omitido:
                omitidos += 1
            else:
                descargados += 1

        resumen = DownloadSummary(
            total_escenas=len(escenas),
            descargados=descargados,
            omitidos=omitidos,
            fallidos=fallidos,
            sin_clip=sin_clip,
        )
        logger.info("descargas_completadas", **asdict(resumen))

        if pendientes and resumen.exitosos == 0:
            raise DownloadError(
                f"Las {fallidos} descarga(s) intentada(s) fallaron todas; no se obtuvo ningún clip."
            )

        return resumen

    @staticmethod
    def _cargar_escenas(scenes_path: Path) -> list[Scene]:
        contenido = scenes_path.read_text(encoding="utf-8")
        datos = json.loads(contenido)
        return [Scene.from_dict(item) for item in datos]
