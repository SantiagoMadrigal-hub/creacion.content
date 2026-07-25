"""Fixtures compartidos por toda la suite de tests de LAVOX."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from lavox.domain.entities.clip import Clip
from lavox.domain.entities.scene import Scene
from lavox.settings import Settings

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Ruta a la carpeta ``tests/fixtures``."""
    return FIXTURES_DIR


def load_fixture_json(nombre: str) -> Any:
    """Carga y parsea un archivo JSON de ``tests/fixtures``."""
    return json.loads((FIXTURES_DIR / nombre).read_text(encoding="utf-8"))


@pytest.fixture
def pexels_response_json() -> dict[str, Any]:
    """Respuesta cruda de ejemplo del endpoint de búsqueda de video de Pexels."""
    return load_fixture_json("pexels_response.json")  # type: ignore[no-any-return]


@pytest.fixture
def llm_responses() -> dict[str, str]:
    """Respuestas de texto crudas de ejemplo, como las devolvería un LLM."""
    return load_fixture_json("llm_responses.json")  # type: ignore[no-any-return]


@pytest.fixture
def guion_sample_path() -> Path:
    """Ruta del guion de ejemplo usado en los tests de análisis/integración."""
    return FIXTURES_DIR / "guion_sample.txt"


@pytest.fixture
def make_clip() -> Callable[..., Clip]:
    """Factory para crear :class:`Clip` de prueba con valores por defecto sensatos."""

    def _make(**overrides: Any) -> Clip:
        base: dict[str, Any] = {
            "id": 1234567,
            "url_descarga": "https://videos.pexels.com/video-files/1234567/sample.mp4",
            "tags": ("old book", "vintage", "library"),
            "duracion": 12.0,
            "ancho": 1920,
            "alto": 1080,
            "url_pexels": "https://www.pexels.com/video/an-old-book-1234567/",
            "query_origen": "old book mysterious letters",
        }
        base.update(overrides)
        return Clip(**base)

    return _make


@pytest.fixture
def make_scene(make_clip: Callable[..., Clip]) -> Callable[..., Scene]:
    """Factory para crear :class:`Scene` de prueba con valores por defecto sensatos."""

    def _make(**overrides: Any) -> Scene:
        base: dict[str, Any] = {
            "numero": 1,
            "narracion": 'Millones de personas dicen "Whisky".',
            "tema_principal": "origen de la palabra whisky",
            "emocion_tono": "intriga",
            "tipo_escena": "hook_inicio",
            "elementos_clave": ["old bottle", "ancient manuscript", "monastery"],
        }
        base.update(overrides)
        return Scene(**base)

    return _make


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    """``Settings`` con valores de prueba (rutas en ``tmp_path``, keys falsas)."""
    return Settings.model_validate(
        {
            "environment": "test",
            "log_level": "WARNING",
            "script_path": tmp_path / "guion.txt",
            "scenes_output_path": tmp_path / "escenas_con_videos.json",
            "clips_dir": tmp_path / "videos",
            "audio_path": tmp_path / "audio.mp3",
            "final_output_path": tmp_path / "video_final.mp4",
            "llm": {"groq_api_key": "gsk_test_fake_key_not_real", "max_retries": 2},
            "pexels": {"api_key": "test_fake_pexels_key_not_real", "max_retries": 2},
        }
    )
