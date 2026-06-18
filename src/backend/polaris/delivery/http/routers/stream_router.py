"""Legacy Neural Weave stream router.

HTTP SSE chat routes are removed. Real-time delivery must use the unified
Nat-JetStream WebSocket runtime transport.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.delivery.http.schemas.common import StreamHealthResponse
from polaris.kernelone.events.constants import (
    EVENT_TYPE_COMPLETE,
    EVENT_TYPE_ERROR,
    EVENT_TYPE_TOOL_CALL,
    EVENT_TYPE_TOOL_RESULT,
)
from polaris.kernelone.llm.engine.contracts import AIStreamEvent
from polaris.kernelone.stream import EventStreamer
from pydantic import BaseModel

from ._shared import legacy_sse_removed, require_auth

logger = logging.getLogger(__name__)

router = APIRouter()

# Cancellation timeout - how long to wait for task to respond to cancellation
_CANCEL_TIMEOUT: float = 2.0


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


class StreamChatResponse(BaseModel):
    """Response indicating the stream has started."""

    status: str = "streaming"
    message: str = "Stream started"


# =============================================================================
# SSE Event Formatting Utilities
# =============================================================================


def format_sse_event(event: AIStreamEvent) -> bytes:
    """Format an AIStreamEvent as SSE bytes.

    Args:
        event: The AIStreamEvent to format.

    Returns:
        SSE-formatted bytes ready for HTTP streaming.
    """
    event_type_map = {
        "chunk": "content_chunk",
        "reasoning_chunk": "thinking_chunk",
        EVENT_TYPE_TOOL_CALL: "tool_call",
        EVENT_TYPE_TOOL_RESULT: "tool_result",
        "meta": "meta",
        EVENT_TYPE_COMPLETE: "complete",
        EVENT_TYPE_ERROR: "error",
    }

    data = event.to_dict()
    sse_type = event_type_map.get(event.type.value, "message")

    # Format as SSE (must end with \n\n for proper SSE termination)
    lines = [f"event: {sse_type}", f"data: {json.dumps(data, ensure_ascii=False)}", "", ""]
    return "\n".join(lines).encode("utf-8")


async def _cancel_task_with_timeout(task: asyncio.Task[Any] | None) -> None:
    """Cancel a task and wait for it to complete with timeout.

    This ensures proper cleanup of background tasks during stream termination.

    Args:
        task: The task to cancel, or None.
    """
    if task is None:
        return

    if task.done():
        return

    # Request cancellation
    task.cancel()

    # Wait for task to respond to cancellation with timeout
    try:
        await asyncio.wait_for(task, timeout=_CANCEL_TIMEOUT)
    except asyncio.CancelledError:
        pass  # Expected - task acknowledged cancellation
    except asyncio.TimeoutError:
        # Task didn't respond to cancellation in time
        logger.warning(
            "[stream-router] Task %r did not complete after %.1fs cancellation timeout",
            task.get_name() if hasattr(task, "get_name") else "unknown",
            _CANCEL_TIMEOUT,
        )
    except BaseException as exc:  # noqa: BLE001
        # Log unexpected exceptions from cancelled task (but not CancelledError)
        # We catch BaseException to also catch potential GeneratorExit etc.
        if not isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            logger.debug(
                "[stream-router] Task %r raised unexpected exception during cancellation: %s",
                task.get_name() if hasattr(task, "get_name") else "unknown",
                exc,
            )


async def sse_stream_generator(
    streamer: EventStreamer,
    timeout: float = 180.0,
) -> Any:
    """Generate SSE bytes from an EventStreamer.

    Args:
        streamer: The EventStreamer to consume events from.
        timeout: Timeout in seconds for keep-alive pings.

    Yields:
        SSE-formatted bytes.
    """
    try:
        async for event in streamer.subscribe():
            yield format_sse_event(event)
            # Force flush to ensure immediate delivery
            await asyncio.sleep(0)

        # Normal completion
        yield b"event: complete\ndata: {}\n\n"
    except asyncio.CancelledError:
        raise  # noqa: RUF100
    except (RuntimeError, ValueError) as exc:
        # Catch common streaming exceptions
        logger.warning("[stream-router] SSE generator error: %s", exc)
        error_data = json.dumps({"error": str(exc)}, ensure_ascii=False)
        yield f"event: error\ndata: {error_data}\n\n".encode()


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
    """Health check endpoint for stream subsystem.

    Returns:
        Health status.
    """
    return {"status": "healthy", "streaming": "enabled"}
