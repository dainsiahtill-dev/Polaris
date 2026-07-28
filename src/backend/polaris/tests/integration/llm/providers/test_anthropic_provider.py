"""Integration tests for AnthropicProvider.

Covers:
- Happy path: invoke(), health(), list_models()
- Edge cases: empty response, missing API key, tool conversion
- Exception paths: HTTP 4xx/5xx, network errors
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.infrastructure.llm.providers.anthropic_provider import (
    AnthropicProvider,
    _convert_tool_choice_to_anthropic,
    _convert_tools_to_anthropic,
)
from polaris.kernelone.llm.engine.provider_native_request import (
    FactoryProviderNativeRequestProjectionError,
    project_factory_provider_native_request,
)
from polaris.kernelone.llm.providers import THINKING_PREFIX
from polaris.kernelone.llm.types import InvokeResult


class TestAnthropicProviderHappyPath:
    """Tests for the normal successful execution paths."""

    def test_get_provider_info(self) -> None:
        info = AnthropicProvider.get_provider_info()
        assert info.type == "anthropic_compat"
        assert "messages_api" in info.supported_features

    def test_get_default_config(self) -> None:
        defaults = AnthropicProvider.get_default_config()
        assert defaults["api_path"] == "/v1/messages"
        assert defaults["anthropic_version"] == "2023-06-01"

    def test_validate_config_valid(self, anthropic_compat_config: dict[str, Any]) -> None:
        result = AnthropicProvider.validate_config(anthropic_compat_config)
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

        provider = AnthropicProvider()
        result = provider.invoke("Say hello", "claude-3-haiku", anthropic_compat_config)

        assert isinstance(result, InvokeResult)
        assert result.ok is True
        assert result.output == "Hello! How can I help you today?"
        assert result.error is None
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 8

    def test_factory_chat_messages_match_exact_anthropic_native_projection(
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

        def fake_post(url: str, _headers: dict[str, str], payload: dict[str, Any], _timeout: int) -> Any:
            captured["url"] = url
            captured["payload"] = payload
            return mock_resp

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            fake_post,
        )
        messages = [
            {"role": "system", "content": "You are the Chief Engineer."},
            {"role": "user", "content": "Inspect the target files."},
        ]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read one file",
                    "parameters": {"type": "object"},
                },
            }
        ]
        config = {
            **anthropic_compat_config,
            "chat_messages": messages,
            "temperature": 0.0,
            "max_tokens": 512,
            "tools": tools,
            "tool_choice": "auto",
        }
        semantic_payload = {
            "model": "claude-3-5-sonnet",
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "response_format": None,
            "temperature": 0.0,
            # Frozen caller request before the Engine applies the provider/model
            # output ceiling.  ``config`` is the final Engine invoke authority.
            "max_tokens": 128_000,
            "stream": False,
        }

        result = AnthropicProvider().invoke("flattened prompt", "claude-3-5-sonnet", config)
        projection = project_factory_provider_native_request(
            provider_type="anthropic_compat",
            mode="invoke",
            final_payload=semantic_payload,
            provider_config=config,
        )

        assert result.ok is True
        assert projection is not None
        assert captured["url"] == projection.exact_endpoint
        assert captured["payload"] == projection.expected_body()
        assert captured["payload"]["max_tokens"] == 512
        assert captured["payload"]["system"] == "You are the Chief Engineer."
        assert all(message["role"] != "system" for message in captured["payload"]["messages"])

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

        provider = AnthropicProvider()
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

        provider = AnthropicProvider()
        result = provider.list_models(anthropic_compat_config)

        assert result.ok is True
        assert len(result.models) == 2
        assert result.models[0].id == "claude-3-opus"


class TestAnthropicProviderToolConversion:
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

        provider = AnthropicProvider()
        config = {
            **anthropic_compat_config,
            "tools": [{"name": "edit_file", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "edit_file"},
        }
        result = provider.invoke("Edit", "claude-3-5-sonnet", config)

        assert result.ok is True
        assert captured["payload"]["tool_choice"] == {"type": "tool", "name": "edit_file"}

    def test_invoke_sends_tool_choice_for_deepseek_anthropic_endpoint(
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

        provider = AnthropicProvider()
        config = {
            **anthropic_compat_config,
            "base_url": "https://api.deepseek.com/anthropic",
            "tools": [{"name": "edit_file", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "edit_file"},
        }
        result = provider.invoke("Edit", "deepseek-v4-pro", config)

        assert result.ok is True
        assert "tools" in captured["payload"]
        assert captured["payload"]["tool_choice"] == {"type": "tool", "name": "edit_file"}
        assert captured["payload"]["thinking"] == {"type": "disabled"}

    def test_invoke_rejects_explicit_deepseek_thinking_with_forced_tool_choice(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
    ) -> None:
        fake_post = MagicMock(side_effect=AssertionError("incompatible request must fail before transport"))
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            fake_post,
        )

        provider = AnthropicProvider()
        result = provider.invoke(
            "Edit",
            "deepseek-v4-pro",
            {
                **anthropic_compat_config,
                "base_url": "https://api.deepseek.com/anthropic",
                "thinking": {"type": "enabled"},
                "tools": [{"name": "edit_file", "input_schema": {"type": "object"}}],
                "tool_choice": {"type": "tool", "name": "edit_file"},
            },
        )

        assert result.ok is False
        assert result.error == "factory_provider_native_request_thinking_tool_choice_conflict:anthropic_messages"
        fake_post.assert_not_called()

    def test_invoke_rejects_specified_tool_choice_for_kimi_coding_thinking_endpoint(
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

        provider = AnthropicProvider()
        config = {
            **anthropic_compat_config,
            "base_url": "https://api.kimi.com/coding",
            "provider_id": "kimi",
            "tools": [{"name": "write_file", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "write_file"},
        }
        result = provider.invoke("Edit", "kimi-for-coding", config)

        assert result.ok is False
        assert result.error == "factory_provider_native_request_tool_choice_unsupported:anthropic_messages"
        assert captured == {}

    def test_invoke_rejects_forced_tool_choice_when_config_disables_it(
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

        provider = AnthropicProvider()
        config = {
            **anthropic_compat_config,
            "disable_tool_choice": True,
            "tools": [{"name": "edit_file", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "edit_file"},
        }
        result = provider.invoke("Edit", "claude-3-5-sonnet", config)

        assert result.ok is False
        assert result.error == "factory_provider_native_request_tool_choice_unsupported:anthropic_messages"
        assert captured == {}

    def test_invoke_rejects_forced_tool_choice_without_tools(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
    ) -> None:
        provider_called = False

        def fake_post(_url: str, _headers: dict[str, str], _payload: dict[str, Any], _timeout: int) -> Any:
            nonlocal provider_called
            provider_called = True
            raise AssertionError("provider transport must not run")

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            fake_post,
        )

        result = AnthropicProvider().invoke(
            "Edit",
            "claude-3-5-sonnet",
            {
                **anthropic_compat_config,
                "tools": [],
                "tool_choice": "required",
            },
        )

        assert result.ok is False
        assert result.error == "factory_provider_native_request_tool_choice_without_tools:anthropic_messages"
        assert provider_called is False

    def test_invoke_rejects_lossy_tool_schema_before_transport(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
    ) -> None:
        provider_called = False

        def fake_post(_url: str, _headers: dict[str, str], _payload: dict[str, Any], _timeout: int) -> Any:
            nonlocal provider_called
            provider_called = True
            raise AssertionError("provider transport must not run")

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            fake_post,
        )

        result = AnthropicProvider().invoke(
            "Read",
            "claude-3-5-sonnet",
            {
                **anthropic_compat_config,
                "tools": [{"name": "read_file", "parameters": "not-a-schema"}],
            },
        )

        assert result.ok is False
        assert result.error == "factory_provider_native_request_tools_unrepresentable:anthropic_messages"
        assert provider_called is False

    def test_invoke_rejects_forced_choice_not_in_tools_before_transport(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
    ) -> None:
        provider_called = False

        def fake_post(_url: str, _headers: dict[str, str], _payload: dict[str, Any], _timeout: int) -> Any:
            nonlocal provider_called
            provider_called = True
            raise AssertionError("provider transport must not run")

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            fake_post,
        )

        result = AnthropicProvider().invoke(
            "Write",
            "claude-3-5-sonnet",
            {
                **anthropic_compat_config,
                "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
                "tool_choice": {"type": "tool", "name": "write_file"},
            },
        )

        assert result.ok is False
        assert result.error == "factory_provider_native_request_tool_choice_unknown_tool:anthropic_messages"
        assert provider_called is False

    def test_invoke_rejects_implicit_kimi_parallel_constraint_before_transport(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
    ) -> None:
        provider_called = False

        def fake_post(_url: str, _headers: dict[str, str], _payload: dict[str, Any], _timeout: int) -> Any:
            nonlocal provider_called
            provider_called = True
            raise AssertionError("provider transport must not run")

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            fake_post,
        )

        result = AnthropicProvider().invoke(
            "Read",
            "kimi-for-coding",
            {
                **anthropic_compat_config,
                "base_url": "https://api.kimi.com/coding/v1",
                "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
                "tool_choice": None,
                "disable_parallel_tool_use": True,
            },
        )

        assert result.ok is False
        assert result.error == "factory_provider_native_request_tool_choice_unsupported:anthropic_messages"
        assert provider_called is False

    def test_invoke_validates_effective_tool_choice_after_request_override(
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

        result = AnthropicProvider().invoke(
            "Read",
            "claude-3-5-sonnet",
            {
                **anthropic_compat_config,
                "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
                "tool_choice": {"type": "tool", "name": "write_file"},
                "request_overrides": {"tool_choice": "auto"},
            },
        )

        assert result.ok is True
        assert captured["payload"]["tool_choice"] == {"type": "auto"}
        assert captured["payload"]["tools"] == [{"name": "read_file", "input_schema": {"type": "object"}}]

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

        provider = AnthropicProvider()
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

        provider = AnthropicProvider()
        config = {
            **anthropic_compat_config,
            "base_url": "https://api.kimi.com/coding",
            "provider_id": "kimi",
        }
        result = provider.invoke("Hello", "kimi-for-coding", config)

        assert result.ok is True
        assert captured["payload"]["thinking"] == {"type": "enabled"}

    def test_invoke_applies_call_scoped_reasoning_budget_for_kimi_coding_endpoint(
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

        config = {
            **anthropic_compat_config,
            "base_url": "https://api.kimi.com/coding",
            "provider_id": "kimi",
            "max_tokens": 8_192,
            "reasoning_budget_tokens": 2_048,
        }
        result = AnthropicProvider().invoke("Hello", "kimi-for-coding", config)

        assert result.ok is True
        assert captured["payload"]["thinking"] == {"type": "enabled", "budget_tokens": 2_048}

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

        provider = AnthropicProvider()
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

        provider = AnthropicProvider()
        config = {
            **anthropic_compat_config,
            "thinking": {"type": "enabled", "budget_tokens": 2048},
            "request_overrides": {"thinking": {"type": "disabled"}},
        }
        result = provider.invoke("Hello", "kimi-for-coding", config)

        assert result.ok is True
        assert captured["payload"]["thinking"] == {"type": "enabled"}

    def test_request_overrides_cannot_reintroduce_unsupported_tool_choice_for_kimi(
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

        provider = AnthropicProvider()
        config = {
            **anthropic_compat_config,
            "base_url": "https://api.kimi.com/coding",
            "provider_id": "kimi",
            "tools": [{"name": "write_file", "input_schema": {"type": "object"}}],
            "tool_choice": "auto",
            "request_overrides": {"tool_choice": "required"},
        }
        result = provider.invoke("Edit", "kimi-for-coding", config)

        assert result.ok is False
        assert result.error == "factory_provider_native_request_tool_choice_unsupported:anthropic_messages"
        assert captured == {}


class TestAnthropicProviderEdgeCases:
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

        provider = AnthropicProvider()
        result = provider.invoke("Say nothing", "claude-3-haiku", anthropic_compat_config)

        assert result.ok is True
        assert result.output == ""

    def test_validate_config_invalid_max_tokens(self) -> None:
        config = {"base_url": "https://api.anthropic.com", "api_path": "/v1/messages", "max_tokens": -5}
        result = AnthropicProvider.validate_config(config)
        assert result.valid is True
        assert any("max_tokens" in w.lower() for w in result.warnings)
        assert result.normalized_config is not None
        assert result.normalized_config["max_tokens"] == 256

    def test_validate_config_allows_zero_max_tokens_for_cache_prewarm(self) -> None:
        config = {"base_url": "https://api.anthropic.com", "api_path": "/v1/messages", "max_tokens": 0}
        result = AnthropicProvider.validate_config(config)
        assert result.valid is True
        assert result.normalized_config is not None
        assert result.normalized_config["max_tokens"] == 0

    def test_validate_config_invalid_headers_type(self) -> None:
        config = {"base_url": "https://api.anthropic.com", "api_path": "/v1/messages", "headers": "bad"}
        result = AnthropicProvider.validate_config(config)
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

        provider = AnthropicProvider()
        result = provider.list_models(anthropic_compat_config)

        assert result.ok is True
        assert result.models == []


class TestAnthropicProviderExceptions:
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

        provider = AnthropicProvider()
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

        provider = AnthropicProvider()
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

        provider = AnthropicProvider()
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

        provider = AnthropicProvider()
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

        provider = AnthropicProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "claude-3-haiku", anthropic_compat_config):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].startswith("Error:")

    @pytest.mark.asyncio
    async def test_invoke_stream_rejects_unsupported_forced_tool_choice_before_transport(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
    ) -> None:
        provider_called = False

        async def _transport_must_not_run(*_args: Any, **_kwargs: Any) -> Any:
            nonlocal provider_called
            provider_called = True
            yield {}

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.anthropic_provider.invoke_stream_with_retry",
            _transport_must_not_run,
        )
        config = {
            **anthropic_compat_config,
            "base_url": "https://api.kimi.com/coding",
            "tools": [{"name": "write_file", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "write_file"},
        }

        chunks = [
            chunk
            async for chunk in AnthropicProvider().invoke_stream(
                "Edit",
                "kimi-for-coding",
                config,
            )
        ]

        assert chunks == ["Error: factory_provider_native_request_tool_choice_unsupported:anthropic_messages"]
        assert provider_called is False

    @pytest.mark.asyncio
    async def test_invoke_stream_rejects_lossy_tool_schema_before_transport(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
    ) -> None:
        provider_called = False

        async def _transport_must_not_run(*_args: Any, **_kwargs: Any) -> Any:
            nonlocal provider_called
            provider_called = True
            yield {}

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.anthropic_provider.invoke_stream_with_retry",
            _transport_must_not_run,
        )
        config = {
            **anthropic_compat_config,
            "tools": [{"name": "read_file", "parameters": "not-a-schema"}],
        }

        chunks = [
            chunk
            async for chunk in AnthropicProvider().invoke_stream(
                "Read",
                "claude-3-5-sonnet",
                config,
            )
        ]

        assert chunks == ["Error: factory_provider_native_request_tools_unrepresentable:anthropic_messages"]
        assert provider_called is False

    @pytest.mark.asyncio
    async def test_invoke_stream_rejects_deepseek_parallel_constraint_before_transport(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
    ) -> None:
        provider_called = False

        async def _transport_must_not_run(*_args: Any, **_kwargs: Any) -> Any:
            nonlocal provider_called
            provider_called = True
            yield {}

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.anthropic_provider.invoke_stream_with_retry",
            _transport_must_not_run,
        )
        config = {
            **anthropic_compat_config,
            "base_url": "https://anthropic-proxy.test/v1",
            "name": "DeepSeek Official",
            "provider_id": "deepseek",
            "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
            "tool_choice": "auto",
            "disable_parallel_tool_use": True,
        }

        chunks = [
            chunk
            async for chunk in AnthropicProvider().invoke_stream(
                "Read",
                "deepseek-v4-pro",
                config,
            )
        ]

        assert chunks == ["Error: factory_provider_native_request_parallel_tool_choice_unsupported:anthropic_messages"]
        assert provider_called is False

    @pytest.mark.asyncio
    async def test_invoke_stream_deepseek_forced_tool_choice_disables_thinking_on_wire(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
    ) -> None:
        captured: dict[str, Any] = {}

        async def _capture_transport(
            _url: str,
            _headers: dict[str, str],
            payload: dict[str, Any],
            *,
            timeout_seconds: float,
        ) -> Any:
            captured["payload"] = payload
            captured["timeout_seconds"] = timeout_seconds
            yield {"type": "message_stop"}

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.anthropic_provider.invoke_stream_with_retry",
            _capture_transport,
        )
        config = {
            **anthropic_compat_config,
            "base_url": "https://api.deepseek.com/anthropic",
            "provider_id": "deepseek",
            "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "read_file"},
        }

        events = [
            event
            async for event in AnthropicProvider().invoke_stream_events(
                "Read",
                "deepseek-v4-pro",
                config,
            )
        ]

        assert events == [{"type": "message_stop"}]
        assert captured["payload"]["tool_choice"] == {"type": "tool", "name": "read_file"}
        assert captured["payload"]["thinking"] == {"type": "disabled"}

    @pytest.mark.asyncio
    async def test_invoke_stream_rejects_explicit_deepseek_thinking_with_tool_choice_before_transport(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
    ) -> None:
        provider_called = False

        async def _transport_must_not_run(*_args: Any, **_kwargs: Any) -> Any:
            nonlocal provider_called
            provider_called = True
            yield {}

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.anthropic_provider.invoke_stream_with_retry",
            _transport_must_not_run,
        )
        config = {
            **anthropic_compat_config,
            "base_url": "https://api.deepseek.com/anthropic",
            "provider_id": "deepseek",
            "thinking": {"type": "enabled", "budget_tokens": 1_024},
            "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "read_file"},
        }

        with pytest.raises(
            FactoryProviderNativeRequestProjectionError,
            match="factory_provider_native_request_thinking_tool_choice_conflict:anthropic_messages",
        ):
            async for _ in AnthropicProvider().invoke_stream_events(
                "Read",
                "deepseek-v4-pro",
                config,
            ):
                pass

        assert provider_called is False

    @pytest.mark.asyncio
    async def test_invoke_stream_validates_effective_tool_choice_after_request_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_compat_config: dict[str, Any],
    ) -> None:
        captured: dict[str, Any] = {}

        async def _capture_transport(
            _url: str,
            _headers: dict[str, str],
            payload: dict[str, Any],
            *,
            timeout_seconds: float,
        ) -> Any:
            captured["payload"] = payload
            captured["timeout_seconds"] = timeout_seconds
            yield {"type": "message_stop"}

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.anthropic_provider.invoke_stream_with_retry",
            _capture_transport,
        )
        config = {
            **anthropic_compat_config,
            "base_url": "https://api.kimi.com/coding/v1",
            "tools": [{"name": "read_file", "input_schema": {"type": "object"}}],
            "tool_choice": "required",
            "request_overrides": {"tool_choice": "auto"},
        }

        events = [
            event
            async for event in AnthropicProvider().invoke_stream_events(
                "Read",
                "kimi-for-coding",
                config,
            )
        ]

        assert events == [{"type": "message_stop"}]
        assert "tool_choice" not in captured["payload"]
        assert captured["payload"]["tools"] == [{"name": "read_file", "input_schema": {"type": "object"}}]

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
            "polaris.infrastructure.llm.providers.anthropic_provider.invoke_stream_with_retry",
            _mock_invoke_stream_with_retry,
        )

        provider = AnthropicProvider()
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
            "polaris.infrastructure.llm.providers.anthropic_provider.invoke_stream_with_retry",
            _mock_invoke_stream_with_retry,
        )

        provider = AnthropicProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "claude-3-haiku", anthropic_compat_config):
            chunks.append(chunk)

        assert chunks[0] == f"{THINKING_PREFIX}reason"
        assert "answer" in chunks
        assert chunks[-1] == "Error: Overloaded"
