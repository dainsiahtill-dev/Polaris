"""Integration tests for GeminiAPIProvider.

Covers:
- Happy path: invoke(), health(), list_models()
- Edge cases: empty response, missing API key, thinking extraction
- Exception paths: HTTP 4xx/5xx, network errors
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.infrastructure.llm.providers.gemini_api_provider import GeminiAPIProvider
from polaris.kernelone.llm.types import InvokeResult


class TestGeminiAPIProviderHappyPath:
    """Tests for the normal successful execution paths."""

    def test_get_provider_info(self) -> None:
        info = GeminiAPIProvider.get_provider_info()
        assert info.type == "gemini_api"
        assert "thinking_extraction" in info.supported_features

    def test_get_default_config(self) -> None:
        defaults = GeminiAPIProvider.get_default_config()
        assert defaults["base_url"] == "https://generativelanguage.googleapis.com"
        assert defaults["max_tokens"] == 8192

    def test_validate_config_valid(self, gemini_api_config: dict[str, Any]) -> None:
        result = GeminiAPIProvider.validate_config(gemini_api_config)
        assert result.valid is True
        assert not result.errors

    def test_invoke_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_api_config: dict[str, Any],
        sample_gemini_response: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_gemini_response
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "requests.post",
            lambda _url, **kwargs: mock_resp,
        )

        provider = GeminiAPIProvider()
        result = provider.invoke("Say hello", "gemini-1.5-pro", gemini_api_config)

        assert isinstance(result, InvokeResult)
        assert result.ok is True
        assert result.output == "Hello! How can I help you today?"
        assert result.error is None
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 8

    def test_health_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_api_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "requests.get",
            lambda _url, **kwargs: mock_resp,
        )

        provider = GeminiAPIProvider()
        result = provider.health(gemini_api_config)

        assert result.ok is True
        assert result.error is None
        assert result.latency_ms >= 0

    def test_list_models_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_api_config: dict[str, Any],
    ) -> None:
        payload = {
            "models": [
                {"name": "models/gemini-1.5-pro", "displayName": "Gemini 1.5 Pro"},
                {"name": "models/gemini-1.5-flash", "displayName": "Gemini 1.5 Flash"},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "requests.get",
            lambda _url, **kwargs: mock_resp,
        )

        provider = GeminiAPIProvider()
        result = provider.list_models(gemini_api_config)

        assert result.ok is True
        assert len(result.models) == 2
        assert result.models[0].id == "gemini-1.5-pro"


class TestGeminiAPIProviderEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_invoke_empty_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_api_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": ""}]}}],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 0},
        }
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "requests.post",
            lambda _url, **kwargs: mock_resp,
        )

        provider = GeminiAPIProvider()
        result = provider.invoke("Say nothing", "gemini-1.5-pro", gemini_api_config)

        assert result.ok is True
        assert result.output == ""

    def test_invoke_missing_api_key(self, gemini_api_config: dict[str, Any]) -> None:
        config = {k: v for k, v in gemini_api_config.items() if k != "api_key"}
        provider = GeminiAPIProvider()
        result = provider.invoke("Hello", "gemini-1.5-pro", config)

        assert result.ok is False
        assert result.error is not None
        assert "api key" in result.error.lower()

    def test_health_missing_api_key(self, gemini_api_config: dict[str, Any]) -> None:
        config = {k: v for k, v in gemini_api_config.items() if k != "api_key"}
        provider = GeminiAPIProvider()
        result = provider.health(config)

        assert result.ok is False
        assert result.error is not None
        assert "api key" in result.error.lower()

    def test_list_models_missing_api_key(self, gemini_api_config: dict[str, Any]) -> None:
        config = {k: v for k, v in gemini_api_config.items() if k != "api_key"}
        provider = GeminiAPIProvider()
        result = provider.list_models(config)

        assert result.ok is False
        assert result.error is not None
        assert "api key" in result.error.lower()

    def test_validate_config_invalid_temperature(self) -> None:
        config = {
            "base_url": "https://generativelanguage.googleapis.com",
            "api_key": "test",
            "temperature": 5.0,
        }
        result = GeminiAPIProvider.validate_config(config)
        assert result.valid is True
        assert any("temperature" in w.lower() for w in result.warnings)
        assert result.normalized_config is not None
        assert result.normalized_config["temperature"] == 0.7

    def test_list_models_fallback_when_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_api_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": []}
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "requests.get",
            lambda _url, **kwargs: mock_resp,
        )

        provider = GeminiAPIProvider()
        result = provider.list_models(gemini_api_config)

        assert result.ok is True
        assert len(result.models) >= 3  # fallback known models

    def test_thinking_extraction_detected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_api_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": (
                                    "<thinking>I need to consider the weather first.</thinking>\n\n"
                                    "The weather is sunny today."
                                )
                            }
                        ]
                    }
                }
            ],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 15},
        }
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "requests.post",
            lambda _url, **kwargs: mock_resp,
        )

        provider = GeminiAPIProvider()
        result = provider.invoke("What's the weather", "gemini-1.5-pro", gemini_api_config)

        assert result.ok is True
        assert "sunny" in result.output


class TestGeminiAPIProviderExceptions:
    """Tests for error and exception handling paths."""

    def test_invoke_http_401(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_api_config: dict[str, Any],
    ) -> None:
        # GeminiProvider.invoke catches RuntimeError/ValueError, not HTTPError.
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 401
        mock_resp.text = '{"error": "Unauthorized"}'
        mock_resp.raise_for_status.side_effect = RuntimeError("401 Client Error: Unauthorized")

        monkeypatch.setattr(
            "requests.post",
            lambda _url, **kwargs: mock_resp,
        )

        provider = GeminiAPIProvider()
        result = provider.invoke("Hello", "gemini-1.5-pro", gemini_api_config)

        assert result.ok is False
        assert result.error is not None
        assert "401" in result.error or "Unauthorized" in result.error

    def test_invoke_http_500_with_retry_exhausted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_api_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 500
        mock_resp.text = '{"error": "Internal Server Error"}'
        mock_resp.raise_for_status.side_effect = RuntimeError("500 Server Error")

        monkeypatch.setattr(
            "requests.post",
            lambda _url, **kwargs: mock_resp,
        )

        provider = GeminiAPIProvider()
        result = provider.invoke("Hello", "gemini-1.5-pro", gemini_api_config)

        assert result.ok is False
        assert result.error is not None
        assert "500" in result.error or "Server Error" in result.error

    def test_health_http_404(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_api_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"
        mock_resp.raise_for_status.side_effect = RuntimeError("404 Client Error: Not Found")

        monkeypatch.setattr(
            "requests.get",
            lambda _url, **kwargs: mock_resp,
        )

        provider = GeminiAPIProvider()
        result = provider.health(gemini_api_config)

        assert result.ok is False
        assert result.error is not None
        assert "404" in result.error or "Not Found" in result.error

    def test_list_models_http_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_api_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 503
        mock_resp.text = "Service Unavailable"
        mock_resp.raise_for_status.side_effect = RuntimeError("503 Server Error")

        monkeypatch.setattr(
            "requests.get",
            lambda _url, **kwargs: mock_resp,
        )

        provider = GeminiAPIProvider()
        result = provider.list_models(gemini_api_config)

        assert result.ok is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_invoke_stream_success_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_api_config: dict[str, Any],
        sample_gemini_response: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_gemini_response
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "requests.post",
            lambda _url, **kwargs: mock_resp,
        )

        provider = GeminiAPIProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "gemini-1.5-pro", gemini_api_config):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0] == "Hello! How can I help you today?"

    @pytest.mark.asyncio
    async def test_invoke_stream_error_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gemini_api_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 429
        mock_resp.text = "Rate limited"
        mock_resp.raise_for_status.side_effect = RuntimeError("429 Rate Limited")

        monkeypatch.setattr(
            "requests.post",
            lambda _url, **kwargs: mock_resp,
        )

        provider = GeminiAPIProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "gemini-1.5-pro", gemini_api_config):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].startswith("Error:")
