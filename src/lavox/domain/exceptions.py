"""Jerarquía de excepciones propias de LAVOX.

Todas las excepciones de negocio e infraestructura heredan de
:class:`LavoxError`, lo que permite capturarlas de forma genérica en la
capa de CLI y mapearlas a códigos de salida semánticos.
"""

from __future__ import annotations

__all__ = [
    "AssemblyError",
    "ConfigError",
    "DownloadError",
    "LLMError",
    "LavoxError",
    "PartialPipelineError",
    "PexelsError",
]


class LavoxError(Exception):
    """Excepción base de la que heredan todos los errores del dominio LAVOX."""


class ConfigError(LavoxError):
    """La configuración/entorno es inválida o le faltan valores requeridos."""


class LLMError(LavoxError):
    """Un proveedor LLM (Groq, OpenAI, ...) falló de forma no recuperable."""


class PexelsError(LavoxError):
    """La búsqueda de video de stock en Pexels falló de forma no recuperable."""


class DownloadError(LavoxError):
    """La descarga de un clip de video falló de forma no recuperable."""


class AssemblyError(LavoxError):
    """El ensamblaje del video final (FFmpeg) falló."""


class PartialPipelineError(LavoxError):
    """El pipeline completó algunas fases pero no todas (éxito parcial).

    Se usa para distinguir, en la CLI, entre un fallo total (código 2-4) y
    una ejecución que produjo resultados parciales utilizables (código 5),
    por ejemplo cuando algunas escenas no consiguieron un clip relevante
    pero el resto del pipeline pudo continuar.
    """
