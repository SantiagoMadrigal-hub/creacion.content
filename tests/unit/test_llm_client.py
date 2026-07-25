"""Tests de los clientes LLM: retry, fallback Groq->OpenAI y parsing JSON tolerante."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import groq
import httpx
import openai
import pytest

from lavox.domain.exceptions import LLMError
from lavox.infrastructure.llm.fallback_client import FallbackClient
from lavox.infrastructure.llm.groq_client import GroqClient
from lavox.infrastructure.llm.openai_client import OpenAIClient

_FAKE_URL = "https://api.groq.com/openai/v1/chat/completions"


def _fake_chat_response(contenido: str) -> SimpleNamespace:
    """Doble mínimo de un `ChatCompletion`: solo lo que el código realmente usa."""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=contenido))])


def _rate_limit_error() -> groq.RateLimitError:
    request = httpx.Request("POST", _FAKE_URL)
    return groq.RateLimitError(
        "rate limited", response=httpx.Response(429, request=request), body=None
    )


def _internal_server_error() -> groq.InternalServerError:
    request = httpx.Request("POST", _FAKE_URL)
    return groq.InternalServerError(
        "boom", response=httpx.Response(500, request=request), body=None
    )


def _connection_error() -> groq.APIConnectionError:
    return groq.APIConnectionError(request=httpx.Request("POST", _FAKE_URL))


def _timeout_error() -> groq.APITimeoutError:
    return groq.APITimeoutError(request=httpx.Request("POST", _FAKE_URL))


def _auth_error() -> groq.AuthenticationError:
    request = httpx.Request("POST", _FAKE_URL)
    return groq.AuthenticationError(
        "invalid api key", response=httpx.Response(401, request=request), body=None
    )


def _openai_rate_limit_error() -> openai.RateLimitError:
    request = httpx.Request("POST", _FAKE_URL)
    return openai.RateLimitError(
        "rate limited", response=httpx.Response(429, request=request), body=None
    )


@pytest.fixture
def mock_groq_sdk() -> MagicMock:
    return MagicMock(spec=groq.Groq)


@pytest.fixture
def mock_openai_sdk() -> MagicMock:
    return MagicMock(spec=openai.OpenAI)


class TestGroqClient:
    """Casos de uso: `LLMClient | retry en 429/5xx, JSON parsing tolerante, timeout`."""

    async def test_complete_devuelve_texto_en_camino_feliz(self, mock_groq_sdk: MagicMock) -> None:
        mock_groq_sdk.chat.completions.create.return_value = _fake_chat_response("  hola mundo  ")
        cliente = GroqClient("fake-key", client=mock_groq_sdk, wait_initial=0.001, wait_max=0.01)

        resultado = await cliente.complete("un prompt")

        assert resultado == "hola mundo"
        mock_groq_sdk.chat.completions.create.assert_called_once()

    async def test_reintenta_en_rate_limit_y_luego_tiene_exito(
        self, mock_groq_sdk: MagicMock
    ) -> None:
        mock_groq_sdk.chat.completions.create.side_effect = [
            _rate_limit_error(),
            _rate_limit_error(),
            _fake_chat_response("recuperado tras reintentos"),
        ]
        cliente = GroqClient(
            "fake-key", client=mock_groq_sdk, max_attempts=5, wait_initial=0.001, wait_max=0.01
        )

        resultado = await cliente.complete("un prompt")

        assert resultado == "recuperado tras reintentos"
        assert mock_groq_sdk.chat.completions.create.call_count == 3

    async def test_reintenta_en_error_5xx(self, mock_groq_sdk: MagicMock) -> None:
        mock_groq_sdk.chat.completions.create.side_effect = [
            _internal_server_error(),
            _fake_chat_response("ok tras 500"),
        ]
        cliente = GroqClient(
            "fake-key", client=mock_groq_sdk, max_attempts=3, wait_initial=0.001, wait_max=0.01
        )

        resultado = await cliente.complete("un prompt")

        assert resultado == "ok tras 500"

    async def test_reintenta_en_timeout(self, mock_groq_sdk: MagicMock) -> None:
        mock_groq_sdk.chat.completions.create.side_effect = [
            _timeout_error(),
            _fake_chat_response("ok tras timeout"),
        ]
        cliente = GroqClient(
            "fake-key", client=mock_groq_sdk, max_attempts=3, wait_initial=0.001, wait_max=0.01
        )

        resultado = await cliente.complete("un prompt")

        assert resultado == "ok tras timeout"

    async def test_reintenta_en_error_de_conexion(self, mock_groq_sdk: MagicMock) -> None:
        mock_groq_sdk.chat.completions.create.side_effect = [
            _connection_error(),
            _fake_chat_response("ok tras conexion"),
        ]
        cliente = GroqClient(
            "fake-key", client=mock_groq_sdk, max_attempts=3, wait_initial=0.001, wait_max=0.01
        )

        resultado = await cliente.complete("un prompt")

        assert resultado == "ok tras conexion"

    async def test_agota_reintentos_y_lanza_llm_error(self, mock_groq_sdk: MagicMock) -> None:
        mock_groq_sdk.chat.completions.create.side_effect = _rate_limit_error()
        cliente = GroqClient(
            "fake-key", client=mock_groq_sdk, max_attempts=3, wait_initial=0.001, wait_max=0.01
        )

        with pytest.raises(LLMError):
            await cliente.complete("un prompt")

        assert mock_groq_sdk.chat.completions.create.call_count == 3

    async def test_error_no_recuperable_no_se_reintenta(self, mock_groq_sdk: MagicMock) -> None:
        mock_groq_sdk.chat.completions.create.side_effect = _auth_error()
        cliente = GroqClient(
            "fake-key", client=mock_groq_sdk, max_attempts=5, wait_initial=0.001, wait_max=0.01
        )

        with pytest.raises(LLMError):
            await cliente.complete("un prompt")

        mock_groq_sdk.chat.completions.create.assert_called_once()

    async def test_complete_json_parsea_json_limpio(
        self, mock_groq_sdk: MagicMock, llm_responses: dict[str, str]
    ) -> None:
        mock_groq_sdk.chat.completions.create.return_value = _fake_chat_response(
            llm_responses["queries_limpio"]
        )
        cliente = GroqClient("fake-key", client=mock_groq_sdk)

        resultado = await cliente.complete_json("un prompt")

        assert resultado == [
            "old book mysterious letters",
            "surprised person thinking",
            "library ancient knowledge",
        ]

    async def test_complete_json_tolera_bloques_markdown(
        self, mock_groq_sdk: MagicMock, llm_responses: dict[str, str]
    ) -> None:
        mock_groq_sdk.chat.completions.create.return_value = _fake_chat_response(
            llm_responses["queries_con_markdown"]
        )
        cliente = GroqClient("fake-key", client=mock_groq_sdk)

        resultado = await cliente.complete_json("un prompt")

        assert resultado == [
            "hamburger german immigrants",
            "old kitchen cooking",
            "Hamburg city map",
        ]

    async def test_complete_json_devuelve_none_si_no_hay_json(
        self, mock_groq_sdk: MagicMock, llm_responses: dict[str, str]
    ) -> None:
        mock_groq_sdk.chat.completions.create.return_value = _fake_chat_response(
            llm_responses["respuesta_no_json"]
        )
        cliente = GroqClient("fake-key", client=mock_groq_sdk)

        resultado = await cliente.complete_json("un prompt")

        assert resultado is None


class TestOpenAIClient:
    async def test_complete_devuelve_texto_en_camino_feliz(
        self, mock_openai_sdk: MagicMock
    ) -> None:
        mock_openai_sdk.chat.completions.create.return_value = _fake_chat_response("respuesta")
        cliente = OpenAIClient("fake-key", client=mock_openai_sdk)

        resultado = await cliente.complete("un prompt")

        assert resultado == "respuesta"

    async def test_reintenta_en_rate_limit(self, mock_openai_sdk: MagicMock) -> None:
        mock_openai_sdk.chat.completions.create.side_effect = [
            _openai_rate_limit_error(),
            _fake_chat_response("ok"),
        ]
        cliente = OpenAIClient(
            "fake-key", client=mock_openai_sdk, max_attempts=3, wait_initial=0.001, wait_max=0.01
        )

        resultado = await cliente.complete("un prompt")

        assert resultado == "ok"


class TestFallbackClient:
    """Caso de uso: `LLMClient | fallback Groq->OpenAI`."""

    async def test_usa_el_primario_si_funciona_y_no_llama_al_secundario(
        self, mock_groq_sdk: MagicMock, mock_openai_sdk: MagicMock
    ) -> None:
        mock_groq_sdk.chat.completions.create.return_value = _fake_chat_response("desde groq")
        primario = GroqClient("fake-key", client=mock_groq_sdk)
        secundario = OpenAIClient("fake-key", client=mock_openai_sdk)
        cliente = FallbackClient(primario, secundario)

        resultado = await cliente.complete("un prompt")

        assert resultado == "desde groq"
        mock_openai_sdk.chat.completions.create.assert_not_called()

    async def test_cae_a_openai_si_groq_agota_reintentos(
        self, mock_groq_sdk: MagicMock, mock_openai_sdk: MagicMock
    ) -> None:
        mock_groq_sdk.chat.completions.create.side_effect = _rate_limit_error()
        mock_openai_sdk.chat.completions.create.return_value = _fake_chat_response("desde openai")
        primario = GroqClient(
            "fake-key", client=mock_groq_sdk, max_attempts=2, wait_initial=0.001, wait_max=0.01
        )
        secundario = OpenAIClient("fake-key", client=mock_openai_sdk)
        cliente = FallbackClient(primario, secundario)

        resultado = await cliente.complete("un prompt")

        assert resultado == "desde openai"

    async def test_sin_secundario_propaga_el_error_del_primario(
        self, mock_groq_sdk: MagicMock
    ) -> None:
        mock_groq_sdk.chat.completions.create.side_effect = _auth_error()
        primario = GroqClient("fake-key", client=mock_groq_sdk)
        cliente = FallbackClient(primario, None)

        with pytest.raises(LLMError):
            await cliente.complete("un prompt")

    async def test_complete_json_tambien_hace_fallback(
        self, mock_groq_sdk: MagicMock, mock_openai_sdk: MagicMock, llm_responses: dict[str, str]
    ) -> None:
        mock_groq_sdk.chat.completions.create.side_effect = _auth_error()
        mock_openai_sdk.chat.completions.create.return_value = _fake_chat_response(
            llm_responses["elementos_concretos"]
        )
        primario = GroqClient("fake-key", client=mock_groq_sdk)
        secundario = OpenAIClient("fake-key", client=mock_openai_sdk)
        cliente = FallbackClient(primario, secundario)

        resultado = await cliente.complete_json("un prompt")

        assert resultado == ["old manuscript", "monk writing", "ancient monastery", "glass bottle"]
