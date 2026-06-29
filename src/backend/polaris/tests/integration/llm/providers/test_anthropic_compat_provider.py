"""Integration tests for AnthropicCompatProvider.

Covers:
- Happy path: invoke(), health(), list_models()
- Edge cases: empty response, missing API key, tool conversion
- Exception paths: HTTP 4xx/5xx, network errors
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.infrastructure.llm.providers.anthropic_compat_provider import (
    AnthropicCompatProvider,
    _convert_tool_choice_to_anthropic,
    _convert_tools_to_anthropic,
)
from polaris.kernelone.llm.providers import THINKING_PREFIX
from polaris.kernelone.llm.types import InvokeResult


class TestAnthropicCompatProviderHappyPath:
    """Tests for the normal successful execution paths."""

    def test_get_provider_info(self) -> None:
        info = AnthropicCompatProvider.get_provider_info()
        assert info.type == "anthropic_compat"
        assert "messages_api" in info.supported_features

    def test_get_default_config(self) -> None:
        defaults = AnthropicCompatProvider.get_default_config()
        assert defaults["api_path"] == "/v1/messages"
        assert defaults["anthropic_version"] == "2023-06-01"

    def test_validate_config_valid(self, anthropic_compat_config: dict[str, Any]) -> None:
        result = AnthropicCompatProvider.validate_config(anthropic_compat_config)
        assert result.valid is True
        assert not result.errors

    def test_invoke_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
        sample_anthropic_response: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_anthropic_response
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = AnthropicCompatProvider()
        result = provider.invoke("Say hello", "claude-3-haiku", anthropic_compat_config)

        assert isinstance(result, InvokeResult)
        assert result.ok is True
        assert result.output == "Hello! How can I help you today?"
        assert result.error is None
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 8

    def test_health_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = AnthropicCompatProvider()
        result = provider.health(anthropic_compat_config)

        assert result.ok is True
        assert result.error is None
        assert result.latency_ms >= 0

    def test_list_models_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
    ) -> None:
        payload = {
            "data": [
                {"id": "claude-3-opus", "object": "model"},
                {"id": "claude-3-sonnet", "object": "model"},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_get",
            lambda _url, _headers, _timeout: mock_resp,
        )

        provider = AnthropicCompatProvider()
        result = provider.list_models(anthropic_compat_config)

        assert result.ok is True
        assert len(result.models) == 2
        assert result.models[0].id == "claude-3-opus"


class TestAnthropicCompatProviderToolConversion:
    """Tests for tool and tool_choice conversion helpers."""

    def test_convert_openai_tools_to_anthropic(self) -> None:
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }
        ]
        result = _convert_tools_to_anthropic(openai_tools)
        assert len(result) == 1
        assert result[0]["name"] == "get_weather"
        assert "input_schema" in result[0]

    def test_convert_anthropic_tools_passthrough(self) -> None:
        anthropic_tools = [{"name": "get_weather", "input_schema": {"type": "object"}}]
        result = _convert_tools_to_anthropic(anthropic_tools)
        assert len(result) == 1
        assert result[0] == anthropic_tools[0]

    def test_convert_tool_choice_auto(self) -> None:
        assert _convert_tool_choice_to_anthropic("auto") == {"type": "auto"}

    def test_convert_tool_choice_required(self) -> None:
        assert _convert_tool_choice_to_anthropic("required") == {"type": "any"}

    def test_convert_tool_choice_function(self) -> None:
        choice = {"type": "function", "function": {"name": "get_weather"}}
        result = _convert_tool_choice_to_anthropic(choice)
        assert result == {"type": "tool", "name": "get_weather"}

    def test_convert_tool_choice_none(self) -> None:
        assert _convert_tool_choice_to_anthropic("none") == {"type": "none"}
        assert _convert_tool_choice_to_anthropic("") is None

    def test_convert_tool_choice_disable_parallel_tool_use(self) -> None:
        assert _convert_tool_choice_to_anthropic("auto", disable_parallel_tool_use=True) == {
            "type": "auto",
            "disable_parallel_tool_use": True,
        }

    def test_invoke_sends_tool_choice_for_standard_anthropic_endpoint(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
        sample_anthropic_response: dict[str, Any],
    ) -> None:
        captured: dict[str, Any] = {}
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_anthropic_response
        mock_resp.raise_for_status.return_value = None

        def fake_post(_url: str, _headers: dict[str, str], payload: dict[str, Any], _timeout: int) -> Any:
            captured["payload"] = payload
            return mock_resp

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            fake_post,
        )

        provider = AnthropicCompatProvider()
        config = {
            **anthropic_compat_config,
            "tools": [{"name": "edit_file", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "edit_file"},
        }
        result = provider.invoke("Edit", "claude-3-5-sonnet", config)

        assert result.ok is True
        assert captured["payload"]["tool_choice"] == {"type": "tool", "name": "edit_file"}

    def test_invoke_omits_tool_choice_for_deepseek_anthropic_endpoint(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
        sample_anthropic_response: dict[str, Any],
    ) -> None:
        captured: dict[str, Any] = {}
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_anthropic_response
        mock_resp.raise_for_status.return_value = None

        def fake_post(_url: str, _headers: dict[str, str], payload: dict[str, Any], _timeout: int) -> Any:
            captured["payload"] = payload
            return mock_resp

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            fake_post,
        )

        provider = AnthropicCompatProvider()
        config = {
            **anthropic_compat_config,
            "base_url": "https://api.deepseek.com/anthropic",
            "tools": [{"name": "edit_file", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "edit_file"},
        }
        result = provider.invoke("Edit", "deepseek-v4-pro", config)

        assert result.ok is True
        assert "tools" in captured["payload"]
        assert "tool_choice" not in captured["payload"]

    def test_invoke_omits_tool_choice_when_config_disables_it(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
        sample_anthropic_response: dict[str, Any],
    ) -> None:
        captured: dict[str, Any] = {}
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_anthropic_response
        mock_resp.raise_for_status.return_value = None

        def fake_post(_url: str, _headers: dict[str, str], payload: dict[str, Any], _timeout: int) -> Any:
            captured["payload"] = payload
            return mock_resp

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            fake_post,
        )

        provider = AnthropicCompatProvider()
        config = {
            **anthropic_compat_config,
            "disable_tool_choice": True,
            "tools": [{"name": "edit_file", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "edit_file"},
        }
        result = provider.invoke("Edit", "claude-3-5-sonnet", config)

        assert result.ok is True
        assert "tools" in captured["payload"]
        assert "tool_choice" not in captured["payload"]

    def test_invoke_sends_latest_messages_api_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
        sample_anthropic_response: dict[str, Any],
    ) -> None:
        captured: dict[str, Any] = {}
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_anthropic_response
        mock_resp.raise_for_status.return_value = None

        def fake_post(_url: str, headers: dict[str, str], payload: dict[str, Any], _timeout: int) -> Any:
            captured["headers"] = headers
            captured["payload"] = payload
            return mock_resp

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            fake_post,
        )

        provider = AnthropicCompatProvider()
        config = {
            **anthropic_compat_config,
            "anthropic_beta": "structured-outputs-2025-11-13",
            "system": [{"type": "text", "text": "You output JSON."}],
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
            "container": "container_123",
            "metadata": {"user_id": "opaque-user"},
            "output_config": {"format": {"type": "json_schema", "schema": {"type": "object"}}},
            "service_tier": "auto",
            "stop_sequences": ["</done>"],
            "thinking": {"type": "enabled", "budget_tokens": 2048, "display": "summarized"},
            "top_k": 5,
            "top_p": 0.7,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "edit_file",
                        "parameters": {"type": "object"},
                        "strict": True,
                    },
                }
            ],
            "tool_choice": "none",
        }
        result = provider.invoke("Hello", "claude-opus-4-6", config)

        assert result.ok is True
        headers = captured["headers"]
        assert headers["anthropic-version"] == "2023-06-01"
        assert headers["anthropic-beta"] == "structured-outputs-2025-11-13"
        payload = captured["payload"]
        assert payload["system"] == [{"type": "text", "text": "You output JSON."}]
        assert payload["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        assert payload["container"] == "container_123"
        assert payload["output_config"]["format"]["type"] == "json_schema"
        assert payload["thinking"]["type"] == "enabled"
        assert payload["service_tier"] == "auto"
        assert payload["stop_sequences"] == ["</done>"]
        assert payload["top_k"] == 5
        assert payload["top_p"] == 0.7
        assert payload["tool_choice"] == {"type": "none"}
        assert payload["tools"][0]["strict"] is True

    def test_invoke_enables_required_thinking_for_kimi_coding_endpoint(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
        sample_anthropic_response: dict[str, Any],
    ) -> None:
        captured: dict[str, Any] = {}
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_anthropic_response
        mock_resp.raise_for_status.return_value = None

        def fake_post(_url: str, _headers: dict[str, str], payload: dict[str, Any], _timeout: int) -> Any:
            captured["payload"] = payload
            return mock_resp

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            fake_post,
        )

        provider = AnthropicCompatProvider()
        config = {
            **anthropic_compat_config,
            "base_url": "https://api.kimi.com/coding",
            "provider_id": "kimi",
        }
        result = provider.invoke("Hello", "kimi-for-coding", config)

        assert result.ok is True
        assert captured["payload"]["thinking"] == {"type": "enabled"}

    def test_invoke_omits_disabled_thinking_for_standard_endpoint(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
        sample_anthropic_response: dict[str, Any],
    ) -> None:
        captured: dict[str, Any] = {}
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_anthropic_response
        mock_resp.raise_for_status.return_value = None

        def fake_post(_url: str, _headers: dict[str, str], payload: dict[str, Any], _timeout: int) -> Any:
            captured["payload"] = payload
            return mock_resp

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            fake_post,
        )

        provider = AnthropicCompatProvider()
        config = {
            **anthropic_compat_config,
            "thinking": {"type": "disabled", "budget_tokens": 2048},
        }
        result = provider.invoke("Hello", "claude-3-haiku", config)

        assert result.ok is True
        assert "thinking" not in captured["payload"]

    def test_request_overrides_cannot_reintroduce_invalid_thinking(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
        sample_anthropic_response: dict[str, Any],
    ) -> None:
        captured: dict[str, Any] = {}
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_anthropic_response
        mock_resp.raise_for_status.return_value = None

        def fake_post(_url: str, _headers: dict[str, str], payload: dict[str, Any], _timeout: int) -> Any:
            captured["payload"] = payload
            return mock_resp

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            fake_post,
        )

        provider = AnthropicCompatProvider()
        config = {
            **anthropic_compat_config,
            "thinking": {"type": "enabled", "budget_tokens": 2048},
            "request_overrides": {"thinking": {"type": "disabled"}},
        }
        result = provider.invoke("Hello", "kimi-for-coding", config)

        assert result.ok is True
        assert captured["payload"]["thinking"] == {"type": "enabled"}


class TestAnthropicCompatProviderEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_invoke_empty_content(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "content": [{"type": "text", "text": ""}],
            "usage": {"input_tokens": 5, "output_tokens": 0},
        }
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = AnthropicCompatProvider()
        result = provider.invoke("Say nothing", "claude-3-haiku", anthropic_compat_config)

        assert result.ok is True
        assert result.output == ""

    def test_validate_config_invalid_max_tokens(self) -> None:
        config = {"base_url": "https://api.anthropic.com", "api_path": "/v1/messages", "max_tokens": -5}
        result = AnthropicCompatProvider.validate_config(config)
        assert result.valid is True
        assert any("max_tokens" in w.lower() for w in result.warnings)
        assert result.normalized_config is not None
        assert result.normalized_config["max_tokens"] == 256

    def test_validate_config_allows_zero_max_tokens_for_cache_prewarm(self) -> None:
        config = {"base_url": "https://api.anthropic.com", "api_path": "/v1/messages", "max_tokens": 0}
        result = AnthropicCompatProvider.validate_config(config)
        assert result.valid is True
        assert result.normalized_config is not None
        assert result.normalized_config["max_tokens"] == 0

    def test_validate_config_invalid_headers_type(self) -> None:
        config = {"base_url": "https://api.anthropic.com", "api_path": "/v1/messages", "headers": "bad"}
        result = AnthropicCompatProvider.validate_config(config)
        assert result.valid is True
        assert any("headers" in w.lower() for w in result.warnings)
        assert result.normalized_config is not None
        assert result.normalized_config["headers"] == {}

    def test_list_models_empty_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": []}
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_get",
            lambda _url, _headers, _timeout: mock_resp,
        )

        provider = AnthropicCompatProvider()
        result = provider.list_models(anthropic_compat_config)

        assert result.ok is True
        assert result.models == []


class TestAnthropicCompatProviderExceptions:
    """Tests for error and exception handling paths."""

    def test_invoke_http_401(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
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

        provider = AnthropicCompatProvider()
        result = provider.invoke("Hello", "claude-3-haiku", anthropic_compat_config)

        assert result.ok is False
        assert result.error is not None
        assert "401" in result.error or "Unauthorized" in result.error

    def test_invoke_http_500_with_retry_exhausted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
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

        provider = AnthropicCompatProvider()
        result = provider.invoke("Hello", "claude-3-haiku", anthropic_compat_config)

        assert result.ok is False
        assert result.error is not None
        assert "500" in result.error or "Server Error" in result.error

    def test_health_http_404(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
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

        provider = AnthropicCompatProvider()
        result = provider.health(anthropic_compat_config)

        # health_check_post maps 404 to a specific message
        assert result.ok is False
        assert result.error is not None
        assert "api_path" in result.error.lower() or "not found" in result.error.lower()

    def test_list_models_http_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
    ) -> None:
        # list_models_from_api catches RuntimeError/ValueError, not HTTPError.
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 503
        mock_resp.text = "Service Unavailable"
        mock_resp.raise_for_status.side_effect = RuntimeError("503 Server Error")

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_get",
            lambda _url, _headers, _timeout: mock_resp,
        )

        provider = AnthropicCompatProvider()
        result = provider.list_models(anthropic_compat_config)

        assert result.ok is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_invoke_stream_yields_error_on_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
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

        provider = AnthropicCompatProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "claude-3-haiku", anthropic_compat_config):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].startswith("Error:")

    @pytest.mark.asyncio
    async def test_invoke_stream_success_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
    ) -> None:
        """invoke_stream uses native SSE when invoke_stream_with_retry is mocked."""

        async def _mock_invoke_stream_with_retry(*_args: Any, **_kwargs: Any) -> Any:
            yield {"delta": {"text": "Hello! How can I help you today?"}}

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.anthropic_compat_provider.invoke_stream_with_retry",
            _mock_invoke_stream_with_retry,
        )

        provider = AnthropicCompatProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "claude-3-haiku", anthropic_compat_config):
            chunks.append(chunk)

        assert len(chunks) >= 1
        assert "".join(chunks) == "Hello! How can I help you today?"

    @pytest.mark.asyncio
    async def test_invoke_stream_handles_anthropic_thinking_delta_and_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
    ) -> None:
        async def _mock_invoke_stream_with_retry(*_args: Any, **_kwargs: Any) -> Any:
            yield {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "reason"}}
            yield {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "answer"}}
            yield {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.anthropic_compat_provider.invoke_stream_with_retry",
            _mock_invoke_stream_with_retry,
        )

        provider = AnthropicCompatProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "claude-3-haiku", anthropic_compat_config):
            chunks.append(chunk)

        assert chunks[0] == f"{THINKING_PREFIX}reason"
        assert "answer" in chunks
        assert chunks[-1] == "Error: Overloaded"
