"""Tests for the legacy stream router after Nat-JetStream migration."""

from __future__ import annotations

import pytest
from polaris.delivery.http.routers._shared import StructuredHTTPException
from polaris.delivery.http.routers.stream_router import (
    StreamChatRequest,
    stream_chat,
    stream_chat_with_backpressure,
    stream_health,
)
from polaris.delivery.http.schemas.common import StreamHealthResponse


@pytest.mark.asyncio
async def test_stream_chat_fails_closed_to_nat_jetstream() -> None:
    """Legacy HTTP stream chat must not expose SSE framing."""

    with pytest.raises(StructuredHTTPException) as exc_info:
        await stream_chat(None, StreamChatRequest(message="hello"))

    exc = exc_info.value
    assert exc.status_code == 410
    assert exc.detail["code"] == "SSE_REMOVED"
    assert exc.detail["details"]["transport"] == "nat-jetstream"
    assert exc.detail["details"]["replacement"] == "/v2/role/{role}/chat/jetstream"


@pytest.mark.asyncio
async def test_stream_chat_backpressure_fails_closed_to_nat_jetstream() -> None:
    """The old backpressure route is not a second realtime transport."""

    with pytest.raises(StructuredHTTPException) as exc_info:
        await stream_chat_with_backpressure(None, StreamChatRequest(message="hello"))

    exc = exc_info.value
    assert exc.status_code == 410
    assert exc.detail["code"] == "SSE_REMOVED"
    assert exc.detail["details"]["transport"] == "nat-jetstream"


@pytest.mark.asyncio
async def test_stream_health_reports_legacy_http_stream_removed() -> None:
    """Health must not advertise the removed SSE stream as enabled."""

    result = await stream_health()

    assert result == {
        "status": "removed",
        "streaming": "disabled",
        "transport": "nat-jetstream",
    }
    assert StreamHealthResponse.model_validate(result).transport == "nat-jetstream"
