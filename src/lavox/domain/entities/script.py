"""Entidad :class:`Script`: el guion narrativo de entrada del pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

__all__ = ["Script"]


@dataclass(frozen=True, slots=True)
class Script:
    """Guion narrativo, como secuencia de líneas no vacías.

    Attributes:
        lineas: líneas del guion, ya despojadas de espacios y sin líneas
            vacías, en el orden original del archivo fuente.
    """

    lineas: tuple[str, ...]

    @classmethod
    def from_text(cls, texto: str) -> Self:
        r"""Construye un :class:`Script` a partir del contenido crudo del archivo.

        Args:
            texto: contenido completo de ``guion.txt`` (puede usar
                terminadores de línea ``\n`` o ``\r\n``).
        """
        lineas = tuple(linea.strip() for linea in texto.splitlines() if linea.strip())
        return cls(lineas=lineas)

    def agrupar(self, *, min_length: int = 60, max_scenes_threshold: int = 45) -> tuple[str, ...]:
        """Fusiona líneas cortas con la siguiente para reducir el número de escenas.

        Regla de negocio original: si el guion ya tiene pocas líneas
        (``<= max_scenes_threshold``), no se agrupa nada. En caso contrario,
        toda línea con menos de ``min_length`` caracteres se concatena con
        la línea siguiente, de forma sucesiva, hasta formar un grupo de
        longitud suficiente.

        Args:
            min_length: longitud mínima (en caracteres) para que una línea
                se considere completa y no se siga fusionando.
            max_scenes_threshold: número de líneas por debajo del cual no
                se aplica ninguna agrupación.

        Returns:
            Tupla de líneas agrupadas, lista para convertirse en escenas.
        """
        if not self.lineas:
            return ()
        if len(self.lineas) <= max_scenes_threshold:
            return self.lineas

        grupos: list[str] = []
        actual = self.lineas[0]
        for linea in self.lineas[1:]:
            if len(actual) < min_length:
                actual = f"{actual} {linea}"
            else:
                grupos.append(actual)
                actual = linea
        if actual:
            grupos.append(actual)
        return tuple(grupos)
