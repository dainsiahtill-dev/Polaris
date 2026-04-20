"""Runtime event query handler for websocket endpoint."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = logging.getLogger(__name__)

SendJsonSafeFn = Callable[..., Awaitable[bool]]


async def handle_event_query(
    websocket: WebSocket,
    workspace: str,
    cache_root: str,
    message: dict[str, Any],
    *,
    send_json_safe: SendJsonSafeFn,
    connection_id: str = "",
    client: str = "",
) -> bool:
    try:
        from polaris.infrastructure.log_pipeline import LogQuery, LogQueryService
    except (RuntimeError, ValueError) as exc:
        logger.debug("Failed to import LogQueryService: %s", exc)
        return await send_json_safe(
            websocket,
            {"type": "error", "message": "Log pipeline not available"},
            connection_id=connection_id,
            client=client,
            workspace=workspace,
        )

    action = str(message.get("action") or "query").strip().lower()
    if action != "query":
        return await send_json_safe(
            websocket,
            {"type": "error", "message": f"Unsupported event action: {action}"},
            connection_id=connection_id,
            client=client,
            workspace=workspace,
        )

    query = LogQuery(
        channel=message.get("channel"),
        severity=message.get("severity"),
        run_id=message.get("run_id"),
        cursor=message.get("cursor"),
        limit=message.get("limit", 100),
        high_signal_only=message.get("high_signal_only", False),
    )
    try:
        service = LogQueryService(workspace=workspace, runtime_root=cache_root)
        result = service.query(query)
    except (RuntimeError, ValueError) as exc:
        return await send_json_safe(
            websocket,
            {"type": "error", "message": f"Query failed: {exc!s}"},
            connection_id=connection_id,
            client=client,
            workspace=workspace,
        )

    return await send_json_safe(
        websocket,
        {
            "type": "event",
            "action": "query_result",
            "events": [item.model_dump() for item in result.events],
            "next_cursor": result.next_cursor,
            "has_more": result.has_more,
            "total_count": result.total_count,
        },
        connection_id=connection_id,
        client=client,
        workspace=workspace,
    )
