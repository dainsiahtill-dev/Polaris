"""Legacy Neural Weave stream router.

HTTP SSE chat routes are removed. Real-time delivery must use the unified
Nat-JetStream WebSocket runtime transport.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.delivery.http.schemas.common import StreamHealthResponse
from pydantic import BaseModel

from ._shared import legacy_sse_removed, require_auth

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================


class StreamChatRequest(BaseModel):
    """Stream chat request for neural weave endpoint."""

    role: str | None = None
    message: str
    provider_id: str | None = None
    model: str | None = None
    context: dict[str, Any] | None = None
    options: dict[str, Any] | None = None


# =============================================================================
# Streaming Endpoints
# =============================================================================


@router.post("/v2/stream/chat", dependencies=[Depends(require_auth)])
async def stream_chat(
    request: Request,
    chat_request: StreamChatRequest,
) -> None:
    """Removed SSE endpoint; use role chat or session Nat-JetStream endpoints."""
    del request, chat_request
    legacy_sse_removed("/v2/role/{role}/chat/jetstream")


@router.post("/v2/stream/chat/backpressure", dependencies=[Depends(require_auth)])
async def stream_chat_with_backpressure(
    request: Request,
    chat_request: StreamChatRequest,
) -> None:
    """Removed SSE endpoint; use Nat-JetStream WebSocket flow control."""
    del request, chat_request
    legacy_sse_removed("/v2/role/{role}/chat/jetstream")


@router.get("/v2/stream/health", response_model=StreamHealthResponse, dependencies=[Depends(require_auth)])
async def stream_health() -> dict[str, str]:
    """Compatibility health endpoint for the removed HTTP stream subsystem."""
    return {"status": "removed", "streaming": "disabled", "transport": "nat-jetstream"}
