"""Descargador asíncrono de clips de video: implementa `VideoDownloaderPort`."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx
import structlog

from lavox.domain.exceptions import DownloadError
from lavox.domain.ports.video_downloader_port import DownloadResult
from lavox.infrastructure._retry import (
    RETRYABLE_HTTP_ERRORS,
    build_http_retrying,
    raise_for_retryable_status,
)

__all__ = ["AsyncDownloader"]

logger = structlog.get_logger(__name__)

#: Un archivo descargado con menos bytes que esto se considera corrupto
#: (igual que en el pipeline original, que usaba el mismo umbral tanto para
#: decidir si reanudar como para validar una descarga terminada).
_TAMANO_MINIMO_VALIDO_BYTES = 1024
_CHUNK_SIZE_BYTES = 1024 * 1024


class AsyncDownloader:
    """Implementación de `VideoDownloaderPort` sobre `httpx.AsyncClient`.

    Soporta reanudación (omite archivos ya descargados y válidos),
    validación de tamaño mínimo con limpieza en caso de error, reintentos
    con backoff exponencial ante fallos transitorios, y descarga de varios
    archivos con concurrencia acotada mediante `download_many`.
    """

    def __init__(
        self,
        *,
        max_attempts: int = 3,
        timeout: float = 60.0,
        chunk_size: int = _CHUNK_SIZE_BYTES,
        wait_initial: float = 1.0,
        wait_max: float = 20.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Inicializa el descargador.

        Args:
            max_attempts: intentos máximos ante errores HTTP transitorios.
            timeout: timeout de red por request, en segundos.
            chunk_size: tamaño de los bloques de escritura a disco, en bytes.
            wait_initial: espera inicial (segundos) del backoff exponencial.
            wait_max: espera máxima (segundos) entre reintentos.
            http_client: cliente ``httpx.AsyncClient`` ya construido (para
                tests/inyección de dependencias). Si se omite, se crea uno
                nuevo y esta instancia se vuelve responsable de cerrarlo.
        """
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout, follow_redirects=True
        )
        self._retrying = build_http_retrying(
            max_attempts=max_attempts, wait_initial=wait_initial, wait_max=wait_max
        )
        self._chunk_size = chunk_size

    async def download(self, url: str, destino: Path) -> DownloadResult:
        """Ver :meth:`VideoDownloaderPort.download`."""
        if destino.exists() and destino.stat().st_size > _TAMANO_MINIMO_VALIDO_BYTES:
            logger.debug("descarga_omitida_ya_existe", destino=str(destino))
            return DownloadResult(
                destino=destino, tamano_bytes=destino.stat().st_size, omitido=True
            )

        destino.parent.mkdir(parents=True, exist_ok=True)
        try:
            tamano: int = await self._retrying(self._descargar_una_vez, url, destino)
        except RETRYABLE_HTTP_ERRORS as exc:
            self._limpiar(destino)
            logger.error("descarga_reintentos_agotados", url=url, error=str(exc))
            raise DownloadError(
                f"Descarga de {url} falló tras los reintentos configurados: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            self._limpiar(destino)
            logger.error("descarga_error_http", url=url, status_code=exc.response.status_code)
            raise DownloadError(
                f"Descarga de {url} devolvió un error no recuperable: {exc}"
            ) from exc

        if tamano < _TAMANO_MINIMO_VALIDO_BYTES:
            self._limpiar(destino)
            raise DownloadError(
                f"Archivo descargado de {url} es demasiado pequeño ({tamano} bytes), "
                "probablemente corrupto"
            )

        logger.info("descarga_completada", url=url, destino=str(destino), bytes=tamano)
        return DownloadResult(destino=destino, tamano_bytes=tamano, omitido=False)

    async def download_many(
        self,
        items: Sequence[tuple[str, Path]],
        *,
        max_concurrency: int = 5,
        on_complete: Callable[[int, int], None] | None = None,
    ) -> list[DownloadResult | BaseException]:
        """Descarga varios archivos con concurrencia acotada por un semáforo.

        Usa ``asyncio.gather(..., return_exceptions=True)`` para que un
        fallo individual no cancele el resto de descargas en curso.

        Args:
            items: pares ``(url, destino)`` a descargar.
            max_concurrency: número máximo de descargas simultáneas.
            on_complete: callback opcional invocado con
                ``(completados, total)`` cada vez que una descarga termina,
                con éxito o con error.

        Returns:
            Lista alineada con ``items``: cada posición es un
            :class:`DownloadResult` si tuvo éxito, o la excepción capturada
            si falló.
        """
        semaforo = asyncio.Semaphore(max_concurrency)
        total = len(items)
        completados = 0

        async def _con_limite(url: str, destino: Path) -> DownloadResult:
            nonlocal completados
            async with semaforo:
                try:
                    resultado = await self.download(url, destino)
                finally:
                    completados += 1
                    if on_complete is not None:
                        try:
                            on_complete(completados, total)
                        except Exception:
                            # Un callback de progreso roto nunca debe corromper
                            # el resultado real de la descarga.
                            logger.warning("on_complete_callback_fallo", url=url)
                return resultado

        tareas = [_con_limite(url, destino) for url, destino in items]
        resultados: list[DownloadResult | BaseException] = await asyncio.gather(
            *tareas, return_exceptions=True
        )
        return resultados

    async def aclose(self) -> None:
        """Cierra el cliente HTTP subyacente, si esta instancia lo posee."""
        if self._owns_client:
            await self._client.aclose()

    async def _descargar_una_vez(self, url: str, destino: Path) -> int:
        escrito = 0
        async with self._client.stream("GET", url) as response:
            raise_for_retryable_status(response)
            response.raise_for_status()
            with destino.open("wb") as archivo:
                async for chunk in response.aiter_bytes(self._chunk_size):
                    archivo.write(chunk)
                    escrito += len(chunk)
        return escrito

    @staticmethod
    def _limpiar(destino: Path) -> None:
        if destino.exists():
            destino.unlink(missing_ok=True)
