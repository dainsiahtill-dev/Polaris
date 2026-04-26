"""Integration tests for KimiProvider.

Covers:
- Happy path: invoke(), health(), list_models()
- Edge cases: empty response, missing API key, invalid top_p
- Exception paths: HTTP 4xx/5xx, network errors
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.infrastructure.llm.providers.kimi_provider import KimiProvider
from polaris.kernelone.llm.types import InvokeResult


class TestKimiProviderHappyPath:
    """Tests for the normal successful execution paths."""

    def test_get_provider_info(self) -> None:
        info = KimiProvider.get_provider_info()
        assert info.type == "kimi"
        assert "chat_completions" in info.supported_features

    def test_get_default_config(self) -> None:
        defaults = KimiProvider.get_default_config()
        assert defaults["base_url"] == "https://api.moonshot.cn/v1"
        assert defaults["model"] == "kimi-k2-thinking-turbo"

    def test_validate_config_valid(self, kimi_config: dict[str, Any]) -> None:
        result = KimiProvider.validate_config(kimi_config)
        assert result.valid is True
        assert not result.errors

    def test_invoke_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        kimi_config: dict[str, Any],
        sample_kimi_response: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_kimi_response
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = KimiProvider()
        result = provider.invoke("Say hello", "kimi-k2-turbo", kimi_config)

        assert isinstance(result, InvokeResult)
        assert result.ok is True
        assert result.output == "Hello! How can I help you today?"
        assert result.error is None
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 8

    def test_health_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        kimi_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = KimiProvider()
        result = provider.health(kimi_config)

        assert result.ok is True
        assert result.error is None
        assert result.latency_ms >= 0

    def test_list_models_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        kimi_config: dict[str, Any],
    ) -> None:
        payload = {
            "data": [
                {"id": "kimi-k2-turbo", "object": "model"},
                {"id": "moonshot-v1-8k", "object": "model"},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload
        mock_resp.raise_for_status.return_value = None

        # KimiProvider.list_models uses requests.get directly
        monkeypatch.setattr(
            "requests.get",
            lambda _url, **kwargs: mock_resp,
        )

        provider = KimiProvider()
        result = provider.list_models(kimi_config)

        assert result.ok is True
        assert len(result.models) == 2
        assert result.models[0].id == "kimi-k2-turbo"


class TestKimiProviderEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_invoke_empty_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
        kimi_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": ""}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
        }
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = KimiProvider()
        result = provider.invoke("Say nothing", "kimi-k2-turbo", kimi_config)

        assert result.ok is True
        assert result.output == ""

    def test_invoke_missing_api_key(self, kimi_config: dict[str, Any]) -> None:
        config = {k: v for k, v in kimi_config.items() if k != "api_key"}
        provider = KimiProvider()
        result = provider.invoke("Hello", "kimi-k2-turbo", config)

        assert result.ok is False
        assert result.error is not None
        assert "api key" in result.error.lower()

    def test_health_missing_api_key(self, kimi_config: dict[str, Any]) -> None:
        config = {k: v for k, v in kimi_config.items() if k != "api_key"}
        provider = KimiProvider()
        result = provider.health(config)

        assert result.ok is False
        assert result.error is not None
        assert "api key" in result.error.lower()

    def test_list_models_missing_api_key(self, kimi_config: dict[str, Any]) -> None:
        config = {k: v for k, v in kimi_config.items() if k != "api_key"}
        provider = KimiProvider()
        result = provider.list_models(config)

        assert result.ok is False
        assert result.error is not None
        assert "api key" in result.error.lower()

    def test_validate_config_invalid_top_p(self) -> None:
        config = {
            "base_url": "https://api.moonshot.cn/v1",
            "api_key": "test",
            "top_p": 1.5,
        }
        result = KimiProvider.validate_config(config)
        assert result.valid is True
        assert any("top_p" in w.lower() for w in result.warnings)
        assert result.normalized_config is not None
        assert result.normalized_config["top_p"] == 1.0

    def test_validate_config_invalid_max_tokens(self) -> None:
        config = {
            "base_url": "https://api.moonshot.cn/v1",
            "api_key": "test",
            "max_tokens": -5,
        }
        result = KimiProvider.validate_config(config)
        assert result.valid is True
        assert any("max_tokens" in w.lower() for w in result.warnings)
        assert result.normalized_config is not None
        assert result.normalized_config["max_tokens"] == 2048

    def test_list_models_fallback_when_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
        kimi_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": []}
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "requests.get",
            lambda _url, **kwargs: mock_resp,
        )

        provider = KimiProvider()
        result = provider.list_models(kimi_config)

        assert result.ok is True
        assert len(result.models) >= 10  # fallback known models


class TestKimiProviderExceptions:
    """Tests for error and exception handling paths."""

    def test_invoke_http_401(
        self,
        monkeypatch: pytest.MonkeyPatch,
        kimi_config: dict[str, Any],
    ) -> None:
        from requests import HTTPError

        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 401
        mock_resp.text = '{"error": "Unauthorized"}'
        mock_resp.raise_for_status.side_effect = HTTPError("401 Client Error: Unauthorized")

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = KimiProvider()
        result = provider.invoke("Hello", "kimi-k2-turbo", kimi_config)

        assert result.ok is False
        assert result.error is not None
        assert "401" in result.error or "Unauthorized" in result.error

    def test_invoke_http_500_with_retry_exhausted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        kimi_config: dict[str, Any],
    ) -> None:
        from requests import HTTPError

        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 500
        mock_resp.text = '{"error": "Internal Server Error"}'
        mock_resp.raise_for_status.side_effect = HTTPError("500 Server Error")

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_sleep",
            lambda _seconds: None,
        )

        provider = KimiProvider()
        result = provider.invoke("Hello", "kimi-k2-turbo", kimi_config)

        assert result.ok is False
        assert result.error is not None
        assert "500" in result.error or "Server Error" in result.error

    def test_health_http_404(
        self,
        monkeypatch: pytest.MonkeyPatch,
        kimi_config: dict[str, Any],
    ) -> None:
        from requests import HTTPError

        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"
        mock_resp.raise_for_status.side_effect = HTTPError("404 Client Error: Not Found")

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = KimiProvider()
        result = provider.health(kimi_config)

        # health_check_post maps 404 to a specific message
        assert result.ok is False
        assert result.error is not None
        assert "api_path" in result.error.lower() or "not found" in result.error.lower()

    def test_list_models_http_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        kimi_config: dict[str, Any],
    ) -> None:
        # KimiProvider.list_models catches RuntimeError/ValueError, not HTTPError.
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 503
        mock_resp.text = "Service Unavailable"
        mock_resp.raise_for_status.side_effect = RuntimeError("503 Server Error")

        monkeypatch.setattr(
            "requests.get",
            lambda _url, **kwargs: mock_resp,
        )

        provider = KimiProvider()
        result = provider.list_models(kimi_config)

        assert result.ok is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_invoke_stream_yields_error_on_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        kimi_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 429
        mock_resp.text = "Rate limited"
        mock_resp.raise_for_status.side_effect = RuntimeError("429 Rate Limited")

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_sleep",
            lambda _seconds: None,
        )

        provider = KimiProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "kimi-k2-turbo", kimi_config):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].startswith("Error:")

    @pytest.mark.asyncio
    async def test_invoke_stream_success_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        kimi_config: dict[str, Any],
    ) -> None:
        """invoke_stream uses native SSE when get_stream_session is mocked."""
        from tests.integration.llm.providers.conftest import _make_mock_stream_session

        async def _mock_get_stream_session(*_args: Any, **_kwargs: Any) -> Any:
            return _make_mock_stream_session(
                [{"choices": [{"delta": {"content": "Hello! How can I help you today?"}}]}]
            )

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.kimi_provider.get_stream_session",
            _mock_get_stream_session,
        )

        provider = KimiProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "kimi-k2-turbo", kimi_config):
            chunks.append(chunk)

        assert len(chunks) >= 1
        assert "".join(chunks) == "Hello! How can I help you today?"
