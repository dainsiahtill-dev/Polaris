from __future__ import annotations

from unittest.mock import patch

import pytest
import polaris.infrastructure.llm.providers.openai_compat_provider as openai_provider_module
from polaris.kernelone.llm.providers import THINKING_PREFIX
from polaris.infrastructure.llm.providers.openai_compat_provider import OpenAICompatProvider


class _AsyncByteStream:
    def __init__(self, chunks):
        self._iter = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _FakeStreamResponse:
    def __init__(self, chunks, status: int = 200, body: str = ""):
        self.status = status
        self.content = _AsyncByteStream(chunks)
        self._body = body

    async def text(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakeSession:
    def __init__(self, *, response=None, error=None):
        self._response = response
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def post(self, *args, **kwargs):
        if self._error is not None:
            raise self._error
        return self._response


@pytest.fixture
def provider():
    return OpenAICompatProvider()


@pytest.fixture
def valid_config():
    return {
        "base_url": "https://api.example.com/v1",
        "api_path": "/v1/chat/completions",
        "api_key": "test-api-key",
        "timeout": 60,
        "temperature": 0.2,
    }


@pytest.mark.asyncio
async def test_openai_compat_stream_handles_split_sse_chunks(provider, valid_config):
    chunks = [
        b'data: {"choices":[{"delta":{"reason',
        b'ing_content":"step-1"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
        b'data: [DO',
        b'NE]\n\n',
    ]
    fake_session = _FakeSession(response=_FakeStreamResponse(chunks))

    async def _fake_get_stream_session(*args, **kwargs):
        return fake_session

    with patch.object(openai_provider_module, "get_stream_session", _fake_get_stream_session):
        tokens = []
        async for token in provider.invoke_stream("hello", "gpt-4.1-mini", valid_config):
            tokens.append(token)

    assert tokens == [f"{THINKING_PREFIX}step-1", "ok"]


@pytest.mark.asyncio
async def test_openai_compat_stream_parses_content_parts(provider, valid_config):
    chunks = [
        (
            b'data: {"choices":[{"delta":{"content":[{"type":"reasoning","text":"R1"},'
            b'{"type":"output_text","text":"A1"}]}}]}\n\n'
        ),
        b"data: [DONE]\n\n",
    ]
    fake_session = _FakeSession(response=_FakeStreamResponse(chunks))

    async def _fake_get_stream_session(*args, **kwargs):
        return fake_session

    with patch.object(openai_provider_module, "get_stream_session", _fake_get_stream_session):
        tokens = []
        async for token in provider.invoke_stream("hello", "gpt-4.1-mini", valid_config):
            tokens.append(token)

    assert tokens == [f"{THINKING_PREFIX}R1", "A1"]


@pytest.mark.asyncio
async def test_openai_compat_stream_handles_multiline_sse_event(provider, valid_config, monkeypatch):
    chunks = [
        b"data: {\n",
        b'data:   "choices": [{"delta": {"content": "ok"}}]\n',
        b"data: }\n\n",
        b"data: [DONE]\n\n",
    ]
    fake_session = _FakeSession(response=_FakeStreamResponse(chunks))

    async def _fake_get_stream_session(*args, **kwargs):
        return fake_session

    monkeypatch.setattr(openai_provider_module, "get_stream_session", _fake_get_stream_session)

    tokens = []
    async for token in provider.invoke_stream("hello", "gpt-4.1-mini", valid_config):
        tokens.append(token)

    assert tokens == ["ok"]


@pytest.mark.asyncio
async def test_openai_compat_stream_preserves_utf8_when_bytes_split(provider, valid_config, monkeypatch):
    prefix = b'data: {"choices":[{"delta":{"content":"'
    han = "汉".encode("utf-8")
    suffix = b'"}}]}\n\n'
    chunks = [
        prefix + han[:1],
        han[1:] + suffix,
        b"data: [DONE]\n\n",
    ]
    fake_session = _FakeSession(response=_FakeStreamResponse(chunks))

    async def _fake_get_stream_session(*args, **kwargs):
        return fake_session

    monkeypatch.setattr(openai_provider_module, "get_stream_session", _fake_get_stream_session)

    tokens = []
    async for token in provider.invoke_stream("hello", "gpt-4.1-mini", valid_config):
        tokens.append(token)

    assert tokens == ["汉"]
