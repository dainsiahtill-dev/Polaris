"""Tests for AsyncGeminiAPIProvider (AAA pattern).

Verifies:
    - Provider info and config validation
    - Async health check with httpx
    - Async model listing
    - Async invoke with retry
    - Thinking extraction
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.infrastructure.llm.providers.async_gemini_api_provider import (
    AsyncGeminiAPIProvider,
    _resolve_max_tokens,
    _timeout_seconds,
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

    def test_resolve_max_tokens_from_config(self) -> None:
        # Arrange
        config: dict[str, object] = {"max_tokens": 4096}

        # Act
        result = _resolve_max_tokens(config, 8192)  # type: ignore[arg-type]

        # Assert
        assert result == 4096

    def test_resolve_max_tokens_default(self) -> None:
        # Arrange
        config: dict[str, object] = {}

        # Act
        result = _resolve_max_tokens(config, 8192)  # type: ignore[arg-type]

        # Assert
        assert result == 8192


# =============================================================================
# Provider tests
# =============================================================================


class TestAsyncGeminiAPIProvider:
    """Tests for the AsyncGeminiAPIProvider class."""

    def test_provider_info(self) -> None:
        # Arrange
        provider = AsyncGeminiAPIProvider()

        # Act
        info = provider.get_provider_info()

        # Assert
        assert info.name == "Gemini API Provider"
        assert info.type == "gemini_api"
        assert info.cost_class == "METERED"
        assert "thinking_extraction" in info.supported_features

    def test_default_config(self) -> None:
        # Arrange
        provider = AsyncGeminiAPIProvider()

        # Act
        config = provider.get_default_config()

        # Assert
        assert "base_url" in config
        assert config["timeout"] == 60
        assert "thinking_extraction" in config

    def test_validate_config_valid(self) -> None:
        # Arrange
        provider = AsyncGeminiAPIProvider()
        config = {
            "base_url": "https://generativelanguage.googleapis.com",
            "api_key": "test-key",
        }

        # Act
        result = provider.validate_config(config)

        # Assert
        assert result.valid is True
        assert result.errors == []

    def test_validate_config_missing_api_key(self) -> None:
        # Arrange
        provider = AsyncGeminiAPIProvider()
        config = {"base_url": "https://generativelanguage.googleapis.com"}

        # Act
        result = provider.validate_config(config)

        # Assert
        assert result.valid is False
        assert any("API key" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_health_missing_api_key(self) -> None:
        # Arrange
        provider = AsyncGeminiAPIProvider()

        # Act
        result = await provider.health({"base_url": "https://test.com"})

        # Assert
        assert result.ok is False
        assert "API key" in result.error

    @pytest.mark.asyncio
    async def test_health_success(self) -> None:
        # Arrange
        provider = AsyncGeminiAPIProvider()
        mock_result = MagicMock()
        mock_result.status_code = 200
        mock_result.elapsed_ms = 42

        # Act
        with patch(
            "polaris.infrastructure.llm.providers.async_gemini_api_provider.AsyncProviderHttpClient"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get_json = AsyncMock(return_value=mock_result)
            mock_cls.return_value = mock_client

            result = await provider.health(
                {
                    "base_url": "https://generativelanguage.googleapis.com",
                    "api_key": "test-key",
                }
            )

        # Assert
        assert result.ok is True
        assert result.latency_ms == 42

    @pytest.mark.asyncio
    async def test_list_models_missing_api_key(self) -> None:
        # Arrange
        provider = AsyncGeminiAPIProvider()

        # Act
        result = await provider.list_models({"base_url": "https://test.com"})

        # Assert
        assert result.ok is False
        assert "API key" in result.error

    @pytest.mark.asyncio
    async def test_list_models_success(self) -> None:
        # Arrange
        provider = AsyncGeminiAPIProvider()
        models_data = {
            "models": [
                {"name": "models/gemini-1.5-pro", "displayName": "Gemini 1.5 Pro"},
                {"name": "models/gemini-1.5-flash", "displayName": "Gemini 1.5 Flash"},
            ]
        }
        mock_result = MagicMock()
        mock_result.status_code = 200
        mock_result.text = json.dumps(models_data)

        # Act
        with patch(
            "polaris.infrastructure.llm.providers.async_gemini_api_provider.AsyncProviderHttpClient"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get_json = AsyncMock(return_value=mock_result)
            mock_cls.return_value = mock_client

            result = await provider.list_models(
                {
                    "base_url": "https://generativelanguage.googleapis.com",
                    "api_key": "test-key",
                }
            )

        # Assert
        assert result.ok is True
        assert len(result.models) == 2
        assert result.models[0].id == "gemini-1.5-pro"

    @pytest.mark.asyncio
    async def test_invoke_missing_api_key(self) -> None:
        # Arrange
        provider = AsyncGeminiAPIProvider()

        # Act
        result = await provider.invoke("Hello", "gemini-1.5-pro", {"base_url": "https://test.com"})

        # Assert
        assert result.ok is False
        assert "API key" in result.error

    @pytest.mark.asyncio
    async def test_invoke_success(self) -> None:
        # Arrange
        provider = AsyncGeminiAPIProvider()
        response_data = {
            "candidates": [{"content": {"parts": [{"text": "Hello from Gemini"}]}}],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "totalTokenCount": 15,
            },
        }
        mock_result = InvokeResult(
            ok=True,
            output="Hello from Gemini",
            latency_ms=100,
            usage=provider.get_provider_info(),
            raw=response_data,
        )

        # Act
        with patch(
            "polaris.infrastructure.llm.providers.async_gemini_api_provider.async_invoke_with_retry",
            new_callable=AsyncMock,
        ) as mock_invoke:
            mock_invoke.return_value = mock_result
            result = await provider.invoke(
                "Hello",
                "gemini-1.5-pro",
                {"base_url": "https://test.com", "api_key": "test-key"},
            )

        # Assert
        assert result.ok is True
        assert result.output == "Hello from Gemini"

    def test_extract_output(self) -> None:
        # Arrange
        data = {"candidates": [{"content": {"parts": [{"text": "Test output"}]}}]}

        # Act
        result = AsyncGeminiAPIProvider._extract_output(data)

        # Assert
        assert result == "Test output"

    def test_extract_output_empty(self) -> None:
        # Arrange
        data: dict[str, object] = {}

        # Act
        result = AsyncGeminiAPIProvider._extract_output(data)  # type: ignore[arg-type]

        # Assert
        assert result == ""

    def test_usage_from_response(self) -> None:
        # Arrange
        data = {
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 20,
                "totalTokenCount": 30,
            }
        }

        # Act
        usage = AsyncGeminiAPIProvider._usage_from_response("test", "output", data)

        # Assert
        assert usage.prompt_tokens == 10
        assert usage.completion_tokens == 20
        assert usage.total_tokens == 30
        assert usage.estimated is False

    def test_thinking_extraction(self) -> None:
        # Arrange
        response = {
            "output": "<thinking>Let me think about this carefully. I need to analyze the problem step by step because it's complex.</thinking>\n\nThe answer is 42.",
            "config": {
                "thinking_extraction": {
                    "enabled": True,
                    "patterns": [r"<thinking>(.*?)</thinking>"],
                    "confidence_threshold": 0.3,
                }
            },
        }

        # Act
        result = AsyncGeminiAPIProvider.extract_thinking_support(response)

        # Assert
        assert result.supports_thinking is True
        assert result.confidence > 0.3
