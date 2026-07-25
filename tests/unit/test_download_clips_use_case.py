"""Tests de :class:`DownloadClipsUseCase` (validaciones y fallo catastrófico)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lavox.application.use_cases.download_clips import DownloadClipsUseCase
from lavox.domain.entities.clip import Clip
from lavox.domain.entities.scene import Scene
from lavox.domain.exceptions import ConfigError, DownloadError
from lavox.domain.ports.video_downloader_port import DownloadResult


class AlwaysFailsDownloader:
    async def download(self, url: str, destino: Path) -> DownloadResult:
        raise DownloadError("siempre falla")

    async def download_many(
        self, items: Any, *, max_concurrency: int = 5, on_complete: Any = None
    ) -> list[Any]:
        return [DownloadError("siempre falla") for _ in items]


class TestValidaciones:
    async def test_falla_si_no_existe_el_checkpoint_de_escenas(self, tmp_path: Path) -> None:
        caso_de_uso = DownloadClipsUseCase(AlwaysFailsDownloader())

        with pytest.raises(ConfigError):
            await caso_de_uso.execute(tmp_path / "no_existe.json", tmp_path / "videos")

    async def test_falla_catastroficamente_si_todas_las_descargas_fallan(
        self, tmp_path: Path
    ) -> None:
        checkpoint = tmp_path / "escenas.json"
        escena = Scene(
            numero=1, narracion="x", clip_seleccionado=Clip(id=1, url_descarga="https://x/1.mp4")
        )
        checkpoint.write_text(json.dumps([escena.to_dict()]), encoding="utf-8")
        caso_de_uso = DownloadClipsUseCase(AlwaysFailsDownloader())

        with pytest.raises(DownloadError):
            await caso_de_uso.execute(checkpoint, tmp_path / "videos")

    async def test_no_falla_si_no_hay_ningun_clip_para_descargar(self, tmp_path: Path) -> None:
        checkpoint = tmp_path / "escenas.json"
        escena = Scene(numero=1, narracion="x", clip_seleccionado=None)
        checkpoint.write_text(json.dumps([escena.to_dict()]), encoding="utf-8")
        caso_de_uso = DownloadClipsUseCase(AlwaysFailsDownloader())

        resumen = await caso_de_uso.execute(checkpoint, tmp_path / "videos")

        assert resumen.sin_clip == 1
        assert resumen.descargados == 0
        assert resumen.fallidos == 0
