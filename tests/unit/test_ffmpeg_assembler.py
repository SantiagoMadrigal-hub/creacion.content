"""Tests de :class:`FFmpegAssembler`, usando ffmpeg real sobre clips sintéticos.

Casos de uso cubiertos: `FFmpegAssembler | letterbox scaling, concat demuxer,
audio mux, cleanup temp`. Se prefiere generar clips reales muy pequeños (con
el generador de patrones `lavfi` de ffmpeg) en vez de mockear subprocess:
así se ejercita la construcción real de los comandos de FFmpeg, no solo que
se llamó a algo.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from lavox.domain.entities.scene import Scene
from lavox.domain.exceptions import AssemblyError
from lavox.infrastructure.video.ffmpeg_assembler import FFmpegAssembler

pytestmark = pytest.mark.slow


async def _generar_clip(destino: Path, *, duracion: float, size: str = "160x120") -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duracion}:size={size}:rate=10",
        "-y",
        str(destino),
    )
    await proc.wait()


async def _generar_audio(destino: Path, *, duracion: float) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duracion}",
        "-y",
        str(destino),
    )
    await proc.wait()


async def _inspeccionar(path: Path) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return json.loads(stdout)  # type: ignore[no-any-return]


@pytest.fixture
def assembler() -> FFmpegAssembler:
    return FFmpegAssembler(
        resolution_width=160, resolution_height=120, preset="ultrafast", process_timeout=30
    )


@pytest.fixture
async def clips_dir(tmp_path: Path) -> Path:
    """Carpeta con dos clips sintéticos: uno de 1s (más corto que el objetivo) y otro de 3s."""
    carpeta = tmp_path / "videos"
    carpeta.mkdir()
    await _generar_clip(carpeta / "escena_1.mp4", duracion=1)
    await _generar_clip(carpeta / "escena_2.mp4", duracion=3)
    return carpeta


@pytest.fixture
async def audio_4s(tmp_path: Path) -> Path:
    destino = tmp_path / "audio.mp3"
    await _generar_audio(destino, duracion=4)
    return destino


class TestEnsamblajeCompleto:
    async def test_letterbox_loop_trim_concat_y_audio_mux(
        self, assembler: FFmpegAssembler, clips_dir: Path, audio_4s: Path, tmp_path: Path
    ) -> None:
        escenas = [Scene(numero=1, narracion="uno"), Scene(numero=2, narracion="dos")]
        salida = tmp_path / "final.mp4"

        resultado = await assembler.assemble(
            escenas,
            clips_dir=clips_dir,
            audio_path=audio_4s,
            output_path=salida,
            duracion_por_escena=2.0,
        )

        assert resultado == salida
        assert salida.exists()

        info = await _inspeccionar(salida)
        tipos_stream = {s["codec_type"] for s in info["streams"]}
        assert tipos_stream == {"video", "audio"}
        # Escena 1 (clip 1s) tuvo que repetirse en loop; escena 2 (clip 3s) se recortó.
        # Ambas escenas duran 2s -> el video concatenado dura ~4s (limitado por -shortest).
        assert float(info["format"]["duration"]) == pytest.approx(4.0, abs=0.2)

        video_stream = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert video_stream["width"] == 160
        assert video_stream["height"] == 120

    async def test_limpia_el_directorio_temporal(
        self, assembler: FFmpegAssembler, clips_dir: Path, audio_4s: Path, tmp_path: Path
    ) -> None:
        temp_dir_raiz = Path(tempfile.gettempdir())
        antes = set(temp_dir_raiz.glob("lavox_*"))

        escenas = [Scene(numero=1, narracion="uno"), Scene(numero=2, narracion="dos")]
        await assembler.assemble(
            escenas,
            clips_dir=clips_dir,
            audio_path=audio_4s,
            output_path=tmp_path / "final.mp4",
            duracion_por_escena=2.0,
        )

        despues = set(temp_dir_raiz.glob("lavox_*"))
        assert despues == antes  # ningún directorio temporal nuevo quedó atrás

    async def test_escena_sin_clip_se_omite_pero_las_demas_continuan(
        self, assembler: FFmpegAssembler, clips_dir: Path, audio_4s: Path, tmp_path: Path
    ) -> None:
        escenas = [
            Scene(numero=1, narracion="uno"),
            Scene(numero=99, narracion="no tiene clip descargado"),
            Scene(numero=2, narracion="dos"),
        ]
        salida = tmp_path / "final.mp4"

        resultado = await assembler.assemble(
            escenas,
            clips_dir=clips_dir,
            audio_path=audio_4s,
            output_path=salida,
            duracion_por_escena=2.0,
        )

        assert resultado.exists()


class TestValidaciones:
    async def test_falla_si_no_existe_el_audio(
        self, assembler: FFmpegAssembler, clips_dir: Path, tmp_path: Path
    ) -> None:
        with pytest.raises(AssemblyError):
            await assembler.assemble(
                [Scene(numero=1, narracion="uno")],
                clips_dir=clips_dir,
                audio_path=tmp_path / "no_existe.mp3",
                output_path=tmp_path / "final.mp4",
                duracion_por_escena=2.0,
            )

    async def test_falla_si_no_hay_escenas(
        self, assembler: FFmpegAssembler, audio_4s: Path, tmp_path: Path
    ) -> None:
        with pytest.raises(AssemblyError):
            await assembler.assemble(
                [],
                clips_dir=tmp_path,
                audio_path=audio_4s,
                output_path=tmp_path / "final.mp4",
                duracion_por_escena=2.0,
            )

    async def test_falla_si_ningun_clip_se_pudo_procesar(
        self, assembler: FFmpegAssembler, audio_4s: Path, tmp_path: Path
    ) -> None:
        carpeta_vacia = tmp_path / "videos_vacios"
        carpeta_vacia.mkdir()

        with pytest.raises(AssemblyError, match="No hay clips procesados"):
            await assembler.assemble(
                [Scene(numero=1, narracion="uno")],
                clips_dir=carpeta_vacia,
                audio_path=audio_4s,
                output_path=tmp_path / "final.mp4",
                duracion_por_escena=2.0,
            )

    async def test_falla_si_el_binario_de_ffmpeg_no_existe(
        self, clips_dir: Path, audio_4s: Path, tmp_path: Path
    ) -> None:
        assembler_roto = FFmpegAssembler(binary="ffmpeg-binario-que-no-existe-xyz")

        with pytest.raises(AssemblyError):
            await assembler_roto.assemble(
                [Scene(numero=1, narracion="uno")],
                clips_dir=clips_dir,
                audio_path=audio_4s,
                output_path=tmp_path / "final.mp4",
                duracion_por_escena=2.0,
            )
