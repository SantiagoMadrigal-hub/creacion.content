"""Configuración de logging estructurado (`structlog`) para LAVOX.

Provee salida legible en consola durante desarrollo y JSON en producción
(para agregadores de logs), y un `correlation_id` propagado vía
`contextvars` para poder trazar todos los logs de una misma ejecución del
pipeline.
"""

from __future__ import annotations

import logging
import sys
import uuid

import structlog

__all__ = ["bind_correlation_id", "configure_logging"]


def configure_logging(*, log_level: str = "INFO", json_format: bool = False) -> None:
    """Configura `structlog` y el logging estándar para toda la aplicación.

    Args:
        log_level: nivel mínimo de log (``DEBUG``, ``INFO``, ``WARNING``,
            ``ERROR``).
        json_format: si ``True``, emite JSON (para agregadores de logs en
            producción); si ``False``, emite salida legible en consola
            (para desarrollo).
    """
    nivel = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=nivel)

    procesadores: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    procesadores.append(
        structlog.processors.JSONRenderer() if json_format else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=procesadores,
        wrapper_class=structlog.make_filtering_bound_logger(nivel),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def bind_correlation_id(correlation_id: str | None = None) -> str:
    """Genera (si hace falta) y vincula un `correlation_id` a los logs actuales.

    Args:
        correlation_id: id a usar; si es ``None``, se genera un UUID4.

    Returns:
        El ``correlation_id`` efectivamente vinculado.
    """
    cid = correlation_id or str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(correlation_id=cid)
    return cid
