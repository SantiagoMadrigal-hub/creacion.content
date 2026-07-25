"""Tests de :class:`LocalAudioProvider`, usando audio sintético real."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lavox.domain.exceptions import AssemblyError
from lavox.infrastructure.audio.local_audio_provider import LocalAudioProvider

pytestmark = pytest.mark.slow


@pytest.fixture
async def audio_2s(tmp_path: Path) -> Path:
    destino = tmp_path / "audio.mp3"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=2",
        "-y",
        str(destino),
    )
    await proc.wait()
    return destino


class TestLocalAudioProvider:
    async def test_lee_la_duracion_de_un_archivo_valido(self, audio_2s: Path) -> None:
        provider = LocalAudioProvider()

        duracion = await provider.get_duration(audio_2s)

        assert duracion == pytest.approx(2.0, abs=0.2)

    async def test_falla_si_el_archivo_no_existe(self, tmp_path: Path) -> None:
        provider = LocalAudioProvider()

        with pytest.raises(AssemblyError):
            await provider.get_duration(tmp_path / "no_existe.mp3")

    async def test_falla_si_el_binario_ffprobe_no_existe(self, audio_2s: Path) -> None:
        provider = LocalAudioProvider(probe_binary="ffprobe-binario-que-no-existe-xyz")

        with pytest.raises(AssemblyError):
            await provider.get_duration(audio_2s)

    async def test_falla_si_el_archivo_no_es_audio_valido(self, tmp_path: Path) -> None:
        archivo_basura = tmp_path / "basura.mp3"
        archivo_basura.write_bytes(b"esto no es un archivo de audio real")
        provider = LocalAudioProvider()

        with pytest.raises(AssemblyError):
            await provider.get_duration(archivo_basura)

    async def test_respeta_el_timeout(self, audio_2s: Path) -> None:
        provider = LocalAudioProvider(timeout=0.0001)

        with pytest.raises(AssemblyError):
            await provider.get_duration(audio_2s)
