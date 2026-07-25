"""Puerto :class:`LLMPort`: contrato para cualquier proveedor de LLM."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

__all__ = ["JSONValue", "LLMPort"]

#: Forma esperada de una respuesta LLM ya parseada como JSON: o bien un
#: objeto, o bien un array. Los prompts del dominio siempre piden uno de
#: los dos, nunca un escalar suelto.
JSONValue = dict[str, Any] | list[Any]


@runtime_checkable
class LLMPort(Protocol):
    """Contrato que debe cumplir cualquier cliente de modelo de lenguaje.

    Las implementaciones concretas (Groq, OpenAI, fallback compuesto) viven
    en :mod:`lavox.infrastructure.llm` y son responsables de su propia
    política de reintentos; este puerto solo expone la interfaz que el
    dominio necesita para razonar sobre el resultado.
    """

    async def complete(self, prompt: str, *, temperature: float = 0.5) -> str:
        """Devuelve la respuesta de texto plano del modelo para ``prompt``.

        Raises:
            LLMError: si el proveedor falla de forma no recuperable.
        """
        ...

    async def complete_json(self, prompt: str, *, temperature: float = 0.5) -> JSONValue | None:
        """Devuelve la respuesta del modelo ya parseada como JSON.

        Es tolerante a que el modelo envuelva el JSON en texto adicional o
        en bloques de código markdown. Devuelve ``None`` si, tras los
        intentos de extracción, no se pudo obtener JSON válido.

        Raises:
            LLMError: si el proveedor falla de forma no recuperable.
        """
        ...
