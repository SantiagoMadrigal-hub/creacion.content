"""Tests de :mod:`lavox.settings`: env vars anidadas, precedencia, tipos y secretos."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from lavox.domain.exceptions import ConfigError
from lavox.settings import Settings, get_settings


class TestVariablesDeEntorno:
    """Casos de uso: `Settings | validación env vars, precedencia, tipos`."""

    def test_lee_api_key_anidada_desde_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LAVOX_LLM__GROQ_API_KEY", "gsk_desde_env_var")
        settings = Settings()
        assert settings.llm.groq_api_key is not None
        assert settings.llm.groq_api_key.get_secret_value() == "gsk_desde_env_var"

    def test_lee_valor_anidado_no_secreto_desde_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LAVOX_PIPELINE__RELEVANCE_THRESHOLD", "85")
        settings = Settings()
        assert settings.pipeline.relevance_threshold == 85

    def test_valores_por_defecto_cuando_no_hay_env_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LAVOX_LOG_LEVEL", raising=False)
        settings = Settings()
        assert settings.log_level == "INFO"
        assert settings.pipeline.relevance_threshold == 70
        assert settings.llm.groq_model == "llama-3.1-8b-instant"

    def test_override_explicito_tiene_prioridad_sobre_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LAVOX_LOG_LEVEL", "INFO")
        settings = get_settings(log_level="DEBUG")
        assert settings.log_level == "DEBUG"

    def test_override_parcial_no_pisa_el_resto_de_campos_anidados(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un override anidado parcial se fusiona con el resto de valores del grupo."""
        monkeypatch.setenv("LAVOX_PIPELINE__MAX_CURATION_ATTEMPTS", "5")
        settings = get_settings(pipeline={"relevance_threshold": 90})
        assert settings.pipeline.relevance_threshold == 90
        assert settings.pipeline.max_curation_attempts == 5

    def test_tipos_invalidos_se_envuelven_en_config_error(self) -> None:
        with pytest.raises(ConfigError):
            get_settings(pipeline={"relevance_threshold": "no-es-un-numero"})

    def test_umbral_de_relevancia_fuera_de_rango_es_invalido(self) -> None:
        with pytest.raises(ConfigError):
            get_settings(pipeline={"relevance_threshold": 150})


class TestSecretosNoExpuestos:
    """Caso de uso: `Settings | secrets no loggeados`."""

    def test_repr_de_settings_no_expone_la_api_key(self) -> None:
        settings = Settings(llm={"groq_api_key": "gsk_super_secreta_no_debe_salir"})
        assert "gsk_super_secreta_no_debe_salir" not in repr(settings)
        assert "gsk_super_secreta_no_debe_salir" not in str(settings)

    def test_secret_str_requiere_get_secret_value_explicito(self) -> None:
        settings = Settings(pexels={"api_key": "clave_pexels_secreta"})
        assert isinstance(settings.pexels.api_key, SecretStr)
        assert str(settings.pexels.api_key) == "**********"
        assert settings.pexels.api_key.get_secret_value() == "clave_pexels_secreta"


class TestValidacionDeClavesRequeridas:
    def test_require_llm_keys_falla_sin_ninguna_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LAVOX_LLM__GROQ_API_KEY", raising=False)
        monkeypatch.delenv("LAVOX_LLM__OPENAI_API_KEY", raising=False)
        settings = Settings(llm={"groq_api_key": None, "openai_api_key": None})
        with pytest.raises(ConfigError):
            settings.require_llm_keys()

    def test_require_llm_keys_pasa_con_una_sola_key(self) -> None:
        settings = Settings(llm={"groq_api_key": "gsk_algo"})
        settings.require_llm_keys()  # no debe lanzar

    def test_require_pexels_key_falla_sin_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LAVOX_PEXELS__API_KEY", raising=False)
        settings = Settings(pexels={"api_key": None})
        with pytest.raises(ConfigError):
            settings.require_pexels_key()
