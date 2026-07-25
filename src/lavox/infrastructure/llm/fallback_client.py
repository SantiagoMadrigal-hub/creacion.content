"""Cliente LLM compuesto: intenta un proveedor primario y cae a uno secundario."""

from __future__ import annotations

import structlog

from lavox.domain.exceptions import LLMError
from lavox.domain.ports.llm_port import JSONValue, LLMPort

__all__ = ["FallbackClient"]

logger = structlog.get_logger(__name__)


class FallbackClient:
    """Compone dos :class:`LLMPort`: intenta el primario, cae al secundario.

    Cada proveedor concreto (``GroqClient``, ``OpenAIClient``) ya gestiona
    sus propios reintentos ante errores recuperables. ``FallbackClient``
    solo decide qué hacer cuando el primario agota sus reintentos o falla
    de forma no recuperable: repite la petición completa contra el
    secundario, si hay uno configurado. Al implementar la misma interfaz
    ``LLMPort``, puede usarse en cualquier lugar donde se espere un
    proveedor LLM único.
    """

    def __init__(self, primary: LLMPort, secondary: LLMPort | None = None) -> None:
        """Inicializa el cliente compuesto.

        Args:
            primary: proveedor LLM a intentar primero.
            secondary: proveedor de respaldo, opcional. Si es ``None``, los
                errores del primario se propagan sin fallback.
        """
        self._primary = primary
        self._secondary = secondary

    async def complete(self, prompt: str, *, temperature: float = 0.5) -> str:
        """Ver :meth:`LLMPort.complete`."""
        try:
            return await self._primary.complete(prompt, temperature=temperature)
        except LLMError as exc:
            if self._secondary is None:
                raise
            logger.warning("llm_fallback_activado", error=str(exc))
            return await self._secondary.complete(prompt, temperature=temperature)

    async def complete_json(self, prompt: str, *, temperature: float = 0.5) -> JSONValue | None:
        """Ver :meth:`LLMPort.complete_json`."""
        try:
            return await self._primary.complete_json(prompt, temperature=temperature)
        except LLMError as exc:
            if self._secondary is None:
                raise
            logger.warning("llm_fallback_activado", error=str(exc))
            return await self._secondary.complete_json(prompt, temperature=temperature)
