from __future__ import annotations

import polaris.infrastructure.llm.providers.anthropic_compat_provider as anthropic_provider_module
import pytest
from polaris.infrastructure.llm.providers.anthropic_compat_provider import AnthropicCompatProvider


@pytest.fixture
def provider():
    return AnthropicCompatProvider()


@pytest.fixture
def valid_config():
    return {
        "base_url": "https://api.example.com/v1",
        "api_path": "/v1/messages",
        "api_key": "test-api-key",
        "timeout": 60,
        "temperature": 0.2,
    }


@pytest.mark.asyncio
async def test_anthropic_compat_stream_emits_tokens_from_structured_events(
    provider,
    valid_config,
    monkeypatch,
):
    async def _fake_invoke_stream_with_retry(*args, **kwargs):
        del args, kwargs
        yield {
            "type": "content_block_delta",
            "delta": {"text": "ok"},
        }

    monkeypatch.setattr(
        anthropic_provider_module,
        "invoke_stream_with_retry",
        _fake_invoke_stream_with_retry,
    )

    tokens = []
    async for token in provider.invoke_stream("hello", "claude-3-5-sonnet", valid_config):
        tokens.append(token)

    assert tokens == ["ok"]


@pytest.mark.asyncio
async def test_anthropic_compat_stream_preserves_utf8_from_structured_events(
    provider,
    valid_config,
    monkeypatch,
):
    async def _fake_invoke_stream_with_retry(*args, **kwargs):
        del args, kwargs
        yield {
            "type": "content_block_delta",
            "delta": {"text": "汉"},
        }

    monkeypatch.setattr(
        anthropic_provider_module,
        "invoke_stream_with_retry",
        _fake_invoke_stream_with_retry,
    )

    tokens = []
    async for token in provider.invoke_stream("hello", "claude-3-5-sonnet", valid_config):
        tokens.append(token)

    assert tokens == ["汉"]
