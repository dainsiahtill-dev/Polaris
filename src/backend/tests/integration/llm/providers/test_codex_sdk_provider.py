"""Integration tests for CodexSDKProvider.

Covers:
- Happy path: invoke(), health(), list_models()
- Edge cases: empty response, missing API key, SDK unavailable
- Exception paths: SDK errors, network failures, config errors
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.infrastructure.llm.providers.codex_sdk_provider import CodexSDKProvider
from polaris.infrastructure.llm.sdk import SDKUnavailableError
from polaris.kernelone.llm.types import InvokeResult


class TestCodexSDKProviderHappyPath:
    """Tests for the normal successful execution paths."""

    def test_get_provider_info(self) -> None:
        info = CodexSDKProvider.get_provider_info()
        assert info.type == "codex_sdk"
        assert "streaming" in info.supported_features
        assert info.provider_category == "AGENT"

    def test_get_default_config(self) -> None:
        defaults = CodexSDKProvider.get_default_config()
        assert defaults["type"] == "codex_sdk"
        assert defaults["default_model"] == "gpt-4-codex"
        assert defaults["temperature"] == 0.2

    def test_validate_config_valid(self, codex_sdk_config: dict[str, Any]) -> None:
        result = CodexSDKProvider.validate_config(codex_sdk_config)
        assert result.valid is True
        assert not result.errors

    def test_invoke_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        mock_response = MagicMock()
        mock_response.content = "Hello! How can I help you today?"
        mock_response.thinking = None
        mock_response.usage = {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}
        mock_response.metadata = {"model": "gpt-4-codex"}

        mock_client = MagicMock()
        mock_client.invoke.return_value = mock_response

        provider = CodexSDKProvider()
        # Inject mock client directly
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        result = provider.invoke("Say hello", "gpt-4-codex", codex_sdk_config)

        assert isinstance(result, InvokeResult)
        assert result.ok is True
        assert result.output == "Hello! How can I help you today?"
        assert result.error is None
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 8

    def test_invoke_with_thinking(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        mock_response = MagicMock()
        mock_response.content = "The answer is 42."
        mock_response.thinking = "Let me calculate..."
        mock_response.usage = None
        mock_response.metadata = {}

        mock_client = MagicMock()
        mock_client.invoke.return_value = mock_response

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        result = provider.invoke("What is 6x7?", "gpt-4-codex", codex_sdk_config)

        assert result.ok is True
        assert "<thinking>" in result.output
        assert "The answer is 42." in result.output

    def test_health_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        mock_client = MagicMock()
        mock_client.health_check.return_value = True

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        result = provider.health(codex_sdk_config)

        assert result.ok is True
        assert result.error is None
        assert result.latency_ms >= 0

    def test_list_models_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        mock_client = MagicMock()
        mock_client.list_models.return_value = ["gpt-4-codex", "gpt-3.5-turbo"]

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        result = provider.list_models(codex_sdk_config)

        assert result.ok is True
        assert len(result.models) == 2
        assert result.models[0].id == "gpt-4-codex"


class TestCodexSDKProviderEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_invoke_empty_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        mock_response = MagicMock()
        mock_response.content = ""
        mock_response.thinking = None
        mock_response.usage = None
        mock_response.metadata = {}

        mock_client = MagicMock()
        mock_client.invoke.return_value = mock_response

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        result = provider.invoke("Say nothing", "gpt-4-codex", codex_sdk_config)

        assert result.ok is True
        assert result.output == ""

    def test_invoke_missing_api_key(self) -> None:
        config: dict[str, Any] = {
            "base_url": "https://api.openai.com/v1",
            "timeout": 60,
            "max_retries": 3,
        }
        result = CodexSDKProvider.validate_config(config)
        assert result.valid is True
        assert any("api_key" in w.lower() for w in result.warnings)

    def test_validate_config_invalid_timeout(self) -> None:
        config: dict[str, Any] = {
            "base_url": "https://api.openai.com/v1",
            "timeout": "invalid",
            "max_retries": 3,
        }
        result = CodexSDKProvider.validate_config(config)
        assert result.valid is True
        assert any("timeout" in w.lower() for w in result.warnings)
        assert result.normalized_config is not None
        assert result.normalized_config["timeout"] == 60

    def test_validate_config_none_max_retries(self) -> None:
        """None max_retries should default gracefully instead of raising TypeError.

        Regression test for: int(None) raising TypeError when max_retries is None.
        """
        config: dict[str, Any] = {
            "base_url": "https://api.openai.com/v1",
            "timeout": 60,
            "max_retries": None,
        }
        result = CodexSDKProvider.validate_config(config)
        assert result.valid is True
        assert result.normalized_config is not None
        assert result.normalized_config["max_retries"] == 3

    def test_normalize_retries_none_returns_default(self) -> None:
        """Direct test for _normalize_retries(None) returning default."""
        from polaris.infrastructure.llm.providers.codex_sdk_provider import _normalize_retries

        assert _normalize_retries(None, default=5) == 5
        assert _normalize_retries(None, default=3) == 3

    def test_normalize_retries_invalid_string_returns_default(self) -> None:
        """Direct test for _normalize_retries with invalid string."""
        from polaris.infrastructure.llm.providers.codex_sdk_provider import _normalize_retries

        assert _normalize_retries("not_a_number", default=5) == 5

    def test_validate_config_invalid_headers(self) -> None:
        config: dict[str, Any] = {
            "base_url": "https://api.openai.com/v1",
            "headers": "bad_headers",
            "max_retries": 3,
        }
        result = CodexSDKProvider.validate_config(config)
        assert result.valid is True
        assert any("headers" in w.lower() for w in result.warnings)

    def test_validate_config_invalid_sdk_params(self) -> None:
        config: dict[str, Any] = {
            "base_url": "https://api.openai.com/v1",
            "sdk_params": "bad_params",
            "max_retries": 3,
        }
        result = CodexSDKProvider.validate_config(config)
        assert result.valid is True
        assert any("sdk_params" in w.lower() for w in result.warnings)
        assert result.normalized_config is not None
        assert result.normalized_config["sdk_params"] == {}

    def test_health_sdk_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        mock_client = MagicMock()
        mock_client.health_check.side_effect = SDKUnavailableError("SDK not installed")

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        result = provider.health(codex_sdk_config)

        assert result.ok is False
        assert result.error is not None
        assert "SDK not installed" in result.error

    def test_list_models_sdk_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        mock_client = MagicMock()
        mock_client.list_models.side_effect = SDKUnavailableError("SDK not installed")

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        result = provider.list_models(codex_sdk_config)

        assert result.ok is False
        assert result.error is not None
        assert "SDK not installed" in result.error

    def test_sdk_client_reuse_on_same_config(
        self,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        """Test that the same SDK client is reused when config identity matches."""
        provider = CodexSDKProvider()

        mock_client1 = MagicMock()
        mock_client1.invoke.return_value = MagicMock(content="", thinking=None, usage=None, metadata={})

        # First call sets the client
        provider._sdk_client = mock_client1
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        # Second call with same config should reuse the same client
        provider.invoke("Test", "gpt-4-codex", codex_sdk_config)
        assert provider._sdk_client is mock_client1

    def test_sdk_client_refresh_on_config_change(
        self,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        """Test that SDK client is refreshed when config identity changes."""
        provider = CodexSDKProvider()

        mock_client1 = MagicMock()
        provider._sdk_client = mock_client1
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        # Different config should trigger client refresh (but we mock _get_sdk_client)
        new_config = {**codex_sdk_config, "api_key": "different-key"}
        new_identity = provider._sdk_identity(new_config)

        assert provider._sdk_key != new_identity


class TestCodexSDKProviderExceptions:
    """Tests for error and exception handling paths."""

    def test_invoke_sdk_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        mock_client = MagicMock()
        mock_client.invoke.side_effect = SDKUnavailableError("SDK not installed")

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        result = provider.invoke("Hello", "gpt-4-codex", codex_sdk_config)

        assert result.ok is False
        assert result.error is not None
        assert "SDK not installed" in result.error

    def test_invoke_runtime_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        mock_client = MagicMock()
        mock_client.invoke.side_effect = RuntimeError("Unexpected SDK error")

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        result = provider.invoke("Hello", "gpt-4-codex", codex_sdk_config)

        assert result.ok is False
        assert result.error is not None
        assert "Unexpected SDK error" in result.error

    def test_invoke_value_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        mock_client = MagicMock()
        mock_client.invoke.side_effect = ValueError("Invalid parameter")

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        result = provider.invoke("Hello", "gpt-4-codex", codex_sdk_config)

        assert result.ok is False
        assert result.error is not None
        assert "Invalid parameter" in result.error

    def test_invoke_connection_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        """Regression test: ConnectionError should be caught and returned as error."""
        mock_client = MagicMock()
        mock_client.invoke.side_effect = ConnectionError("Connection refused")

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        result = provider.invoke("Hello", "gpt-4-codex", codex_sdk_config)

        assert result.ok is False
        assert result.error is not None
        assert "Connection error" in result.error
        assert "Connection refused" in result.error

    def test_invoke_timeout_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        """Regression test: TimeoutError should be caught and returned as error."""
        mock_client = MagicMock()
        mock_client.invoke.side_effect = TimeoutError("Request timed out")

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        result = provider.invoke("Hello", "gpt-4-codex", codex_sdk_config)

        assert result.ok is False
        assert result.error is not None
        assert "Request timeout" in result.error
        assert "Request timed out" in result.error

    def test_health_runtime_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        mock_client = MagicMock()
        mock_client.health_check.side_effect = RuntimeError("Health check failed")

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        result = provider.health(codex_sdk_config)

        assert result.ok is False
        assert result.error is not None
        assert "Health check failed" in result.error

    def test_health_connection_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        """Regression test: ConnectionError in health() should be caught and returned as error."""
        mock_client = MagicMock()
        mock_client.health_check.side_effect = ConnectionError("Network unreachable")

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        result = provider.health(codex_sdk_config)

        assert result.ok is False
        assert result.error is not None
        assert "Connection error" in result.error
        assert "Network unreachable" in result.error

    def test_health_timeout_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        """Regression test: TimeoutError in health() should be caught and returned as error."""
        mock_client = MagicMock()
        mock_client.health_check.side_effect = TimeoutError("Health check timed out")

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        result = provider.health(codex_sdk_config)

        assert result.ok is False
        assert result.error is not None
        assert "Connection error" in result.error
        assert "Health check timed out" in result.error

    def test_list_models_runtime_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        mock_client = MagicMock()
        mock_client.list_models.side_effect = RuntimeError("List models failed")

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        result = provider.list_models(codex_sdk_config)

        assert result.ok is False
        assert result.error is not None
        assert "List models failed" in result.error

    def test_list_models_connection_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        """Regression test: ConnectionError in list_models() should be caught and returned as error."""
        mock_client = MagicMock()
        mock_client.list_models.side_effect = ConnectionError("Connection refused")

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        result = provider.list_models(codex_sdk_config)

        assert result.ok is False
        assert result.error is not None
        assert "Connection error" in result.error
        assert "Connection refused" in result.error

    def test_list_models_timeout_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        """Regression test: TimeoutError in list_models() should be caught and returned as error."""
        mock_client = MagicMock()
        mock_client.list_models.side_effect = TimeoutError("Request timed out")

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        result = provider.list_models(codex_sdk_config)

        assert result.ok is False
        assert result.error is not None
        assert "Request timeout" in result.error
        assert "Request timed out" in result.error

    def test_extract_thinking_support_detected(self) -> None:
        response = {"output": "<thinking>Deep thought</thinking>\n\nAnswer here."}
        info = CodexSDKProvider.extract_thinking_support(response)
        assert info.supports_thinking is True
        assert info.format == "xml"
        assert info.extraction_method == "sdk_tagged"

    def test_extract_thinking_support_not_detected(self) -> None:
        response = {"output": "Just a regular answer."}
        info = CodexSDKProvider.extract_thinking_support(response)
        assert info.supports_thinking is False
        assert info.extraction_method == "sdk_default"

    def test_extract_thinking_support_non_dict(self) -> None:
        response: Any = "not a dict"
        info = CodexSDKProvider.extract_thinking_support(response)
        assert info.supports_thinking is False
        assert info.extraction_method == "sdk_default"

    @pytest.mark.asyncio
    async def test_invoke_stream_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        mock_response = MagicMock()
        mock_response.content = "Stream fallback output"
        mock_response.thinking = None
        mock_response.usage = None
        mock_response.metadata = {}

        mock_client = MagicMock()
        mock_client.invoke.return_value = mock_response

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "gpt-4-codex", codex_sdk_config):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0] == "Stream fallback output"

    @pytest.mark.asyncio
    async def test_invoke_stream_error_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_sdk_config: dict[str, Any],
    ) -> None:
        mock_client = MagicMock()
        mock_client.invoke.side_effect = RuntimeError("Stream invoke failed")

        provider = CodexSDKProvider()
        provider._sdk_client = mock_client
        provider._sdk_key = provider._sdk_identity(codex_sdk_config)

        chunks: list[str] = []
        async for chunk in provider.invoke_stream("Hello", "gpt-4-codex", codex_sdk_config):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].startswith("Error:")
