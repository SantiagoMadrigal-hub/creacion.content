"""Extracción tolerante de JSON desde texto de respuesta de un LLM.

Los modelos de lenguaje no siempre devuelven JSON puro: a veces lo envuelven
en explicaciones o bloques de código markdown. Estas utilidades intentan
primero un parseo directo y, si falla, recuperan el primer objeto o array
JSON balanceado por expresión regular.
"""

from __future__ import annotations

import json
import re

from lavox.domain.ports.llm_port import JSONValue

__all__ = ["extract_json_value"]

_PATRONES_JSON: tuple[str, ...] = (r"\{.*\}", r"\[.*\]")


def extract_json_value(texto: str) -> JSONValue | None:
    """Extrae un objeto o array JSON desde ``texto``, con recuperación tolerante.

    Args:
        texto: texto crudo devuelto por el LLM.

    Returns:
        El valor JSON parseado (``dict`` o ``list``), o ``None`` si no se
        pudo extraer JSON válido de ningún tipo.
    """
    if not texto or not texto.strip():
        return None

    valor_directo = _intentar_parsear(texto)
    if valor_directo is not None:
        return valor_directo

    for patron in _PATRONES_JSON:
        coincidencia = re.search(patron, texto, re.DOTALL)
        if coincidencia is None:
            continue
        valor = _intentar_parsear(coincidencia.group())
        if valor is not None:
            return valor

    return None


def _intentar_parsear(texto: str) -> JSONValue | None:
    try:
        obj = json.loads(texto)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict | list) else None
