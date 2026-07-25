"""Entidad :class:`Clip`: un clip de video de stock candidato o seleccionado."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Self

__all__ = ["Clip"]


@dataclass(frozen=True, slots=True)
class Clip:
    """Clip de video de stock (típicamente proveniente de Pexels).

    Es un value object inmutable: cualquier actualización (p. ej. tras
    evaluar su relevancia semántica) produce una nueva instancia mediante
    :meth:`with_evaluation`, en vez de mutar el objeto original.

    Attributes:
        id: identificador del video en el proveedor de origen.
        url_descarga: URL directa de descarga del archivo de video elegido.
        tags: etiquetas asociadas al video en el proveedor.
        duracion: duración del clip original, en segundos.
        ancho: ancho en píxeles del archivo de video elegido.
        alto: alto en píxeles del archivo de video elegido.
        url_pexels: URL de la página del video en Pexels (para atribución).
        query_origen: query de búsqueda que produjo este candidato.
        relevancia: puntaje de relevancia semántica (0-100) asignado por el LLM.
        justificacion: explicación en texto libre del puntaje de relevancia.
        query_usada: query finalmente asociada a la selección de este clip.
    """

    id: int
    url_descarga: str
    tags: tuple[str, ...] = ()
    duracion: float = 0.0
    ancho: int = 0
    alto: int = 0
    url_pexels: str = ""
    query_origen: str = ""
    relevancia: int = 0
    justificacion: str = ""
    query_usada: str = ""

    def with_evaluation(self, *, relevancia: int, justificacion: str) -> Self:
        """Devuelve una copia de este clip con su evaluación de relevancia aplicada."""
        return dataclasses.replace(self, relevancia=relevancia, justificacion=justificacion)

    def with_query_usada(self, query_usada: str) -> Self:
        """Devuelve una copia de este clip marcando la query que lo seleccionó."""
        return dataclasses.replace(self, query_usada=query_usada)

    def to_dict(self) -> dict[str, Any]:
        """Serializa el clip al mismo esquema JSON usado por el pipeline original."""
        return {
            "id": self.id,
            "url_descarga": self.url_descarga,
            "tags": list(self.tags),
            "duracion": self.duracion,
            "ancho": self.ancho,
            "alto": self.alto,
            "url_pexels": self.url_pexels,
            "query_origen": self.query_origen,
            "relevancia": self.relevancia,
            "justificacion": self.justificacion,
            "query_usada": self.query_usada,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Reconstruye un :class:`Clip` desde un dict (p. ej. checkpoint JSON)."""
        return cls(
            id=data["id"],
            url_descarga=data.get("url_descarga", ""),
            tags=tuple(data.get("tags", [])),
            duracion=data.get("duracion", 0.0),
            ancho=data.get("ancho", 0),
            alto=data.get("alto", 0),
            url_pexels=data.get("url_pexels", ""),
            query_origen=data.get("query_origen", ""),
            relevancia=data.get("relevancia", 0),
            justificacion=data.get("justificacion", ""),
            query_usada=data.get("query_usada", ""),
        )
