"""
Role Chat JetStream Publisher

Publish each role-chat chunk to NAT JetStream. The chunk shape is
thinking_chunk / content_chunk / tool_call / tool_result / complete / error,
which the front-end ``useChatStreamWS`` hook decodes from the v2 JetStream
envelope.

Wire format (one event per publish):
    subject = "hp.runtime.chat.<session_id>"  (workspace-agnostic, like bench)
    payload = RuntimeEventEnvelope.to_dict()  with
        channel = "chat:<session_id>"
        kind    = "chat.chunk"
        payload = { "type": "<chunk_type>", "data": {...} }
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import uuid4

from polaris.cells.roles.runtime.public.service import RoleRuntimeService

from .role_runtime_chat import (
    _build_session_command,
    _queue_event_from_runtime_event,
    _stream_complete_payload,
)

logger = logging.getLogger(__name__)

ChatChunkCallback = Callable[[dict[str, Any]], Awaitable[None]]


def _new_chat_session_id(role: str) -> str:
    """Generate a compact chat session id.

    Returns:
        e.g. ``chat-pm-1f2c3a4b5c``
    """
    return f"chat-{role}-{uuid4().hex[:12]}"


async def _publish_chat_chunk(
    *,
    session_id: str,
    chunk: dict[str, Any],
    seq: int,
) -> bool:
    """Publish a single chat chunk to NAT JetStream as a v2 RuntimeEventEnvelope.

    Returns:
        True if the publish was accepted by the JetStream publisher.
    """
    try:
        from polaris.delivery.http.routers.jetstream_utils import publish_to_jetstream
        from polaris.infrastructure.messaging.nats.nats_types import (
            create_runtime_event,
        )
    except (ImportError, RuntimeError, ValueError) as exc:  # pragma: no cover
        logger.debug("chat-jetstream import failed: %s", exc)
        return False

    envelope = create_runtime_event(
        workspace_key="chat",  # workspace-agnostic, like ``bench``
        run_id=session_id,
        channel=f"chat:{session_id}",
        kind="chat.chunk",
        payload={
            "type": str(chunk.get("type") or "message"),
            "data": dict(chunk.get("data") or {}),
            "seq": int(seq),
        },
        meta={"source": "role_chat_jetstream"},
    )
    return await publish_to_jetstream(
        subject=f"hp.runtime.chat.{session_id}",
        payload=envelope.to_dict(),
    )


async def execute_role_chat_jetstream(
    *,
    role: str,
    workspace: str,
    message: str,
    payload: Mapping[str, Any] | None,
    default_domain: str,
    host_kind: str,
    context: Mapping[str, Any] | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    history: tuple[tuple[str, str], ...] | list[tuple[str, str]] | None = None,
    on_chunk: ChatChunkCallback | None = None,
) -> str:
    """Run the role chat LLM and publish every chunk to NAT JetStream.

    Returns the ``session_id`` used (so the caller can return it in the HTTP
    response). The same session_id is what the front-end subscribes to via
    ``subscribeChannels([{ channel: "chat:<session_id>" }])`` over the
    runtime.v2 WebSocket — identical to the bench path that already works.
    """
    resolved_session_id = session_id or _new_chat_session_id(role)
    command = _build_session_command(
        role=role,
        workspace=workspace,
        message=message,
        payload=payload,
        default_domain=default_domain,
        host_kind=host_kind,
        stream=True,
        context=context,
        session_id=resolved_session_id,
        run_id=run_id,
        task_id=task_id,
        history=history,
    )

    seq = 0
    saw_terminal = False
    try:
        async for event in RoleRuntimeService().stream_chat_turn(command):
            chunk = _queue_event_from_runtime_event(event)
            event_type = str(chunk.get("type") or "")
            if on_chunk is not None:
                await on_chunk(chunk)
            ok = await _publish_chat_chunk(
                session_id=resolved_session_id,
                chunk=chunk,
                seq=seq,
            )
            seq += 1
            if not ok:
                # Best-effort; do not break the LLM stream if JetStream is down.
                logger.debug("chat-jetstream publish dropped chunk for %s", resolved_session_id)
            if event_type in {"complete", "error"}:
                saw_terminal = True
                break
    except (RuntimeError, ValueError, asyncio.CancelledError) as exc:
        logger.warning("chat-jetstream stream failed for %s: %s", resolved_session_id, exc)
        error_chunk = {"type": "error", "data": {"error": str(exc) or "role_runtime_stream_failed"}}
        if on_chunk is not None:
            await on_chunk(error_chunk)
        await _publish_chat_chunk(
            session_id=resolved_session_id,
            chunk=error_chunk,
            seq=seq,
        )
        return resolved_session_id

    if not saw_terminal:
        # Synthesize a terminal complete chunk so the front-end always sees
        # a clean close over WS.
        complete_chunk = {
            "type": "complete",
            "data": _stream_complete_payload({"result": None}),
        }
        if on_chunk is not None:
            await on_chunk(complete_chunk)
        await _publish_chat_chunk(
            session_id=resolved_session_id,
            chunk=complete_chunk,
            seq=seq,
        )
    return resolved_session_id
