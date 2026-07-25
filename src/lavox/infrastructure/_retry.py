"""Utilidades HTTP de reintento compartidas por los adaptadores de infraestructura.

Centraliza la política de reintento (backoff exponencial con jitter) y la
clasificación de qué respuestas HTTP se consideran transitorias (429, 5xx)
para que ``PexelsSearcher`` y ``AsyncDownloader`` compartan un único
comportamiento en vez de duplicar la lógica.
"""

from __future__ import annotations

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

__all__ = [
    "RETRYABLE_HTTP_ERRORS",
    "RetryableStatusError",
    "build_http_retrying",
    "raise_for_retryable_status",
]


class RetryableStatusError(Exception):
    """Envuelve una respuesta HTTP con status transitorio (429 o 5xx)."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"HTTP {response.status_code} en {response.request.url}")
        self.response = response


#: `httpx.TransportError` cubre errores de conexión y de timeout (son
#: subclases suyas `ConnectError`, `ReadTimeout`, `PoolTimeout`, etc.).
RETRYABLE_HTTP_ERRORS: tuple[type[Exception], ...] = (httpx.TransportError, RetryableStatusError)


def raise_for_retryable_status(response: httpx.Response) -> None:
    """Lanza :class:`RetryableStatusError` si el status HTTP es 429 o >= 500."""
    if response.status_code == 429 or response.status_code >= 500:
        raise RetryableStatusError(response)


def build_http_retrying(
    *, max_attempts: int, wait_initial: float = 1.0, wait_max: float = 20.0
) -> AsyncRetrying:
    """Construye una política ``AsyncRetrying`` para errores HTTP transitorios."""
    return AsyncRetrying(
        retry=retry_if_exception_type(RETRYABLE_HTTP_ERRORS),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=wait_initial, max=wait_max),
        reraise=True,
    )
