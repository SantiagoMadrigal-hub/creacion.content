"""Entidad :class:`Scene`: una escena de video derivada de una línea del guion."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Self

from lavox.domain.entities.clip import Clip

__all__ = ["TIPOS_ESCENA_CONOCIDOS", "Scene"]

#: Valores de ``tipo_escena`` que el prompt de análisis narrativo sugiere al
#: LLM. No se valida estrictamente contra esta lista (el LLM puede producir
#: variaciones razonables), pero sirve como referencia y para tests.
TIPOS_ESCENA_CONOCIDOS: Final[tuple[str, ...]] = (
    "hook_inicio",
    "explicacion",
    "ejemplo_historico",
    "transicion",
    "giro_revelacion",
    "reflexion_final",
    "pregunta_cierre",
)


@dataclass(slots=True)
class Scene:
    """Escena de video: una línea de narración analizada y curada.

    A diferencia de :class:`~lavox.domain.entities.clip.Clip`, ``Scene`` es
    mutable de forma controlada: evoluciona a medida que avanza el pipeline
    (se le asignan variaciones de búsqueda intentadas, un clip seleccionado
    y un contador de reintentos), por lo que se modela como una entidad con
    identidad (``numero``) en vez de como un value object.

    Attributes:
        numero: posición de la escena dentro del guion (1-indexado).
        narracion: texto de narración de la escena.
        tema_principal: tema central de la escena (2-5 palabras).
        emocion_tono: tono emocional dominante.
        tipo_escena: tipo narrativo de la escena (ver `TIPOS_ESCENA_CONOCIDOS`).
        elementos_clave: elementos visuales concretos en inglés, usados para
            construir queries de búsqueda de video de stock.
        variaciones_intentadas: todas las queries de búsqueda probadas.
        clip_seleccionado: clip final elegido para esta escena, si lo hay.
        reintentos: número de reintentos de curación adicionales realizados.
    """

    numero: int
    narracion: str
    tema_principal: str = "video scene"
    emocion_tono: str = "neutral"
    tipo_escena: str = "explicacion"
    elementos_clave: list[str] = field(default_factory=lambda: ["video footage"])
    variaciones_intentadas: list[str] = field(default_factory=list)
    clip_seleccionado: Clip | None = None
    reintentos: int = 0

    @property
    def tiene_clip(self) -> bool:
        """Indica si la escena ya tiene un clip de video asignado."""
        return self.clip_seleccionado is not None

    def to_dict(self) -> dict[str, Any]:
        """Serializa la escena al mismo esquema JSON usado por el pipeline original.

        Este esquema es compatible con los archivos ``escenas_con_videos.json``
        generados por la versión anterior del pipeline, de forma que un
        checkpoint existente pueda seguir usándose como punto de reanudación.
        """
        clip = self.clip_seleccionado
        return {
            "tema_principal": self.tema_principal,
            "emocion_tono": self.emocion_tono,
            "tipo_escena": self.tipo_escena,
            "elementos_clave": list(self.elementos_clave),
            "narracion": self.narracion,
            "numero": self.numero,
            "variaciones_intentadas": list(self.variaciones_intentadas),
            "clip_seleccionado": clip.to_dict() if clip else None,
            "reintentos": self.reintentos,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Reconstruye una :class:`Scene` desde un dict (p. ej. checkpoint JSON)."""
        clip_data = data.get("clip_seleccionado")
        return cls(
            numero=data["numero"],
            narracion=data.get("narracion", ""),
            tema_principal=data.get("tema_principal", "video scene"),
            emocion_tono=data.get("emocion_tono", "neutral"),
            tipo_escena=data.get("tipo_escena", "explicacion"),
            elementos_clave=list(data.get("elementos_clave", ["video footage"])),
            variaciones_intentadas=list(data.get("variaciones_intentadas", [])),
            clip_seleccionado=Clip.from_dict(clip_data) if clip_data else None,
            reintentos=data.get("reintentos", 0),
        )
