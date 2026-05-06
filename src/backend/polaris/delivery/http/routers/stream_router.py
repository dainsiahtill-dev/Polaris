"""Neural Weave SSE Stream Router - Full-Duplex Streaming Architecture

Demonstrates the EventStreamer class for SSE serialization and multiplexing
with StreamExecutor integration.

This router provides streaming endpoints that:
1. Convert AIStreamEvent to SSE format via EventStreamer
2. Support multiple consumers via asyncio.Queue-based multiplexing
3. Provide backpressure control via AsyncBackpressureBuffer
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from polaris.delivery.http.schemas.common import StreamHealthResponse
from polaris.kernelone.events.constants import (
    EVENT_TYPE_COMPLETE,
    EVENT_TYPE_ERROR,
    EVENT_TYPE_TOOL_CALL,
    EVENT_TYPE_TOOL_RESULT,
)
from polaris.kernelone.llm.engine.contracts import AIRequest, AIStreamEvent
from polaris.kernelone.llm.engine.stream.config import StreamConfig
from polaris.kernelone.llm.engine.stream.executor import StreamExecutor
from polaris.kernelone.llm.shared_contracts import TaskType
from polaris.kernelone.stream import EventStreamer
from polaris.kernelone.stream.sse_streamer import AsyncBackpressureBuffer
from pydantic import BaseModel

from ._shared import get_state, require_auth

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
) -> StreamingResponse:
    """Stream chat endpoint using EventStreamer for SSE multiplexing.

    This endpoint demonstrates the Neural Weave architecture:
    1. StreamExecutor produces AIStreamEvent via invoke_stream()
    2. EventStreamer serializes to SSE and broadcasts to multiple consumers
    3. FastAPI StreamingResponse delivers SSE to client

    The endpoint supports:
    - Real-time token streaming (chunk events)
    - Thinking/reasoning progress (reasoning_chunk events)
    - Tool call lifecycle (tool_call events with tool_start/tool_end metadata)
    - Backpressure control via AsyncBackpressureBuffer

    Args:
        request: The FastAPI request object.
        chat_request: The chat request with message and options.

    Returns:
        StreamingResponse with SSE events.
    """
    state = get_state(request)

    # Create StreamExecutor
    executor = StreamExecutor(
        workspace=str(state.settings.workspace),
        telemetry=None,  # Could add telemetry here
    )

    # Create EventStreamer for SSE multiplexing
    config = StreamConfig.from_env()
    streamer = EventStreamer(config=config, max_queue_size=50)

    # Build AIRequest from chat request
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role=str(chat_request.role or "user"),
        input=chat_request.message,
        provider_id=chat_request.provider_id,
        model=chat_request.model,
        context=dict(chat_request.context or {}),
        options=dict(chat_request.options or {}),
    )

    # Background task to run the stream and broadcast events
    async def run_stream() -> None:
        try:
            await streamer.broadcast(
                executor.invoke_stream(ai_request),
                task_name="stream-chat-broadcast",
            )
        except asyncio.CancelledError:
            logger.debug("[stream-chat] stream cancelled")
        except (RuntimeError, ValueError, OSError) as exc:
            # Catch common streaming exceptions
            logger.exception("[stream-chat] stream error: %s", exc)
            await streamer.publish(AIStreamEvent.error_event(str(exc)))
        finally:
            await streamer.close()

    # Start the broadcast task
    stream_task = asyncio.create_task(run_stream())
    stream_task.set_name("stream-chat-broadcast-task")

    # Create SSE generator that consumes from the streamer
    async def sse_generator() -> Any:
        stream_task_ref: asyncio.Task[Any] | None = stream_task
        try:
            async for event in streamer.subscribe():
                yield format_sse_event(event)
                await asyncio.sleep(0)  # Force flush
        except asyncio.CancelledError:
            logger.debug("[stream-chat] SSE generator cancelled")
            raise
        finally:
            # Cancel and wait for stream task with timeout
            if stream_task_ref is not None:
                await _cancel_task_with_timeout(stream_task_ref)

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/v2/stream/chat/backpressure", dependencies=[Depends(require_auth)])
async def stream_chat_with_backpressure(
    request: Request,
    chat_request: StreamChatRequest,
) -> StreamingResponse:
    """Stream chat endpoint with explicit backpressure control.

    This endpoint demonstrates the AsyncBackpressureBuffer for cases where
    the consumer might be slower than the producer.

    Args:
        request: The FastAPI request object.
        chat_request: The chat request with message and options.

    Returns:
        StreamingResponse with SSE events and backpressure control.
    """
    state = get_state(request)

    # Create components
    executor = StreamExecutor(
        workspace=str(state.settings.workspace),
        telemetry=None,
    )
    config = StreamConfig.from_env()
    buffer = AsyncBackpressureBuffer(config=config)

    # Build AIRequest
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role=str(chat_request.role or "user"),
        input=chat_request.message,
        provider_id=chat_request.provider_id,
        model=chat_request.model,
        context=dict(chat_request.context or {}),
        options=dict(chat_request.options or {}),
    )

    async def run_stream_with_buffer() -> None:
        """Run stream and feed through backpressure buffer."""
        try:
            async for event in executor.invoke_stream(ai_request):
                # Feed through backpressure-aware buffer
                event_json = json.dumps(event.to_dict(), ensure_ascii=False)
                await buffer.feed(event_json)
        except asyncio.CancelledError:
            pass  # Expected cancellation
        except (RuntimeError, ValueError, OSError) as exc:
            # Catch common streaming exceptions
            logger.exception("[stream-chat/backpressure] error: %s", exc)
        finally:
            await buffer.clear()

    # Start stream task
    stream_task = asyncio.create_task(run_stream_with_buffer())
    stream_task.set_name("stream-chat-backpressure-task")

    async def sse_generator() -> Any:
        """Generate SSE from backpressure buffer."""
        stream_task_ref: asyncio.Task[Any] | None = stream_task
        try:
            while True:
                chunks = await asyncio.wait_for(buffer.drain(), timeout=30.0)
                if not chunks:
                    # Check if stream is done
                    if stream_task_ref is not None and stream_task_ref.done():
                        break
                    continue

                for chunk_json in chunks:
                    event_dict = json.loads(chunk_json)
                    event_type = event_dict.get("type", "message")
                    yield f"event: {event_type}\ndata: {chunk_json}\n\n".encode()
                    await asyncio.sleep(0)

                # Check completion
                if stream_task_ref is not None and stream_task_ref.done():
                    break
        except asyncio.CancelledError:
            logger.debug("[stream-chat/backpressure] SSE generator cancelled")
            raise
        except asyncio.TimeoutError:
            # Keep-alive ping
            yield b"event: ping\ndata: {}\n\n"
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            # Catch common generator exceptions
            logger.warning("[stream-chat/backpressure] generator error: %s", exc)
        finally:
            # Cancel and wait for stream task with timeout
            if stream_task_ref is not None:
                await _cancel_task_with_timeout(stream_task_ref)

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/v2/stream/health", response_model=StreamHealthResponse, dependencies=[Depends(require_auth)])
async def stream_health() -> dict[str, str]:
    """Health check endpoint for stream subsystem.

    Returns:
        Health status.
    """
    return {"status": "healthy", "streaming": "enabled"}
