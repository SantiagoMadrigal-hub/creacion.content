"""Tests de :class:`AssembleVideoUseCase` (validaciones propias del caso de uso)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lavox.application.use_cases.assemble_video import AssembleVideoUseCase
from lavox.domain.exceptions import AssemblyError, ConfigError


class FakeAssembler:
    async def assemble(self, scenes: Any, **kwargs: Any) -> Path:
        return kwargs["output_path"]


class FakeAudioProvider:
    async def get_duration(self, audio_path: Path) -> float:
        return 10.0


class TestValidaciones:
    async def test_falla_si_no_existe_el_checkpoint_de_escenas(self, tmp_path: Path) -> None:
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"x")
        caso_de_uso = AssembleVideoUseCase(FakeAssembler(), FakeAudioProvider())

        with pytest.raises(ConfigError):
            await caso_de_uso.execute(
                tmp_path / "no_existe.json", tmp_path / "videos", audio, tmp_path / "final.mp4"
            )

    async def test_falla_si_no_existe_el_audio(self, tmp_path: Path) -> None:
        checkpoint = tmp_path / "escenas.json"
        checkpoint.write_text("[]", encoding="utf-8")
        caso_de_uso = AssembleVideoUseCase(FakeAssembler(), FakeAudioProvider())

        with pytest.raises(ConfigError):
            await caso_de_uso.execute(
                checkpoint, tmp_path / "videos", tmp_path / "no_existe.mp3", tmp_path / "final.mp4"
            )

    async def test_falla_si_el_checkpoint_esta_vacio(self, tmp_path: Path) -> None:
        checkpoint = tmp_path / "escenas.json"
        checkpoint.write_text("[]", encoding="utf-8")
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"x")
        caso_de_uso = AssembleVideoUseCase(FakeAssembler(), FakeAudioProvider())

        with pytest.raises(AssemblyError):
            await caso_de_uso.execute(
                checkpoint, tmp_path / "videos", audio, tmp_path / "final.mp4"
            )
