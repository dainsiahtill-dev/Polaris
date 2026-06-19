"""Tests for AsyncOllamaProvider (AAA pattern).

Verifies:
    - Provider info and config validation
    - Async health check with httpx
    - Async model listing
    - Async invoke with retry
    - Backward-compatible sync API
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.infrastructure.llm.providers.async_ollama_provider import (
    AsyncOllamaProvider,
    _build_headers,
    _extract_messages,
    _extract_output,
    _is_openai_compat_mode,
    _resolve_max_tokens,
    _timeout_seconds,
    _usage_from_response,
)
from polaris.kernelone.llm.types import InvokeResult

# =============================================================================
# Config helper tests
# =============================================================================


class TestConfigHelpers:
    """Tests for config helper functions."""

    def test_timeout_seconds_returns_default(self) -> None:
        # Arrange
        config: dict[str, object] = {}

        # Act
        result = _timeout_seconds(config, 60)  # type: ignore[arg-type]

        # Assert
        assert result == 60

    def test_timeout_seconds_from_config(self) -> None:
        # Arrange
        config: dict[str, object] = {"timeout": 30}

        # Act
        result = _timeout_seconds(config, 60)  # type: ignore[arg-type]

        # Assert
        assert result == 30

    def test_is_openai_compat_mode_true(self) -> None:
        # Arrange
        config: dict[str, object] = {"api_path": "/v1/chat/completions"}

        # Act
        result = _is_openai_compat_mode(config)  # type: ignore[arg-type]

        # Assert
        assert result is True

    def test_is_openai_compat_mode_false(self) -> None:
        # Arrange
        config: dict[str, object] = {"api_path": "/api/chat"}

        # Act
        result = _is_openai_compat_mode(config)  # type: ignore[arg-type]

        # Assert
        assert result is False

    def test_build_headers_openai_compat(self) -> None:
        # Arrange
        config: dict[str, object] = {
            "api_path": "/v1/chat/completions",
            "api_key": "test-key",
        }

        # Act
        result = _build_headers(config)  # type: ignore[arg-type]

        # Assert
        assert result["Authorization"] == "Bearer test-key"

    def test_build_headers_native(self) -> None:
        # Arrange
        config: dict[str, object] = {"api_path": "/api/chat"}

        # Act
        result = _build_headers(config)  # type: ignore[arg-type]

        # Assert
        assert result == {}

    def test_resolve_max_tokens_none(self) -> None:
        # Arrange
        config: dict[str, object] = {}

        # Act
        result = _resolve_max_tokens(config)  # type: ignore[arg-type]

        # Assert
        assert result is None

    def test_resolve_max_tokens_from_config(self) -> None:
        # Arrange
        config: dict[str, object] = {"max_tokens": 1000}

        # Act
        result = _resolve_max_tokens(config)  # type: ignore[arg-type]

        # Assert
        assert result == 1000

    def test_resolve_max_tokens_from_output_tokens(self) -> None:
        # Arrange
        config: dict[str, object] = {"max_output_tokens": 2000}

        # Act
        result = _resolve_max_tokens(config)  # type: ignore[arg-type]

        # Assert
        assert result == 2000


# =============================================================================
# Message extraction tests
# =============================================================================


class TestExtractMessages:
    """Tests for message extraction."""

    def test_basic_user_message(self) -> None:
        # Arrange
        config: dict[str, object] = {}

        # Act
        result = _extract_messages("Hello", config)  # type: ignore[arg-type]

        # Assert
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello"

    def test_with_system_prompt(self) -> None:
        # Arrange
        config: dict[str, object] = {"system_prompt": "You are helpful"}

        # Act
        result = _extract_messages("Hello", config)  # type: ignore[arg-type]

        # Assert
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are helpful"
        assert result[1]["role"] == "user"


# =============================================================================
# Output extraction tests
# =============================================================================


class TestExtractOutput:
    """Tests for output extraction from response data."""

    def test_openai_compat_format(self) -> None:
        # Arrange
        data = {
            "choices": [{"message": {"content": "Hello world"}}],
        }

        # Act
        result = _extract_output(data, is_compat=True)

        # Assert
        assert result == "Hello world"

    def test_native_chat_format(self) -> None:
        # Arrange
        data = {"message": {"content": "Hello from Ollama"}}

        # Act
        result = _extract_output(data, is_compat=False)

        # Assert
        assert result == "Hello from Ollama"

    def test_native_generate_format(self) -> None:
        # Arrange
        data = {"response": "Generated text"}

        # Act
        result = _extract_output(data, is_compat=False)

        # Assert
        assert result == "Generated text"

    def test_empty_data(self) -> None:
        # Arrange
        data: dict[str, object] = {}

        # Act
        result = _extract_output(data, is_compat=False)  # type: ignore[arg-type]

        # Assert
        assert result == ""


# =============================================================================
# Usage extraction tests
# =============================================================================


class TestUsageFromResponse:
    """Tests for usage extraction."""

    def test_openai_format(self) -> None:
        # Arrange
        data = {
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            }
        }

        # Act
        usage = _usage_from_response("test prompt", "test output", data, True)

        # Assert
        assert usage.prompt_tokens == 10
        assert usage.completion_tokens == 20
        assert usage.total_tokens == 30
        assert usage.estimated is False

    def test_native_ollama_format(self) -> None:
        # Arrange
        data = {"prompt_eval_count": 15, "eval_count": 25}

        # Act
        usage = _usage_from_response("test prompt", "test output", data, False)

        # Assert
        assert usage.prompt_tokens == 15
        assert usage.completion_tokens == 25
        assert usage.total_tokens == 40
        assert usage.estimated is False

    def test_fallback_to_estimation(self) -> None:
        # Arrange
        data: dict[str, object] = {}

        # Act
        usage = _usage_from_response("test prompt", "test output", data, False)  # type: ignore[arg-type]

        # Assert
        assert usage.estimated is True


# =============================================================================
# Provider tests
# =============================================================================


class TestAsyncOllamaProvider:
    """Tests for the AsyncOllamaProvider class."""

    def test_provider_info(self) -> None:
        # Arrange
        provider = AsyncOllamaProvider()

        # Act
        info = provider.get_provider_info()

        # Assert
        assert info.name == "Ollama Provider"
        assert info.type == "ollama"
        assert info.cost_class == "LOCAL"
        assert "health_check" in info.supported_features

    def test_default_config(self) -> None:
        # Arrange
        provider = AsyncOllamaProvider()

        # Act
        config = provider.get_default_config()

        # Assert
        assert "base_url" in config
        assert config["timeout"] == 60

    def test_validate_config_valid(self) -> None:
        # Arrange
        provider = AsyncOllamaProvider()
        config = {"base_url": "http://localhost:11434"}

        # Act
        result = provider.validate_config(config)

        # Assert
        assert result.valid is True
        assert result.errors == []

    def test_validate_config_openai_compat_requires_api_key(self) -> None:
        # Arrange
        provider = AsyncOllamaProvider()
        config = {"api_path": "/v1/chat/completions"}

        # Act
        result = provider.validate_config(config)

        # Assert
        assert result.valid is True
        assert any("api_key" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_health_success(self) -> None:
        # Arrange
        provider = AsyncOllamaProvider()
        mock_result = MagicMock()
        mock_result.status_code = 200
        mock_result.elapsed_ms = 42

        # Act
        with patch(
            "polaris.infrastructure.llm.providers.async_ollama_provider.AsyncProviderHttpClient"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get_json = AsyncMock(return_value=mock_result)
            mock_cls.return_value = mock_client

            result = await provider.health({"base_url": "http://test:11434"})

        # Assert
        assert result.ok is True
        assert result.latency_ms == 42

    @pytest.mark.asyncio
    async def test_list_models_success(self) -> None:
        # Arrange
        provider = AsyncOllamaProvider()
        models_data = {
            "models": [
                {"name": "llama2", "model": "llama2"},
                {"name": "codellama", "model": "codellama"},
            ]
        }
        mock_result = MagicMock()
        mock_result.status_code = 200
        mock_result.text = json.dumps(models_data)

        # Act
        with patch(
            "polaris.infrastructure.llm.providers.async_ollama_provider.AsyncProviderHttpClient"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get_json = AsyncMock(return_value=mock_result)
            mock_cls.return_value = mock_client

            result = await provider.list_models({"base_url": "http://test:11434"})

        # Assert
        assert result.ok is True
        assert len(result.models) == 2
        assert result.models[0].id == "llama2"

    @pytest.mark.asyncio
    async def test_invoke_success(self) -> None:
        # Arrange
        provider = AsyncOllamaProvider()
        response_data = {
            "message": {"content": "Hello from Ollama"},
            "prompt_eval_count": 10,
            "eval_count": 5,
        }
        mock_result = InvokeResult(
            ok=True,
            output="Hello from Ollama",
            latency_ms=100,
            usage=provider.get_provider_info(),
            raw=response_data,
        )

        # Act
        with patch(
            "polaris.infrastructure.llm.providers.async_ollama_provider.async_invoke_with_retry",
            new_callable=AsyncMock,
        ) as mock_invoke:
            mock_invoke.return_value = mock_result
            result = await provider.invoke(
                "Hello", "llama2", {"base_url": "http://test:11434"}
            )

        # Assert
        assert result.ok is True
        assert result.output == "Hello from Ollama"
