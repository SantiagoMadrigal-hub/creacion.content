"""Adaptador de búsqueda de video de stock en Pexels: implementa `VideoSearchPort`."""

from __future__ import annotations

import httpx
import structlog
from pydantic import BaseModel, Field

from lavox.domain.entities.clip import Clip
from lavox.domain.exceptions import PexelsError
from lavox.infrastructure._retry import (
    RETRYABLE_HTTP_ERRORS,
    build_http_retrying,
    raise_for_retryable_status,
)

__all__ = ["PexelsSearcher"]

logger = structlog.get_logger(__name__)


class _PexelsVideoFile(BaseModel):
    """Un archivo de video concreto (una resolución/calidad) de Pexels."""

    link: str | None = None
    width: int | None = None
    height: int | None = None


class _PexelsVideo(BaseModel):
    """Un video de Pexels con sus metadatos y archivos disponibles."""

    id: int
    url: str = ""
    duration: float = 0.0
    tags: list[str] = Field(default_factory=list)
    video_files: list[_PexelsVideoFile] = Field(default_factory=list)


class _PexelsSearchResponse(BaseModel):
    """Respuesta cruda del endpoint ``/videos/search`` de Pexels."""

    videos: list[_PexelsVideo] = Field(default_factory=list)
    page: int = 1
    per_page: int = 10
    total_results: int = 0
    next_page: str | None = None


class PexelsSearcher:
    """Implementación de `VideoSearchPort` sobre la API de video de Pexels."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.pexels.com/videos/search",
        min_width: int = 1280,
        max_attempts: int = 3,
        timeout: float = 20.0,
        wait_initial: float = 1.0,
        wait_max: float = 15.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Inicializa el buscador.

        Args:
            api_key: API key de Pexels. Ignorada si se inyecta ``http_client``
                ya configurado con el header de autorización.
            base_url: URL del endpoint de búsqueda de video.
            min_width: ancho mínimo (px) exigido a los resultados.
            max_attempts: intentos máximos ante errores HTTP transitorios.
            timeout: timeout de red por request, en segundos.
            wait_initial: espera inicial (segundos) del backoff exponencial.
            wait_max: espera máxima (segundos) entre reintentos.
            http_client: cliente ``httpx.AsyncClient`` ya construido (para
                tests/inyección de dependencias). Si se omite, se crea uno
                nuevo y esta instancia se vuelve responsable de cerrarlo.
        """
        self._base_url = base_url
        self._min_width = min_width
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            headers={"Authorization": api_key}, timeout=timeout
        )
        self._retrying = build_http_retrying(
            max_attempts=max_attempts, wait_initial=wait_initial, wait_max=wait_max
        )

    async def search(self, query: str, *, per_page: int = 10, page: int = 1) -> list[Clip]:
        """Ver :meth:`VideoSearchPort.search`. Admite ``page`` para paginación."""
        try:
            data: _PexelsSearchResponse = await self._retrying(
                self._fetch_page, query, per_page, page
            )
        except RETRYABLE_HTTP_ERRORS as exc:
            logger.error("pexels_reintentos_agotados", query=query, error=str(exc))
            raise PexelsError(
                f"Pexels no respondió tras los reintentos configurados: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            logger.error("pexels_error_http", query=query, status_code=exc.response.status_code)
            raise PexelsError(f"Pexels devolvió un error no recuperable: {exc}") from exc

        clips = [
            clip
            for video in data.videos
            if (clip := self._to_domain_clip(video, query)) is not None
        ]
        logger.debug("pexels_busqueda_completada", query=query, page=page, resultados=len(clips))
        return clips

    async def search_paginated(
        self, query: str, *, per_page: int = 10, max_pages: int = 1
    ) -> list[Clip]:
        """Busca a través de varias páginas y devuelve todos los clips combinados.

        Args:
            query: términos de búsqueda.
            per_page: resultados por página.
            max_pages: número máximo de páginas a recorrer.
        """
        todos: list[Clip] = []
        for page in range(1, max_pages + 1):
            clips = await self.search(query, per_page=per_page, page=page)
            if not clips:
                break
            todos.extend(clips)
        return todos

    async def aclose(self) -> None:
        """Cierra el cliente HTTP subyacente, si esta instancia lo posee."""
        if self._owns_client:
            await self._client.aclose()

    async def _fetch_page(self, query: str, per_page: int, page: int) -> _PexelsSearchResponse:
        response = await self._client.get(
            self._base_url,
            params={
                "query": query,
                "per_page": per_page,
                "page": page,
                "orientation": "landscape",
                "min_width": self._min_width,
            },
        )
        raise_for_retryable_status(response)
        response.raise_for_status()
        return _PexelsSearchResponse.model_validate(response.json())

    def _to_domain_clip(self, video: _PexelsVideo, query: str) -> Clip | None:
        archivo = self._mejor_archivo(video.video_files)
        if archivo is None or not archivo.link:
            return None
        return Clip(
            id=video.id,
            url_descarga=archivo.link,
            tags=tuple(video.tags),
            duracion=video.duration,
            ancho=archivo.width or 0,
            alto=archivo.height or 0,
            url_pexels=video.url,
            query_origen=query,
        )

    @staticmethod
    def _mejor_archivo(video_files: list[_PexelsVideoFile]) -> _PexelsVideoFile | None:
        """Elige el archivo de mayor resolución (ancho * alto) que tenga link."""
        candidatos = [f for f in video_files if f.link]
        if not candidatos:
            return None
        return max(candidatos, key=lambda f: (f.width or 0) * (f.height or 0))
