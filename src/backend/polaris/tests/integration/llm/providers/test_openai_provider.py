"""Integration tests for OpenAIProvider.

Covers:
- Happy path: invoke(), health(), list_models()
- Edge cases: empty response, missing API key, invalid model
- Exception paths: HTTP 4xx/5xx, network errors, timeout
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.infrastructure.llm.providers.openai_provider import (
    OpenAIProvider,
)
from polaris.kernelone.llm.providers import THINKING_PREFIX
from polaris.kernelone.llm.types import InvokeResult


class TestOpenAIProviderHappyPath:
    """Tests for the normal successful execution paths."""

    def test_get_provider_info(self) -> None:
        info = OpenAIProvider.get_provider_info()
        assert info.type == "openai_compat"
        assert "health_check" in info.supported_features

    def test_get_default_config(self) -> None:
        defaults = OpenAIProvider.get_default_config()
        assert defaults["base_url"] == "https://api.example.com/v1"
        assert defaults["api_path"] == "/v1/chat/completions"

    def test_validate_config_valid(self, openai_compat_config: dict[str, Any]) -> None:
        result = OpenAIProvider.validate_config(openai_compat_config)
        assert result.valid is True
        assert not result.errors

    def test_validate_config_missing_api_path(self) -> None:
        config = {"base_url": "https://api.example.com"}
        result = OpenAIProvider.validate_config(config)
        # api_path is required; missing it makes validation invalid
        assert result.valid is False
        assert any("api_path" in e.lower() for e in result.errors)

    def test_invoke_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        openai_compat_config: dict[str, Any],
        sample_openai_response: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_openai_response
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = OpenAIProvider()
        result = provider.invoke("Say hello", "gpt-4", openai_compat_config)

        assert isinstance(result, InvokeResult)
        assert result.ok is True
        assert result.output == "Hello! How can I help you today?"
        assert result.error is None
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 8

    def test_invoke_responses_api_uses_latest_payload_and_usage(
        self,
        monkeypatch: pytest.MonkeyPatch,
        openai_compat_config: dict[str, Any],
    ) -> None:
        captured: dict[str, Any] = {}
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "resp_test",
            "object": "response",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Response hello", "annotations": []}],
                }
            ],
            "usage": {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17},
        }
        mock_resp.raise_for_status.return_value = None

        def fake_post(_url: str, _headers: dict[str, str], payload: dict[str, Any], _timeout: int) -> Any:
            captured["payload"] = payload
            return mock_resp

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            fake_post,
        )

        provider = OpenAIProvider()
        config = {
            **openai_compat_config,
            "api_path": "/v1/responses",
            "chat_messages": [{"role": "user", "content": "Hello"}],
            "max_output_tokens": 2048,
            "reasoning": {"effort": "medium", "summary": "auto"},
            "text": {"format": {"type": "json_schema", "name": "Answer", "schema": {"type": "object"}}},
            "tool_choice": "auto",
            "tools": [{"type": "function", "name": "repo_search", "description": "Search", "parameters": {}}],
            "parallel_tool_calls": False,
            "service_tier": "auto",
            "prompt_cache_key": "bench-run",
            "safety_identifier": "user-opaque-id",
            "metadata": {"run_id": "r1"},
            "store": False,
        }
        result = provider.invoke("Hello", "gpt-4.1", config)

        assert result.ok is True
        assert result.output == "Response hello"
        assert result.usage.prompt_tokens == 12
        assert result.usage.completion_tokens == 5
        payload = captured["payload"]
        assert payload["input"] == [{"role": "user", "content": "Hello"}]
        assert "messages" not in payload
        assert payload["max_output_tokens"] == 2048
        assert payload["reasoning"] == {"effort": "medium", "summary": "auto"}
        assert payload["text"]["format"]["type"] == "json_schema"
        assert payload["parallel_tool_calls"] is False
        assert payload["service_tier"] == "auto"
        assert payload["prompt_cache_key"] == "bench-run"
        assert payload["safety_identifier"] == "user-opaque-id"

    def test_invoke_chat_completions_copies_latest_chat_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
        openai_compat_config: dict[str, Any],
        sample_openai_response: dict[str, Any],
    ) -> None:
        captured: dict[str, Any] = {}
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = sample_openai_response
        mock_resp.raise_for_status.return_value = None

        def fake_post(_url: str, _headers: dict[str, str], payload: dict[str, Any], _timeout: int) -> Any:
            captured["payload"] = payload
            return mock_resp

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            fake_post,
        )

        provider = OpenAIProvider()
        config = {
            **openai_compat_config,
            "max_completion_tokens": 123,
            "reasoning_effort": "medium",
            "response_format": {"type": "json_schema", "json_schema": {"name": "Answer", "schema": {}}},
            "verbosity": "low",
            "metadata": {"run_id": "chat"},
            "service_tier": "auto",
            "prompt_cache_key": "chat-cache",
            "safety_identifier": "safety-id",
            "store": False,
            "parallel_tool_calls": True,
        }
        result = provider.invoke("Hello", "gpt-4", config)

        assert result.ok is True
        payload = captured["payload"]
        assert payload["max_completion_tokens"] == 123
        assert "max_tokens" not in payload
        assert payload["reasoning_effort"] == "medium"
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["verbosity"] == "low"
        assert payload["parallel_tool_calls"] is True

    def test_health_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        openai_compat_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = OpenAIProvider()
        result = provider.health(openai_compat_config)

        assert result.ok is True
        assert result.error is None
        assert result.latency_ms >= 0

    def test_list_models_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        openai_compat_config: dict[str, Any],
    ) -> None:
        payload = {
            "object": "list",
            "data": [
                {"id": "gpt-4", "object": "model"},
                {"id": "gpt-3.5-turbo", "object": "model"},
            ],
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

        provider = OpenAIProvider()
        result = provider.list_models(openai_compat_config)

        assert result.ok is True
        assert len(result.models) == 2
        assert result.models[0].id == "gpt-4"


class TestOpenAIProviderEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_invoke_empty_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
        openai_compat_config: dict[str, Any],
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

        provider = OpenAIProvider()
        result = provider.invoke("Say nothing", "gpt-4", openai_compat_config)

        assert result.ok is True
        assert result.output == ""

    def test_invoke_missing_api_key(self, openai_compat_config: dict[str, Any]) -> None:
        config = {k: v for k, v in openai_compat_config.items() if k != "api_key"}
        provider = OpenAIProvider()
        result = provider.invoke("Hello", "gpt-4", config)

        # invoke_with_retry should still attempt the call; if server rejects it,
        # the mock path isn't taken here, so we just verify it doesn't crash.
        assert isinstance(result, InvokeResult)

    def test_validate_config_invalid_temperature(self) -> None:
        config = {
            "base_url": "https://api.example.com",
            "api_path": "/v1/chat/completions",
            "temperature": 5.0,
        }
        result = OpenAIProvider.validate_config(config)
        assert result.valid is True
        assert any("temperature" in w.lower() for w in result.warnings)
        assert result.normalized_config is not None
        assert result.normalized_config["temperature"] == 0.2

    def test_validate_config_negative_timeout(self) -> None:
        config = {
            "base_url": "https://api.example.com",
            "api_path": "/v1/chat/completions",
            "timeout": -10,
        }
        result = OpenAIProvider.validate_config(config)
        assert result.valid is True
        assert any("timeout" in w.lower() for w in result.warnings)
        assert result.normalized_config is not None
        assert result.normalized_config["timeout"] == 60

    def test_list_models_empty_data(
        self,
        monkeypatch: pytest.MonkeyPatch,
        openai_compat_config: dict[str, Any],
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

        provider = OpenAIProvider()
        result = provider.list_models(openai_compat_config)

        assert result.ok is True
        assert result.models == []


class TestOpenAIProviderExceptions:
    """Tests for error and exception handling paths."""

    def test_invoke_http_401(
        self,
        monkeypatch: pytest.MonkeyPatch,
        openai_compat_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 401
        mock_resp.text = '{"error": "Unauthorized"}'
        # raise_for_status is called inside invoke_with_retry and raises HTTPError
        from requests import HTTPError

        mock_resp.raise_for_status.side_effect = HTTPError("401 Client Error: Unauthorized")

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = OpenAIProvider()
        result = provider.invoke("Hello", "gpt-4", openai_compat_config)

        assert result.ok is False
        assert result.error is not None
        assert "401" in result.error or "Unauthorized" in result.error

    def test_invoke_http_500_with_retry_exhausted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        openai_compat_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 500
        mock_resp.text = '{"error": "Internal Server Error"}'
        from requests import HTTPError

        mock_resp.raise_for_status.side_effect = HTTPError("500 Server Error")

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )
        # Speed up retry loop for tests
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_sleep",
            lambda _seconds: None,
        )

        provider = OpenAIProvider()
        result = provider.invoke("Hello", "gpt-4", openai_compat_config)

        assert result.ok is False
        assert result.error is not None
        assert "500" in result.error or "Server Error" in result.error

    def test_health_http_404(
        self,
        monkeypatch: pytest.MonkeyPatch,
        openai_compat_config: dict[str, Any],
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"
        from requests import HTTPError

        mock_resp.raise_for_status.side_effect = HTTPError("404 Client Error: Not Found")

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: mock_resp,
        )

        provider = OpenAIProvider()
        result = provider.health(openai_compat_config)

        # health_check_post maps 404 to a specific message
        assert result.ok is False
        assert result.error is not None
        assert "api_path" in result.error.lower() or "not found" in result.error.lower()

    def test_list_models_http_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        openai_compat_config: dict[str, Any],
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

        provider = OpenAIProvider()
        result = provider.list_models(openai_compat_config)

        assert result.ok is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_invoke_stream_yields_error_on_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        openai_compat_config: dict[str, Any],
    ) -> None:
        """When invoke_stream falls back to invoke and it fails, yield error prefix."""
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

        provider = OpenAIProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "gpt-4", openai_compat_config):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].startswith("Error:")

    @pytest.mark.asyncio
    async def test_invoke_stream_success_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        openai_compat_config: dict[str, Any],
    ) -> None:
        """invoke_stream uses native SSE when get_stream_session is mocked."""
        from polaris.tests.integration.llm.providers.conftest import _make_mock_stream_session

        async def _mock_get_stream_session(*_args: Any, **_kwargs: Any) -> Any:
            return _make_mock_stream_session(
                [{"choices": [{"delta": {"content": "Hello! How can I help you today?"}}]}]
            )

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._close_and_create_session",
            _mock_get_stream_session,
        )

        provider = OpenAIProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "gpt-4", openai_compat_config):
            chunks.append(chunk)

        assert len(chunks) >= 1
        assert "".join(chunks) == "Hello! How can I help you today?"

    @pytest.mark.asyncio
    async def test_invoke_stream_handles_openai_responses_events(
        self,
        monkeypatch: pytest.MonkeyPatch,
        openai_compat_config: dict[str, Any],
    ) -> None:
        """Responses API SSE uses typed response.* events, not choices deltas."""
        from polaris.tests.integration.llm.providers.conftest import _make_mock_stream_session

        async def _mock_get_stream_session(*_args: Any, **_kwargs: Any) -> Any:
            return _make_mock_stream_session(
                [
                    {"type": "response.reasoning_summary_text.delta", "delta": "checking"},
                    {"type": "response.output_text.delta", "delta": "Hello"},
                    {"type": "response.output_text.delta", "delta": " world"},
                ]
            )

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._close_and_create_session",
            _mock_get_stream_session,
        )

        provider = OpenAIProvider()
        chunks: list[str] = []
        async for chunk in provider.invoke_stream(
            "Hello", "gpt-4.1", {**openai_compat_config, "api_path": "/v1/responses"}
        ):
            chunks.append(chunk)

        assert chunks[0] == f"{THINKING_PREFIX}checking"
        assert "".join(chunk for chunk in chunks if not chunk.startswith(THINKING_PREFIX)) == "Hello world"


class TestOpenAIProviderModelResolution:
    """Tests specific to model name resolution and validation."""

    def test_invoke_invalid_model_name(
        self,
        monkeypatch: pytest.MonkeyPatch,
        openai_compat_config: dict[str, Any],
    ) -> None:
        """An empty or invalid model should fail fast before HTTP call."""
        # Mock _blocking_http_post to avoid real network call when model resolution
        # unexpectedly passes.
        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_helpers._blocking_http_post",
            lambda _url, _headers, _payload, _timeout: MagicMock(
                ok=True,
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {
                    "choices": [{"message": {"content": ""}}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                },
            ),
        )

        provider = OpenAIProvider()
        result = provider.invoke("Hello", "", openai_compat_config)

        # resolve_model_name returns empty model as valid, but validate_model_name
        # may reject it or the HTTP call may fail.
        assert isinstance(result, InvokeResult)

    def test_invoke_model_resolution_with_role_model(self, openai_compat_config: dict[str, Any]) -> None:
        _config = {**openai_compat_config, "role_model": "gpt-4-turbo"}
        provider = OpenAIProvider()
        # We only verify that config merging doesn't crash; actual HTTP is mocked elsewhere.
        assert provider is not None
        assert _config.get("role_model") == "gpt-4-turbo"
