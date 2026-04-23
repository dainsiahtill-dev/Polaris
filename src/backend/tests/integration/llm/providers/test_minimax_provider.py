"""Integration tests for MiniMaxProvider.

Covers:
- Happy path: invoke(), health(), list_models()
- Edge cases: empty response, missing API key, invalid model
- Exception paths: HTTP 4xx/5xx, network errors, timeout, circuit breaker
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.infrastructure.llm.providers.minimax_provider import MiniMaxProvider
from polaris.infrastructure.llm.providers.provider_helpers import CircuitOpenError
from polaris.kernelone.llm.types import InvokeResult


class TestMiniMaxProviderHappyPath:
    """Tests for the normal successful execution paths."""

    def test_get_provider_info(self) -> None:
        info = MiniMaxProvider.get_provider_info()
        assert info.type == "minimax"
        assert "thinking_extraction" in info.supported_features
        assert info.provider_category == "LLM"

    def test_get_default_config(self) -> None:
        defaults = MiniMaxProvider.get_default_config()
        assert defaults["base_url"] == "https://api.minimaxi.com/v1"
        assert defaults["api_path"] == "/text/chatcompletion_v2"
        assert defaults["max_tokens"] == 2048

    def test_validate_config_valid(self, minimax_config: dict[str, Any]) -> None:
        result = MiniMaxProvider.validate_config(minimax_config)
        assert result.valid is True
        assert not result.errors

    def test_invoke_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        minimax_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.json.return_value = {
            "base_resp": {"status_code": 0, "status_msg": "Success"},
            "choices": [
                {
                    "message": {
                        "content": "Hello! How can I help you today?",
                        "role": "assistant",
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
        }

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.minimax_provider._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = MiniMaxProvider()
        result = provider.invoke("Say hello", "MiniMax-M2.1", minimax_config)

        assert isinstance(result, InvokeResult)
        assert result.ok is True
        assert result.output == "Hello! How can I help you today?"
        assert result.error is None
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 8

    def test_invoke_with_reasoning_content(
        self,
        monkeypatch: pytest.MonkeyPatch,
        minimax_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.json.return_value = {
            "base_resp": {"status_code": 0, "status_msg": "Success"},
            "choices": [
                {
                    "message": {
                        "content": "The answer is 42.",
                        "role": "assistant",
                        "reasoning_content": "Let me calculate: 6 * 7 = 42",
                    }
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
        }

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.minimax_provider._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = MiniMaxProvider()
        result = provider.invoke("What is 6x7?", "MiniMax-M2.1", minimax_config)

        assert result.ok is True
        assert result.output == "The answer is 42."
        assert result.thinking is not None
        assert "calculate" in result.thinking

    def test_health_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        minimax_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "OK"

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.minimax_provider._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = MiniMaxProvider()
        result = provider.health(minimax_config)

        assert result.ok is True
        assert result.error is None
        assert result.latency_ms >= 0

    def test_list_models(self, minimax_config: dict[str, Any]) -> None:
        provider = MiniMaxProvider()
        result = provider.list_models(minimax_config)

        assert result.ok is True
        assert result.supported is True
        assert len(result.models) == 3
        assert result.models[0].id == "MiniMax-M2.1"


class TestMiniMaxProviderEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_invoke_empty_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
        minimax_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.json.return_value = {
            "base_resp": {"status_code": 0, "status_msg": "Success"},
            "choices": [{"message": {"content": "", "role": "assistant"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 0, "total_tokens": 5},
        }

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.minimax_provider._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = MiniMaxProvider()
        result = provider.invoke("Say nothing", "MiniMax-M2.1", minimax_config)

        assert result.ok is False
        assert result.error is not None
        assert "empty" in result.error.lower()

    def test_invoke_missing_api_key(self, minimax_config: dict[str, Any]) -> None:
        config = {k: v for k, v in minimax_config.items() if k != "api_key"}
        provider = MiniMaxProvider()
        result = provider.invoke("Hello", "MiniMax-M2.1", config)

        assert result.ok is False
        assert result.error is not None
        assert "api key" in result.error.lower()

    def test_health_missing_api_key(self, minimax_config: dict[str, Any]) -> None:
        config = {k: v for k, v in minimax_config.items() if k != "api_key"}
        provider = MiniMaxProvider()
        result = provider.health(config)

        assert result.ok is False
        assert result.error is not None
        assert "api key" in result.error.lower()

    def test_validate_config_missing_base_url(self) -> None:
        config: dict[str, Any] = {
            "api_key": "test-key",
            "api_path": "/text/chatcompletion_v2",
        }
        result = MiniMaxProvider.validate_config(config)
        assert result.valid is False
        assert any("base url" in e.lower() for e in result.errors)

    def test_validate_config_missing_api_key(self) -> None:
        config: dict[str, Any] = {
            "base_url": "https://api.minimaxi.com/v1",
            "api_path": "/text/chatcompletion_v2",
        }
        result = MiniMaxProvider.validate_config(config)
        assert result.valid is False
        assert any("api key" in e.lower() for e in result.errors)

    def test_validate_config_negative_timeout(self) -> None:
        config: dict[str, Any] = {
            "base_url": "https://api.minimaxi.com/v1",
            "api_key": "test",
            "timeout": -10,
        }
        result = MiniMaxProvider.validate_config(config)
        assert result.valid is True
        assert any("timeout" in w.lower() for w in result.warnings)
        assert result.normalized_config is not None
        assert result.normalized_config["timeout"] == 60

    def test_validate_config_ssrf_blocked(self) -> None:
        config: dict[str, Any] = {
            "base_url": "http://192.168.1.1/v1",
            "api_key": "test",
        }
        result = MiniMaxProvider.validate_config(config)
        assert result.valid is False
        assert any("ssrf" in e.lower() for e in result.errors)

    def test_invoke_empty_model_uses_config_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        minimax_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.json.return_value = {
            "base_resp": {"status_code": 0, "status_msg": "Success"},
            "choices": [{"message": {"content": "Hello!", "role": "assistant"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.minimax_provider._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = MiniMaxProvider()
        result = provider.invoke("Say hello", "", minimax_config)

        assert result.ok is True
        assert result.output == "Hello!"

    def test_build_request_payload_with_tools(self, minimax_config: dict[str, Any]) -> None:
        provider = MiniMaxProvider()
        config = {
            **minimax_config,
            "tools": [{"type": "function", "function": {"name": "get_weather"}}],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "response_format": {"type": "json_object"},
        }
        payload = provider._build_request_payload("What's the weather?", "MiniMax-M2.1", config, stream=False)

        assert payload["tools"] is not None
        assert payload["tool_choice"] == "auto"
        assert payload["parallel_tool_calls"] is True
        assert payload["response_format"] == {"type": "json_object"}


class TestMiniMaxProviderExceptions:
    """Tests for error and exception handling paths."""

    def test_invoke_http_401(
        self,
        monkeypatch: pytest.MonkeyPatch,
        minimax_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = '{"error": "Unauthorized"}'

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.minimax_provider._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = MiniMaxProvider()
        result = provider.invoke("Hello", "MiniMax-M2.1", minimax_config)

        assert result.ok is False
        assert result.error is not None
        assert "401" in result.error

    def test_invoke_http_500_with_retry_exhausted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        minimax_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = '{"error": "Internal Server Error"}'

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.minimax_provider._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.minimax_provider._blocking_sleep",
            lambda _seconds: None,
        )

        provider = MiniMaxProvider()
        result = provider.invoke("Hello", "MiniMax-M2.1", minimax_config)

        assert result.ok is False
        assert result.error is not None
        assert "500" in result.error

    def test_health_http_404(
        self,
        monkeypatch: pytest.MonkeyPatch,
        minimax_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.minimax_provider._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = MiniMaxProvider()
        result = provider.health(minimax_config)

        assert result.ok is False
        assert result.error is not None
        assert "404" in result.error

    def test_health_connection_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        minimax_config: dict[str, Any],
    ) -> None:
        import requests

        def _mock_post(*_args: Any, **_kwargs: Any) -> Any:
            raise requests.exceptions.ConnectionError("Connection refused")

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.minimax_provider._blocking_http_post",
            _mock_post,
        )

        provider = MiniMaxProvider()
        result = provider.health(minimax_config)

        assert result.ok is False
        assert result.error is not None
        assert "connect" in result.error.lower()

    def test_invoke_circuit_open_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        minimax_config: dict[str, Any],
    ) -> None:
        def _mock_post(*_args: Any, **_kwargs: Any) -> Any:
            raise CircuitOpenError("circuit_open:30s_remaining")

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.minimax_provider._blocking_http_post",
            _mock_post,
        )

        provider = MiniMaxProvider()
        result = provider.invoke("Hello", "MiniMax-M2.1", minimax_config)

        assert result.ok is False
        assert result.error is not None
        assert "circuit_open" in result.error

    def test_invoke_json_parse_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        minimax_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.json.side_effect = ValueError("Invalid JSON")
        mock_resp.text = "not valid json"

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.minimax_provider._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = MiniMaxProvider()
        result = provider.invoke("Hello", "MiniMax-M2.1", minimax_config)

        assert result.ok is False
        assert result.error is not None
        assert "json parse error" in result.error.lower()

    def test_invoke_base_resp_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        minimax_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.json.return_value = {
            "base_resp": {"status_code": 1001, "status_msg": "Invalid parameter"},
            "choices": [],
        }

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.minimax_provider._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = MiniMaxProvider()
        result = provider.invoke("Hello", "MiniMax-M2.1", minimax_config)

        assert result.ok is False
        assert result.error is not None
        assert "1001" in result.error
        assert "Invalid parameter" in result.error

    @pytest.mark.asyncio
    async def test_invoke_stream_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        minimax_config: dict[str, Any],
    ) -> None:
        from tests.integration.llm.providers.conftest import _make_mock_stream_session

        async def _mock_get_stream_session(*_args: Any, **_kwargs: Any) -> Any:
            return _make_mock_stream_session(
                [
                    {"choices": [{"delta": {"content": "Hello! "}}]},
                    {"choices": [{"delta": {"content": "How can I help?"}}]},
                ]
            )

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.minimax_provider.get_stream_session",
            _mock_get_stream_session,
        )

        provider = MiniMaxProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "MiniMax-M2.1", minimax_config):
            chunks.append(chunk)

        assert len(chunks) >= 1
        assert "".join(chunks) == "Hello! How can I help?"

    @pytest.mark.asyncio
    async def test_invoke_stream_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        minimax_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = "Rate limited"

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.minimax_provider._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.minimax_provider._blocking_sleep",
            lambda _seconds: None,
        )

        provider = MiniMaxProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "MiniMax-M2.1", minimax_config):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].startswith("Error:")

    def test_clean_content_strips_tags(self) -> None:
        provider = MiniMaxProvider()
        raw = "<think>Thinking...</think>Answer here."
        cleaned = provider._clean_content(raw)
        assert "<think>" not in cleaned
        assert "Answer here." in cleaned

    def test_extract_thinking(self) -> None:
        provider = MiniMaxProvider()
        raw = "<think>Deep thought</think>Answer."
        thinking = provider._extract_thinking(raw)
        assert thinking == "Deep thought"

    def test_extract_thinking_no_match(self) -> None:
        provider = MiniMaxProvider()
        raw = "Just a regular answer."
        thinking = provider._extract_thinking(raw)
        assert thinking is None
