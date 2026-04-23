"""Tests for MiniMax provider streaming functionality"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.infrastructure.llm.providers import minimax_provider
from polaris.infrastructure.llm.providers.minimax_provider import MiniMaxProvider


def _build_mock_client_session(mock_response: AsyncMock) -> AsyncMock:
    """Build a ClientSession mock whose post(...) works with async context manager usage."""
    post_ctx = AsyncMock()
    post_ctx.__aenter__.return_value = mock_response
    post_ctx.__aexit__.return_value = None

    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    session.post = MagicMock(return_value=post_ctx)
    return session


class TestMiniMaxStreaming:
    """Test suite for MiniMax streaming implementation"""

    def test_invoke_stream_is_override(self):
        """Test that MiniMax actually overrides invoke_stream (not using BaseProvider default)"""
        is_override = 'invoke_stream' in MiniMaxProvider.__dict__
        assert is_override, "MiniMax must override invoke_stream for true streaming"

    def test_provider_info_declares_streaming(self):
        """Test that provider info declares streaming support"""
        info = MiniMaxProvider.get_provider_info()
        assert "streaming" in info.supported_features

    def test_debug_headers_are_redacted(self):
        masked = minimax_provider._redact_headers(
            {"Authorization": "Bearer secret-token", "Accept": "application/json"}
        )
        assert masked["Authorization"] == "Bearer ***REDACTED***"
        assert masked["Accept"] == "application/json"

    def test_debug_flag_defaults_to_off(self, monkeypatch):
        monkeypatch.delenv("KERNELONE_MINIMAX_DEBUG", raising=False)
        assert minimax_provider._debug_enabled({}) is False

    def test_debug_flag_can_be_enabled_by_env(self, monkeypatch):
        monkeypatch.setenv("KERNELONE_MINIMAX_DEBUG", "1")
        assert minimax_provider._debug_enabled({}) is True

    @pytest.mark.asyncio
    async def test_invoke_stream_yields_tokens(self):
        """Test that invoke_stream yields tokens from SSE response"""
        provider = MiniMaxProvider()

        # Mock SSE response data
        mock_chunks = [
            b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n',
            b'data: {"choices": [{"delta": {"content": " world"}}]}\n\n',
            b'data: [DONE]\n\n',
        ]

        # Create mock response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {'Content-Type': 'text/event-stream'}
        mock_response.content = AsyncMock()
        mock_response.content.__aiter__.return_value = mock_chunks

        # Create mock session
        mock_session = _build_mock_client_session(mock_response)

        config = {
            "api_key": "test-key",
            "base_url": "https://api.test.com",
            "timeout": 30,
        }

        with patch('aiohttp.ClientSession', return_value=mock_session):
            tokens = []
            async for token in provider.invoke_stream("Hi", "MiniMax-M2.1", config):
                tokens.append(token)

            assert len(tokens) == 2
            assert tokens[0] == "Hello"
            assert tokens[1] == " world"

    @pytest.mark.asyncio
    async def test_invoke_stream_handles_json_response(self):
        """Test that invoke_stream handles non-streaming JSON response"""
        provider = MiniMaxProvider()

        # Mock JSON response
        mock_response_data = {
            "choices": [{"message": {"content": "Hello world this is a test"}}]
        }

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {'Content-Type': 'application/json'}
        mock_response.json = AsyncMock(return_value=mock_response_data)

        mock_session = _build_mock_client_session(mock_response)

        config = {
            "api_key": "test-key",
            "base_url": "https://api.test.com",
        }

        with patch('aiohttp.ClientSession', return_value=mock_session):
            tokens = []
            async for token in provider.invoke_stream("Hi", "MiniMax-M2.1", config):
                tokens.append(token)

            # Should yield word by word for JSON response
            assert len(tokens) > 0
            full_response = ''.join(tokens)
            assert "Hello world" in full_response

    @pytest.mark.asyncio
    async def test_invoke_stream_handles_error(self):
        """Test that invoke_stream handles HTTP errors"""
        provider = MiniMaxProvider()

        mock_response = AsyncMock()
        mock_response.status = 401
        mock_response.text = AsyncMock(return_value="Unauthorized")

        mock_session = _build_mock_client_session(mock_response)

        config = {
            "api_key": "bad-key",
            "base_url": "https://api.test.com",
        }

        with patch('aiohttp.ClientSession', return_value=mock_session):
            tokens = []
            async for token in provider.invoke_stream("Hi", "MiniMax-M2.1", config):
                tokens.append(token)

            assert len(tokens) == 1
            assert tokens[0].startswith("Error:")

    @pytest.mark.asyncio
    async def test_invoke_stream_requires_api_key(self):
        """Test that invoke_stream requires API key"""
        provider = MiniMaxProvider()
        config = {}  # No API key

        tokens = []
        async for token in provider.invoke_stream("Hi", "MiniMax-M2.1", config):
            tokens.append(token)

        assert len(tokens) == 1
        assert "API key is required" in tokens[0]


class TestStreamingDetection:
    """Test suite for streaming detection logic"""

    def test_detection_logic(self):
        """Test the new streaming detection logic"""
        provider = MiniMaxProvider()

        # The new detection logic
        has_attr = hasattr(provider, 'invoke_stream')
        is_override = 'invoke_stream' in provider.__class__.__dict__
        supports_true = is_override  # New logic

        assert has_attr is True  # Always true due to BaseProvider
        assert is_override is True  # MiniMax now overrides
        assert supports_true is True  # Should use true streaming


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
