"""Cliente LLM para OpenAI: implementación concreta de :class:`LLMPort`."""

from __future__ import annotations

import asyncio

import openai
import structlog
from openai.types.chat import ChatCompletion
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from lavox.domain.exceptions import LLMError
from lavox.domain.ports.llm_port import JSONValue
from lavox.infrastructure.llm._parsing import extract_json_value

__all__ = ["OpenAIClient"]

logger = structlog.get_logger(__name__)

#: Ver el comentario equivalente en ``groq_client.py``: conexión/timeout,
#: rate limit (429) y errores 5xx del servidor se consideran reintentables.
_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)


class OpenAIClient:
    """Implementación de :class:`LLMPort` sobre el SDK oficial de OpenAI.

    Ver :class:`~lavox.infrastructure.llm.groq_client.GroqClient` para el
    razonamiento detrás de ``asyncio.to_thread`` y de desactivar el
    ``max_retries`` interno del SDK.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gpt-4o-mini",
        max_attempts: int = 3,
        timeout: float = 30.0,
        wait_initial: float = 1.0,
        wait_max: float = 20.0,
        client: openai.OpenAI | None = None,
    ) -> None:
        """Inicializa el cliente.

        Args:
            api_key: API key de OpenAI. Ignorada si se inyecta ``client``.
            model: modelo de chat completion a usar.
            max_attempts: número máximo de intentos ante errores recuperables.
            timeout: timeout de red por request, en segundos.
            wait_initial: espera inicial (segundos) del backoff exponencial.
            wait_max: espera máxima (segundos) entre reintentos.
            client: cliente ``openai.OpenAI`` ya construido (para
                tests/inyección de dependencias). Si se omite, se crea uno nuevo.
        """
        self._model = model
        self._client = client or openai.OpenAI(api_key=api_key, timeout=timeout, max_retries=0)
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
            logger.error("openai_reintentos_agotados", error=str(exc))
            raise LLMError(f"OpenAI no respondió tras los reintentos configurados: {exc}") from exc
        except openai.OpenAIError as exc:
            logger.error("openai_error_no_recuperable", error=str(exc))
            raise LLMError(f"OpenAI falló: {exc}") from exc

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
