"""Explicit rejection for the retired role chat stream publisher."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from polaris.delivery.http.legacy_engine_retired import legacy_engine_retired

ChatChunkCallback = Callable[[dict[str, Any]], Awaitable[None]]


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
    """Reject the old runtime publisher without emitting synthetic success."""
    raise legacy_engine_retired("Legacy role chat JetStream execution")
