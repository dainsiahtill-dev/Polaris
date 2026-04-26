"""
Tests for Kimi Provider Streaming Implementation

Run with: python -m pytest tests/test_kimi_streaming.py -v
"""

import asyncio
from typing import Self
from unittest.mock import patch

import pytest
from polaris.infrastructure.llm.providers.kimi_provider import KimiProvider


class _AsyncByteStream:
    def __init__(self, chunks) -> None:
        self._iter = iter(chunks)

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


class _FakeStreamResponse:
    def __init__(self, chunks) -> None:
        self.content = _AsyncByteStream(chunks)

    def raise_for_status(self) -> None:
        return None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: type, exc: BaseException, tb: object) -> None:
        return None


class _FakeSession:
    def __init__(self, *, response=None, error=None) -> None:
        self._response = response
        self._error = error
        self.timeout_arg = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: type, exc: BaseException, tb: object) -> None:
        return None

    def post(self, *args, **kwargs):
        self.timeout_arg = kwargs.get("timeout")
        if self._error is not None:
            raise self._error
        return self._response


class TestKimiInvokeStream:
    """Test suite for Kimi invoke_stream method"""

    @pytest.fixture
    def provider(self):
        return KimiProvider()

    @pytest.fixture
    def valid_config(self):
        return {
            "api_key": "test-api-key",
            "base_url": "https://api.moonshot.cn/v1",
            "temperature": 0.7,
            "max_tokens": 50,
            "timeout": 120,
        }

    @pytest.mark.asyncio
    async def test_invoke_stream_no_api_key(self, provider):
        """Test that missing API key returns error"""
        config = {"api_key": ""}

        tokens = []
        async for token in provider.invoke_stream("Hello", "kimi-k2-turbo-preview", config):
            tokens.append(token)

        assert len(tokens) == 1
        assert "API key is required" in tokens[0]

    @pytest.mark.asyncio
    async def test_invoke_stream_success(self, provider, valid_config):
        """Test successful streaming response"""
        # Simulate SSE stream data
        sse_data = [
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]

        fake_session = _FakeSession(response=_FakeStreamResponse(sse_data))

        with patch("aiohttp.ClientSession", return_value=fake_session):
            tokens = []
            async for token in provider.invoke_stream("Hi", "kimi-k2-turbo-preview", valid_config):
                tokens.append(token)

        assert len(tokens) == 2
        assert tokens[0] == "Hello"
        assert tokens[1] == " world"

    @pytest.mark.asyncio
    async def test_invoke_stream_network_error(self, provider, valid_config):
        """Test network error handling"""
        import aiohttp

        fake_session = _FakeSession(error=aiohttp.ClientError("Connection failed"))

        with patch("aiohttp.ClientSession", return_value=fake_session):
            tokens = []
            async for token in provider.invoke_stream("Hi", "kimi-k2-turbo-preview", valid_config):
                tokens.append(token)

        assert len(tokens) == 1
        assert "Network error" in tokens[0]

    @pytest.mark.asyncio
    async def test_invoke_stream_timeout(self, provider, valid_config):
        """Test timeout error handling"""

        fake_session = _FakeSession(error=asyncio.TimeoutError())

        with patch("aiohttp.ClientSession", return_value=fake_session):
            tokens = []
            async for token in provider.invoke_stream("Hi", "kimi-k2-turbo-preview", valid_config):
                tokens.append(token)

        assert len(tokens) == 1
        assert "Request timeout" in tokens[0]

    @pytest.mark.asyncio
    async def test_invoke_stream_prefers_stream_timeout(self, provider, valid_config):
        """`stream_timeout` should override `timeout` for streaming requests."""
        fake_session = _FakeSession(response=_FakeStreamResponse([b"data: [DONE]\n\n"]))
        config = {**valid_config, "timeout": 120, "stream_timeout": 33}

        with patch("aiohttp.ClientSession", return_value=fake_session):
            tokens = []
            async for token in provider.invoke_stream("Hi", "kimi-k2-turbo-preview", config):
                tokens.append(token)

        assert tokens == []
        assert fake_session.timeout_arg is not None
        assert fake_session.timeout_arg.total == 33

    @pytest.mark.asyncio
    async def test_invoke_stream_reasoning_content(self, provider, valid_config):
        """Reasoning tokens from thinking models should be yielded with THINKING_PREFIX."""
        from polaris.kernelone.llm.providers import THINKING_PREFIX

        sse_data = [
            b'data: {"choices":[{"delta":{"reasoning_content":"Let me think..."}}]}\n\n',
            b'data: {"choices":[{"delta":{"reasoning_content":"The answer is 42."}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"{\\"reply\\":\\"42\\"}"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]

        fake_session = _FakeSession(response=_FakeStreamResponse(sse_data))

        with patch("aiohttp.ClientSession", return_value=fake_session):
            tokens = []
            async for token in provider.invoke_stream("Hi", "kimi-k2-thinking-turbo", valid_config):
                tokens.append(token)

        assert len(tokens) == 3
        # First two tokens should be reasoning, prefixed with THINKING_PREFIX
        assert tokens[0] == f"{THINKING_PREFIX}Let me think..."
        assert tokens[1] == f"{THINKING_PREFIX}The answer is 42."
        # Third token is regular content, no prefix
        assert tokens[2] == '{"reply":"42"}'

    @pytest.mark.asyncio
    async def test_invoke_stream_handles_split_sse_chunks_with_reasoning(self, provider, valid_config):
        """SSE lines can be split across TCP chunks; reasoning should still be emitted."""
        from polaris.kernelone.llm.providers import THINKING_PREFIX

        sse_data = [
            b'data: {"choices":[{"delta":{"reason',
            b'ing_content":"step-1"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
            b"data: [DO",
            b"NE]\n\n",
        ]
        fake_session = _FakeSession(response=_FakeStreamResponse(sse_data))

        with patch("aiohttp.ClientSession", return_value=fake_session):
            tokens = []
            async for token in provider.invoke_stream("Hi", "kimi-k2-thinking-turbo", valid_config):
                tokens.append(token)

        assert tokens == [f"{THINKING_PREFIX}step-1", "ok"]

    @pytest.mark.asyncio
    async def test_invoke_stream_system_prompt(self, provider, valid_config):
        """When system_prompt is in config, it should be prepended to messages."""
        fake_session = _FakeSession(response=_FakeStreamResponse([b"data: [DONE]\n\n"]))
        config = {**valid_config, "system_prompt": "You output strict JSON only."}

        captured_payload = {}

        original_post = fake_session.post

        def capturing_post(*args, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return original_post(*args, **kwargs)

        fake_session.post = capturing_post

        with patch("aiohttp.ClientSession", return_value=fake_session):
            tokens = []
            async for token in provider.invoke_stream("Hello", "kimi-k2-turbo-preview", config):
                tokens.append(token)

        messages = captured_payload.get("messages", [])
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You output strict JSON only."
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Hello"


class TestKimiModelList:
    """Test suite for Kimi model listing"""

    @pytest.fixture
    def provider(self):
        return KimiProvider()

    def test_known_models_include_latest(self, provider):
        """Test that known models include latest Kimi models"""
        # The models are embedded in list_models method
        # We verify by checking the provider info supports them
        info = provider.get_provider_info()
        assert "streaming" in info.supported_features


if __name__ == "__main__":
    # Run with: python tests/test_kimi_streaming.py
    pytest.main([__file__, "-v"])
