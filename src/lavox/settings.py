"""Configuración tipada del proyecto LAVOX.

Usa ``pydantic-settings`` para cargar configuración por capas, con la
siguiente precedencia (de menor a mayor prioridad):

    valores por defecto -> archivo ``.env`` -> variables de entorno reales
    -> overrides explícitos pasados al construir ``Settings`` (p. ej. desde
    la CLI).

Ninguna API key vive en el código fuente: todas se cargan desde el entorno
y se exponen como :class:`pydantic.SecretStr` para evitar que aparezcan por
accidente en logs, reprs o tracebacks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from lavox.domain.exceptions import ConfigError

__all__ = [
    "FFmpegConfig",
    "LLMConfig",
    "PexelsConfig",
    "PipelineConfig",
    "Settings",
    "get_settings",
]


class LLMConfig(BaseModel):
    """Configuración de los proveedores LLM (Groq principal, OpenAI fallback)."""

    groq_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    groq_model: str = "llama-3.1-8b-instant"
    openai_model: str = "gpt-4o-mini"
    max_retries: int = Field(default=3, ge=1, le=10)
    request_timeout: float = Field(default=30.0, gt=0)


class PexelsConfig(BaseModel):
    """Configuración del proveedor de búsqueda de video Pexels."""

    api_key: SecretStr | None = None
    base_url: str = "https://api.pexels.com/videos/search"
    per_page: int = Field(default=10, ge=1, le=80)
    min_width: int = Field(default=1280, ge=1)
    max_retries: int = Field(default=3, ge=1, le=10)
    request_timeout: float = Field(default=20.0, gt=0)


class FFmpegConfig(BaseModel):
    """Configuración del ensamblador de video basado en FFmpeg/ffprobe."""

    binary: str = "ffmpeg"
    probe_binary: str = "ffprobe"
    resolution_width: int = Field(default=1920, gt=0)
    resolution_height: int = Field(default=1080, gt=0)
    preset: str = "fast"
    crf: int = Field(default=23, ge=0, le=51)
    process_timeout: float = Field(default=120.0, gt=0)


class PipelineConfig(BaseModel):
    """Parámetros de negocio del pipeline (umbrales, concurrencia, agrupación)."""

    relevance_threshold: int = Field(default=70, ge=0, le=100)
    max_curation_attempts: int = Field(default=2, ge=1, le=10)
    max_download_concurrency: int = Field(default=5, ge=1, le=32)
    download_max_retries: int = Field(default=3, ge=1, le=10)
    download_timeout: float = Field(default=60.0, gt=0)
    min_line_length_for_grouping: int = Field(default=60, ge=1)
    max_scenes_before_grouping: int = Field(default=45, ge=1)
    contexto_narrativo: str = "Documental sobre palabras en inglés con orígenes engañosos."


class Settings(BaseSettings):
    """Configuración raíz de LAVOX, cargada desde ``.env`` y variables de entorno.

    Ejemplo de variables anidadas (separador ``__``)::

        LAVOX_LLM__GROQ_API_KEY=gsk_...
        LAVOX_PEXELS__API_KEY=...
        LAVOX_PIPELINE__RELEVANCE_THRESHOLD=75
    """

    model_config = SettingsConfigDict(
        env_prefix="LAVOX_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    environment: Literal["development", "production", "test"] = "development"
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    script_path: Path = Path("guion.txt")
    scenes_output_path: Path = Path("escenas_con_videos.json")
    clips_dir: Path = Path("videos")
    audio_path: Path = Path("audios/vozenoff_completa.mp3")
    final_output_path: Path = Path("video_final_definitivo.mp4")

    llm: LLMConfig = Field(default_factory=LLMConfig)
    pexels: PexelsConfig = Field(default_factory=PexelsConfig)
    ffmpeg: FFmpegConfig = Field(default_factory=FFmpegConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)

    def require_llm_keys(self) -> None:
        """Valida que exista al menos una API key de LLM configurada.

        Raises:
            ConfigError: si no hay ninguna key de Groq ni de OpenAI.
        """
        if self.llm.groq_api_key is None and self.llm.openai_api_key is None:
            raise ConfigError(
                "No hay ninguna API key de LLM configurada. Define "
                "LAVOX_LLM__GROQ_API_KEY y/o LAVOX_LLM__OPENAI_API_KEY en tu .env."
            )

    def require_pexels_key(self) -> None:
        """Valida que exista una API key de Pexels configurada.

        Raises:
            ConfigError: si ``pexels.api_key`` no está configurada.
        """
        if self.pexels.api_key is None:
            raise ConfigError(
                "No hay API key de Pexels configurada. Define LAVOX_PEXELS__API_KEY en tu .env."
            )


def get_settings(**overrides: object) -> Settings:
    """Construye ``Settings`` envolviendo errores de validación en ``ConfigError``.

    Args:
        **overrides: valores explícitos (p. ej. desde la CLI) que tienen la
            máxima precedencia sobre ``.env`` y variables de entorno.

    Returns:
        Instancia de :class:`Settings` validada.

    Raises:
        ConfigError: si la configuración no pasa la validación de pydantic.
    """
    try:
        return Settings.model_validate(overrides)
    except Exception as exc:  # se re-envuelve deliberadamente como ConfigError
        raise ConfigError(f"Configuración inválida: {exc}") from exc
