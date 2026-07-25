"""Tests de la CLI (`lavox.cli.main`): dry-run, códigos de salida y errores."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import lavox.cli.main as cli_module
from lavox.application.pipeline.orchestrator import PipelineResult
from lavox.application.use_cases.download_clips import DownloadSummary
from lavox.cli.main import ExitCode, app
from lavox.domain.entities.clip import Clip
from lavox.domain.entities.scene import Scene
from lavox.domain.exceptions import AssemblyError, ConfigError, DownloadError, LLMError

runner = CliRunner()


@pytest.fixture(autouse=True)
def _sin_env_vars_de_lavox(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asegura que ninguna variable LAVOX_* ambiental contamine estos tests."""
    for var in list(__import__("os").environ):
        if var.startswith("LAVOX_"):
            monkeypatch.delenv(var, raising=False)


class TestDryRun:
    """El --dry-run no debe requerir ninguna API key ni hacer llamadas externas."""

    def test_analyze_dry_run_con_guion_valido(self, guion_sample_path: Path) -> None:
        resultado = runner.invoke(app, ["analyze", "--guion", str(guion_sample_path), "--dry-run"])

        assert resultado.exit_code == int(ExitCode.OK)
        assert "Plan (dry-run)" in resultado.stdout

    def test_analyze_dry_run_sin_guion_falla_con_config_error(self, tmp_path: Path) -> None:
        resultado = runner.invoke(
            app, ["analyze", "--guion", str(tmp_path / "no_existe.txt"), "--dry-run"]
        )

        assert resultado.exit_code == int(ExitCode.CONFIG_ERROR)

    def test_run_dry_run_smoke_test_del_spec(self, guion_sample_path: Path) -> None:
        """Reproduce literalmente: `lavox-pipeline run --guion ... --dry-run`."""
        resultado = runner.invoke(app, ["run", "--guion", str(guion_sample_path), "--dry-run"])

        assert resultado.exit_code == int(ExitCode.OK)

    def test_download_dry_run_sin_checkpoint_falla(self, tmp_path: Path) -> None:
        resultado = runner.invoke(
            app, ["download", "--scenes", str(tmp_path / "no_existe.json"), "--dry-run"]
        )

        assert resultado.exit_code == int(ExitCode.CONFIG_ERROR)

    def test_assemble_dry_run_sin_audio_falla(self, tmp_path: Path) -> None:
        checkpoint = tmp_path / "escenas.json"
        checkpoint.write_text("[]", encoding="utf-8")

        resultado = runner.invoke(
            app,
            [
                "assemble",
                "--scenes",
                str(checkpoint),
                "--audio",
                str(tmp_path / "no_existe.mp3"),
                "--dry-run",
            ],
        )

        assert resultado.exit_code == int(ExitCode.CONFIG_ERROR)


class TestComandoAnalyze:
    async def _fake_ok(self, *args: Any, **kwargs: Any) -> list[Scene]:
        return [Scene(numero=1, narracion="x", clip_seleccionado=Clip(id=1, url_descarga="u"))]

    async def _fake_parcial(self, *args: Any, **kwargs: Any) -> list[Scene]:
        return [Scene(numero=1, narracion="x", clip_seleccionado=None)]

    async def _fake_config_error(self, *args: Any, **kwargs: Any) -> list[Scene]:
        raise ConfigError("falta configuración")

    async def _fake_llm_error(self, *args: Any, **kwargs: Any) -> list[Scene]:
        raise LLMError("el proveedor LLM falló")

    def test_exit_ok_si_todas_las_escenas_tienen_clip(
        self, monkeypatch: pytest.MonkeyPatch, guion_sample_path: Path
    ) -> None:
        monkeypatch.setattr(cli_module, "_run_analyze", self._fake_ok)

        resultado = runner.invoke(app, ["analyze", "--guion", str(guion_sample_path)])

        assert resultado.exit_code == int(ExitCode.OK)

    def test_exit_partial_si_alguna_escena_sin_clip(
        self, monkeypatch: pytest.MonkeyPatch, guion_sample_path: Path
    ) -> None:
        monkeypatch.setattr(cli_module, "_run_analyze", self._fake_parcial)

        resultado = runner.invoke(app, ["analyze", "--guion", str(guion_sample_path)])

        assert resultado.exit_code == int(ExitCode.PARTIAL)

    def test_exit_config_error(
        self, monkeypatch: pytest.MonkeyPatch, guion_sample_path: Path
    ) -> None:
        monkeypatch.setattr(cli_module, "_run_analyze", self._fake_config_error)

        resultado = runner.invoke(app, ["analyze", "--guion", str(guion_sample_path)])

        assert resultado.exit_code == int(ExitCode.CONFIG_ERROR)

    def test_exit_llm_error(self, monkeypatch: pytest.MonkeyPatch, guion_sample_path: Path) -> None:
        monkeypatch.setattr(cli_module, "_run_analyze", self._fake_llm_error)

        resultado = runner.invoke(app, ["analyze", "--guion", str(guion_sample_path)])

        assert resultado.exit_code == int(ExitCode.LLM_ERROR)


class TestComandoDownload:
    async def _fake_ok(self, *args: Any, **kwargs: Any) -> DownloadSummary:
        return DownloadSummary(total_escenas=2, descargados=2, omitidos=0, fallidos=0, sin_clip=0)

    async def _fake_error(self, *args: Any, **kwargs: Any) -> DownloadSummary:
        raise DownloadError("todas las descargas fallaron")

    def test_exit_ok(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        checkpoint = tmp_path / "escenas.json"
        checkpoint.write_text("[]", encoding="utf-8")
        monkeypatch.setattr(cli_module, "_run_download", self._fake_ok)

        resultado = runner.invoke(app, ["download", "--scenes", str(checkpoint)])

        assert resultado.exit_code == int(ExitCode.OK)

    def test_exit_download_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        checkpoint = tmp_path / "escenas.json"
        checkpoint.write_text("[]", encoding="utf-8")
        monkeypatch.setattr(cli_module, "_run_download", self._fake_error)

        resultado = runner.invoke(app, ["download", "--scenes", str(checkpoint)])

        assert resultado.exit_code == int(ExitCode.DOWNLOAD_ERROR)


class TestComandoAssemble:
    async def _fake_ok(self, *args: Any, **kwargs: Any) -> Path:
        return Path("/tmp/video_final.mp4")

    async def _fake_error(self, *args: Any, **kwargs: Any) -> Path:
        raise AssemblyError("ffmpeg falló")

    def test_exit_ok(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        checkpoint = tmp_path / "escenas.json"
        checkpoint.write_text("[]", encoding="utf-8")
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"x")
        monkeypatch.setattr(cli_module, "_run_assemble", self._fake_ok)

        resultado = runner.invoke(
            app, ["assemble", "--scenes", str(checkpoint), "--audio", str(audio)]
        )

        assert resultado.exit_code == int(ExitCode.OK)

    def test_exit_assembly_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        checkpoint = tmp_path / "escenas.json"
        checkpoint.write_text("[]", encoding="utf-8")
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"x")
        monkeypatch.setattr(cli_module, "_run_assemble", self._fake_error)

        resultado = runner.invoke(
            app, ["assemble", "--scenes", str(checkpoint), "--audio", str(audio)]
        )

        assert resultado.exit_code == int(ExitCode.ASSEMBLY_ERROR)


class TestComandoRun:
    async def _fake_ok(self, *args: Any, **kwargs: Any) -> PipelineResult:
        return PipelineResult(
            escenas=[
                Scene(numero=1, narracion="x", clip_seleccionado=Clip(id=1, url_descarga="u"))
            ],
            download_summary=DownloadSummary(
                total_escenas=1, descargados=1, omitidos=0, fallidos=0, sin_clip=0
            ),
            output_video_path=Path("/tmp/final.mp4"),
        )

    async def _fake_parcial(self, *args: Any, **kwargs: Any) -> PipelineResult:
        return PipelineResult(
            escenas=[Scene(numero=1, narracion="x", clip_seleccionado=None)],
            download_summary=DownloadSummary(
                total_escenas=1, descargados=0, omitidos=0, fallidos=0, sin_clip=1
            ),
            output_video_path=Path("/tmp/final.mp4"),
        )

    def test_exit_ok(self, monkeypatch: pytest.MonkeyPatch, guion_sample_path: Path) -> None:
        monkeypatch.setattr(cli_module, "_run_pipeline", self._fake_ok)

        resultado = runner.invoke(app, ["run", "--guion", str(guion_sample_path)])

        assert resultado.exit_code == int(ExitCode.OK)
        assert "Video final" in resultado.stdout

    def test_exit_partial(self, monkeypatch: pytest.MonkeyPatch, guion_sample_path: Path) -> None:
        monkeypatch.setattr(cli_module, "_run_pipeline", self._fake_parcial)

        resultado = runner.invoke(app, ["run", "--guion", str(guion_sample_path)])

        assert resultado.exit_code == int(ExitCode.PARTIAL)
