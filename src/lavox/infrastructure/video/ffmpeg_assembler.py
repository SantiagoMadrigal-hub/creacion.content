"""Ensamblador de video final basado en FFmpeg: implementa `VideoAssemblerPort`.

Reproduce el pipeline de tres fases del ``ensamblar_video.py`` original:

1. Por cada escena, normalizar su clip a la resolución/duración objetivo
   (letterbox 16:9 + recorte, o loop + recorte si el clip es más corto que
   la porción de audio que le corresponde).
2. Concatenar todos los clips normalizados sin recodificar (concat demuxer).
3. Mezclar el video concatenado con el audio de narración.

A diferencia del original, los procesos de FFmpeg se lanzan con
``asyncio.create_subprocess_exec`` para no bloquear el event loop, y la
duración de cada clip se obtiene con ``ffprobe`` (ver
:mod:`lavox.infrastructure._ffprobe`) en vez de parsear la salida de texto
de ``ffmpeg``.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

import structlog

from lavox.domain.entities.scene import Scene
from lavox.domain.exceptions import AssemblyError
from lavox.infrastructure._ffprobe import probe_duration_seconds

__all__ = ["FFmpegAssembler"]

logger = structlog.get_logger(__name__)


class FFmpegAssembler:
    """Implementación de `VideoAssemblerPort` sobre binarios `ffmpeg`/`ffprobe`."""

    def __init__(
        self,
        *,
        binary: str = "ffmpeg",
        probe_binary: str = "ffprobe",
        resolution_width: int = 1920,
        resolution_height: int = 1080,
        preset: str = "fast",
        crf: int = 23,
        process_timeout: float = 120.0,
    ) -> None:
        """Inicializa el ensamblador.

        Args:
            binary: nombre o ruta del ejecutable ``ffmpeg``.
            probe_binary: nombre o ruta del ejecutable ``ffprobe``.
            resolution_width: ancho objetivo del video final, en píxeles.
            resolution_height: alto objetivo del video final, en píxeles.
            preset: preset de velocidad/calidad de x264.
            crf: factor de calidad constante de x264 (0-51, menor = mejor).
            process_timeout: tiempo máximo de espera por proceso, en segundos.
        """
        self._binary = binary
        self._probe_binary = probe_binary
        self._width = resolution_width
        self._height = resolution_height
        self._preset = preset
        self._crf = crf
        self._timeout = process_timeout

    async def assemble(
        self,
        scenes: Sequence[Scene],
        *,
        clips_dir: Path,
        audio_path: Path,
        output_path: Path,
        duracion_por_escena: float,
    ) -> Path:
        """Ver :meth:`VideoAssemblerPort.assemble`."""
        if not audio_path.exists():
            raise AssemblyError(f"No se encuentra el archivo de audio: {audio_path}")
        if not scenes:
            raise AssemblyError("No hay escenas para ensamblar.")

        temp_dir = Path(tempfile.mkdtemp(prefix="lavox_"))
        try:
            clips_normalizados = await self._procesar_escenas(
                scenes, clips_dir, temp_dir, duracion_por_escena
            )
            if not clips_normalizados:
                raise AssemblyError("No hay clips procesados para ensamblar.")

            video_sin_audio = temp_dir / "sin_audio.mp4"
            await self._concatenar(clips_normalizados, temp_dir, video_sin_audio)
            await self._mezclar_audio(video_sin_audio, audio_path, output_path)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        logger.info("video_ensamblado", output_path=str(output_path), escenas=len(scenes))
        return output_path

    async def _procesar_escenas(
        self,
        scenes: Sequence[Scene],
        clips_dir: Path,
        temp_dir: Path,
        duracion_por_escena: float,
    ) -> list[Path]:
        procesados: list[Path] = []
        for scene in scenes:
            entrada = clips_dir / f"escena_{scene.numero}.mp4"
            if not entrada.exists():
                logger.warning("clip_no_encontrado", scene_id=scene.numero, entrada=str(entrada))
                continue

            try:
                duracion_clip = await probe_duration_seconds(
                    entrada, probe_binary=self._probe_binary, timeout=self._timeout
                )
            except (FileNotFoundError, RuntimeError) as exc:
                logger.error("no_se_pudo_leer_duracion", scene_id=scene.numero, error=str(exc))
                continue

            if duracion_clip <= 0:
                logger.error("duracion_invalida", scene_id=scene.numero, duracion=duracion_clip)
                continue

            salida = temp_dir / f"s{scene.numero}.mp4"
            try:
                await self._normalizar_clip(entrada, salida, duracion_clip, duracion_por_escena)
            except AssemblyError as exc:
                logger.error("normalizacion_de_clip_fallo", scene_id=scene.numero, error=str(exc))
                continue

            procesados.append(salida)
            logger.debug(
                "escena_normalizada",
                scene_id=scene.numero,
                duracion_original=duracion_clip,
                duracion_objetivo=duracion_por_escena,
            )

        return procesados

    async def _normalizar_clip(
        self, entrada: Path, salida: Path, duracion_clip: float, duracion_objetivo: float
    ) -> None:
        """Escala + letterbox el clip y lo recorta (o repite en loop) a la duración objetivo."""
        filtro_escala = (
            f"scale={self._width}:{self._height}:force_original_aspect_ratio=1,"
            f"pad={self._width}:{self._height}:(ow-iw)/2:(oh-ih)/2"
        )
        args = [self._binary, "-y"]
        if duracion_clip < duracion_objetivo:
            args += ["-stream_loop", "-1"]
        args += [
            "-i",
            str(entrada),
            "-t",
            str(duracion_objetivo),
            "-vf",
            filtro_escala,
            "-c:v",
            "libx264",
            "-preset",
            self._preset,
            "-crf",
            str(self._crf),
            "-an",
            str(salida),
        ]
        await self._ejecutar(args, contexto=f"normalizar '{entrada.name}'")

    async def _concatenar(self, clips: list[Path], temp_dir: Path, salida: Path) -> None:
        """Concatena `clips` (ya normalizados) sin recodificar, vía concat demuxer."""
        lista = temp_dir / "lista.txt"
        contenido = "\n".join(f"file '{clip.resolve()}'" for clip in clips)
        lista.write_text(contenido, encoding="utf-8")

        args = [
            self._binary,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lista),
            "-c:v",
            "copy",
            str(salida),
        ]
        await self._ejecutar(args, contexto="concatenar clips")

    async def _mezclar_audio(self, video_sin_audio: Path, audio_path: Path, salida: Path) -> None:
        """Mezcla `video_sin_audio` con `audio_path`, recodificando solo el audio."""
        args = [
            self._binary,
            "-y",
            "-i",
            str(video_sin_audio),
            "-i",
            str(audio_path),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(salida),
        ]
        await self._ejecutar(args, contexto="mezclar audio")

    async def _ejecutar(self, args: list[str], *, contexto: str) -> None:
        try:
            proceso = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise AssemblyError(f"No se encontró el ejecutable '{args[0]}' al {contexto}") from exc

        try:
            _, stderr = await asyncio.wait_for(proceso.communicate(), timeout=self._timeout)
        except TimeoutError as exc:
            proceso.kill()
            await proceso.wait()
            raise AssemblyError(
                f"FFmpeg superó el timeout de {self._timeout}s al {contexto}"
            ) from exc

        if proceso.returncode != 0:
            mensaje_error = stderr.decode(errors="replace")[-500:]
            raise AssemblyError(f"FFmpeg falló al {contexto}: {mensaje_error}")
