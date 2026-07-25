"""Utilidad compartida para invocar `ffprobe` y leer la duración de un archivo.

Reemplaza dos mecanismos frágiles del pipeline original: la dependencia de
``moviepy`` solo para leer la duración del audio, y el parseo por expresión
regular de la línea ``Duration:`` en la salida de texto de ``ffmpeg`` para
los clips de video. ``ffprobe -show_format`` con salida JSON es la forma
soportada y estable de obtener este dato para cualquier archivo de
audio o video.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

__all__ = ["probe_duration_seconds"]


async def probe_duration_seconds(
    path: Path, *, probe_binary: str = "ffprobe", timeout: float = 30.0
) -> float:
    """Devuelve la duración de un archivo de audio/video, en segundos.

    Args:
        path: ruta del archivo a inspeccionar.
        probe_binary: nombre o ruta del ejecutable ``ffprobe``.
        timeout: tiempo máximo de espera, en segundos.

    Returns:
        Duración del archivo, en segundos.

    Raises:
        FileNotFoundError: si ``path`` no existe.
        RuntimeError: si ``ffprobe`` falla, se agota el timeout, o la salida
            no contiene una duración interpretable.
    """
    if not path.exists():
        raise FileNotFoundError(f"No se encontró el archivo: {path}")

    proceso = await asyncio.create_subprocess_exec(
        probe_binary,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proceso.communicate(), timeout=timeout)
    except TimeoutError as exc:
        proceso.kill()
        await proceso.wait()
        raise RuntimeError(f"ffprobe superó el timeout de {timeout}s para {path}") from exc

    if proceso.returncode != 0:
        raise RuntimeError(f"ffprobe falló para {path}: {stderr.decode(errors='replace')}")

    try:
        data = json.loads(stdout)
        return float(data["format"]["duration"])
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise RuntimeError(
            f"No se pudo leer la duración de {path} desde la salida de ffprobe"
        ) from exc
