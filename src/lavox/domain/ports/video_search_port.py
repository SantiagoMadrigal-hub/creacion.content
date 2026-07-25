"""Puerto :class:`VideoSearchPort`: contrato para búsqueda de video de stock."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lavox.domain.entities.clip import Clip

__all__ = ["VideoSearchPort"]


@runtime_checkable
class VideoSearchPort(Protocol):
    """Contrato para cualquier proveedor de búsqueda de video de stock."""

    async def search(self, query: str, *, per_page: int = 10) -> list[Clip]:
        """Busca clips candidatos para ``query``.

        Args:
            query: términos de búsqueda visuales (en inglés).
            per_page: número máximo de resultados a solicitar al proveedor.

        Returns:
            Lista de :class:`Clip` candidatos (sin evaluación de relevancia
            aún). Lista vacía si no hay resultados o si la búsqueda falla
            de forma recuperable tras agotar los reintentos.

        Raises:
            PexelsError: si la búsqueda falla de forma no recuperable.
        """
        ...
