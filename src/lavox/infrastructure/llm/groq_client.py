"""Cliente LLM para Groq: implementación concreta de :class:`LLMPort`."""

from __future__ import annotations

import asyncio

import groq
import structlog
from groq.types.chat import ChatCompletion
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from lavox.domain.exceptions import LLMError
from lavox.domain.ports.llm_port import JSONValue
from lavox.infrastructure.llm._parsing import extract_json_value

__all__ = ["GroqClient"]

logger = structlog.get_logger(__name__)

#: Errores de Groq considerados transitorios y por lo tanto reintentables:
#: conexión/timeout, rate limit (429) y errores 5xx del servidor.
_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    groq.APIConnectionError,
    groq.RateLimitError,
    groq.InternalServerError,
)


class GroqClient:
    """Implementación de :class:`LLMPort` sobre el SDK oficial de Groq.

    El SDK de Groq es síncrono; sus llamadas se despachan a un hilo con
    ``asyncio.to_thread`` para no bloquear el event loop. El
    ``max_retries`` interno del SDK se desactiva explícitamente (se pasa
    ``0``) para que exista una única política de reintentos —la de esta
    clase, basada en ``tenacity`` con backoff exponencial y jitter— en vez
    de dos capas de reintento superpuestas.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "llama-3.1-8b-instant",
        max_attempts: int = 3,
        timeout: float = 30.0,
        wait_initial: float = 1.0,
        wait_max: float = 20.0,
        client: groq.Groq | None = None,
    ) -> None:
        """Inicializa el cliente.

        Args:
            api_key: API key de Groq. Ignorada si se inyecta ``client``.
            model: modelo de chat completion a usar.
            max_attempts: número máximo de intentos ante errores recuperables.
            timeout: timeout de red por request, en segundos.
            wait_initial: espera inicial (segundos) del backoff exponencial.
            wait_max: espera máxima (segundos) entre reintentos.
            client: cliente ``groq.Groq`` ya construido (para tests/inyección
                de dependencias). Si se omite, se crea uno nuevo.
        """
        self._model = model
        self._client = client or groq.Groq(api_key=api_key, timeout=timeout, max_retries=0)
        self._retrying = AsyncRetrying(
            retry=retry_if_exception_type(_RETRYABLE_ERRORS),
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential_jitter(initial=wait_initial, max=wait_max),
            reraise=True,
        )

    async def complete(self, prompt: str, *, temperature: float = 0.5) -> str:
        """Ver :meth:`LLMPort.complete`."""
        try:
            return await self._retrying(self._complete_once, prompt, temperature)
        except _RETRYABLE_ERRORS as exc:
            logger.error("groq_reintentos_agotados", error=str(exc))
            raise LLMError(f"Groq no respondió tras los reintentos configurados: {exc}") from exc
        except groq.GroqError as exc:
            logger.error("groq_error_no_recuperable", error=str(exc))
            raise LLMError(f"Groq falló: {exc}") from exc

    async def complete_json(self, prompt: str, *, temperature: float = 0.5) -> JSONValue | None:
        """Ver :meth:`LLMPort.complete_json`."""
        texto = await self.complete(prompt, temperature=temperature)
        return extract_json_value(texto)

    async def _complete_once(self, prompt: str, temperature: float) -> str:
        response = await asyncio.to_thread(self._crear_chat_completion, prompt, temperature)
        contenido = response.choices[0].message.content
        return contenido.strip() if contenido else ""

    def _crear_chat_completion(self, prompt: str, temperature: float) -> ChatCompletion:
        return self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
