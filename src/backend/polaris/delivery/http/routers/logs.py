"""Logs router for querying and managing log events."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Body, Depends, Query
from polaris.delivery.http.routers._shared import StructuredHTTPException, require_auth
from polaris.delivery.http.schemas import (
    LogsChannelsResponse,
    LogsQueryResponse,
    LogsUserActionResponse,
)
from polaris.infrastructure.log_pipeline.canonical_event import LogChannel, LogSeverity
from polaris.infrastructure.log_pipeline.query import (
    LogQuery,
    LogQueryService,
)
from polaris.kernelone._runtime_config import resolve_env_str
from polaris.kernelone.storage import resolve_runtime_path
from pydantic import BaseModel

router = APIRouter(prefix="/logs", tags=["logs"], dependencies=[Depends(require_auth)])


class UserActionRequest(BaseModel):
    """Request for logging a user action."""

    action: str
    user: str = "anonymous"
    metadata: dict = {}


@router.get("/query", response_model=LogsQueryResponse)  # DEPRECATED
async def query_logs(
    run_id: str | None = Query(None, description="Filter by run_id"),
    channel: str | None = Query(None, description="Filter by channel (system, process, llm)"),
    severity: str | None = Query(None, description="Filter by severity (debug, info, warn, error)"),
    actor: str | None = Query(None, description="Filter by actor"),
    task_id: str | None = Query(None, description="Filter by task_id"),
    cursor: str | None = Query(None, description="Cursor for pagination"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    high_signal_only: bool = Query(False, description="Filter out noise"),
    workspace: str | None = Query(None, description="Workspace path"),
) -> dict:
    """Query log events with filtering and pagination.

    Supports filtering by:
    - run_id: specific run
    - channel: system, process, llm
    - severity: debug, info, warn, error
    - actor: specific actor (e.g., PM, Director)
    - task_id: specific task
    - cursor: pagination cursor
    - limit: results per page
    - high_signal_only: filter out low-value events
    """
    workspace_path = workspace or resolve_env_str("workspace") or "."

    # Validate and cast channel/severity to expected Literal types
    valid_channels = ("system", "process", "llm")
    valid_severities = ("debug", "info", "warn", "error", "critical")
    typed_channel: LogChannel | None = cast(LogChannel, channel) if channel in valid_channels else None
    typed_severity: LogSeverity | None = cast(LogSeverity, severity) if severity in valid_severities else None

    query = LogQuery(
        run_id=run_id,
        channel=typed_channel,
        severity=typed_severity,
        actor=actor,
        task_id=task_id,
        cursor=cursor,
        limit=limit,
        high_signal_only=high_signal_only,
    )

    service = LogQueryService(workspace=workspace_path)
    result = service.query(query)

    return {
        "events": [e.model_dump() for e in result.events],
        "next_cursor": result.next_cursor,
        "total_count": result.total_count,
        "has_more": result.has_more,
    }


@router.get("/v2/query", response_model=LogsQueryResponse)
async def v2_query_logs(
    run_id: str | None = Query(None, description="Filter by run_id"),
    channel: str | None = Query(None, description="Filter by channel (system, process, llm)"),
    severity: str | None = Query(None, description="Filter by severity (debug, info, warn, error)"),
    actor: str | None = Query(None, description="Filter by actor"),
    task_id: str | None = Query(None, description="Filter by task_id"),
    cursor: str | None = Query(None, description="Cursor for pagination"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    high_signal_only: bool = Query(False, description="Filter out noise"),
    workspace: str | None = Query(None, description="Workspace path"),
) -> dict:
    """Query log events with filtering and pagination."""
    workspace_path = workspace or resolve_env_str("workspace") or "."

    valid_channels = ("system", "process", "llm")
    valid_severities = ("debug", "info", "warn", "error", "critical")
    typed_channel: LogChannel | None = cast(LogChannel, channel) if channel in valid_channels else None
    typed_severity: LogSeverity | None = cast(LogSeverity, severity) if severity in valid_severities else None

    query = LogQuery(
        run_id=run_id,
        channel=typed_channel,
        severity=typed_severity,
        actor=actor,
        task_id=task_id,
        cursor=cursor,
        limit=limit,
        high_signal_only=high_signal_only,
    )

    service = LogQueryService(workspace=workspace_path)
    result = service.query(query)

    return {
        "events": [e.model_dump() for e in result.events],
        "next_cursor": result.next_cursor,
        "total_count": result.total_count,
        "has_more": result.has_more,
    }


@router.post("/user-action", response_model=LogsUserActionResponse)  # DEPRECATED
async def log_user_action(
    action: str = Body(..., description="User action name"),
    user: str = Body("anonymous", description="User identifier"),
    metadata: dict = Body({}, description="Additional metadata"),
    workspace: str | None = Query(None, description="Workspace path"),
) -> dict:
    """Log a user action to the system channel.

    This endpoint writes key user interactions to the domain=user/channel=system
    for audit and analysis purposes.
    """
    workspace_path = workspace or resolve_env_str("workspace") or "."

    # Import here to avoid circular imports
    try:
        import time

        from polaris.kernelone.events import utc_iso_now
        from polaris.kernelone.fs.jsonl.ops import append_jsonl_atomic

        log_path = resolve_runtime_path(
            workspace_path,
            "runtime/logs/user_actions.jsonl",
        )

        payload = {
            "ts": utc_iso_now(),
            "ts_epoch": time.time(),
            "domain": "user",
            "channel": "system",
            "action": action,
            "user": user,
            "metadata": metadata,
        }

        append_jsonl_atomic(str(log_path), payload)
        return {"status": "logged", "action": action}
    except (RuntimeError, ValueError) as e:
        raise StructuredHTTPException(
            status_code=500,
            code="LOG_USER_ACTION_FAILED",
            message=f"Failed to log user action: {e}",
        ) from e


@router.post("/v2/user-action", response_model=LogsUserActionResponse)
async def v2_log_user_action(
    action: str = Body(..., description="User action name"),
    user: str = Body("anonymous", description="User identifier"),
    metadata: dict = Body({}, description="Additional metadata"),
    workspace: str | None = Query(None, description="Workspace path"),
) -> dict:
    """Log a user action to the system channel."""
    workspace_path = workspace or resolve_env_str("workspace") or "."

    try:
        import time

        from polaris.kernelone.events import utc_iso_now
        from polaris.kernelone.fs.jsonl.ops import append_jsonl_atomic

        log_path = resolve_runtime_path(
            workspace_path,
            "runtime/logs/user_actions.jsonl",
        )

        payload = {
            "ts": utc_iso_now(),
            "ts_epoch": time.time(),
            "domain": "user",
            "channel": "system",
            "action": action,
            "user": user,
            "metadata": metadata,
        }

        append_jsonl_atomic(str(log_path), payload)
        return {"status": "logged", "action": action}
    except (RuntimeError, ValueError) as e:
        raise StructuredHTTPException(
            status_code=500,
            code="LOG_USER_ACTION_FAILED",
            message=f"Failed to log user action: {e}",
        ) from e


@router.get("/channels", response_model=LogsChannelsResponse)  # DEPRECATED
async def get_channels(
    workspace: str | None = Query(None, description="Workspace path"),
) -> dict:
    """Get available log channels."""

    # Return the standard channels
    return {
        "channels": [
            {"name": "system", "description": "System events and status"},
            {"name": "process", "description": "Process execution events"},
            {"name": "llm", "description": "LLM interactions and responses"},
        ]
    }


@router.get("/v2/channels", response_model=LogsChannelsResponse)
async def v2_get_channels(
    workspace: str | None = Query(None, description="Workspace path"),
) -> dict:
    """Get available log channels."""
    return {
        "channels": [
            {"name": "system", "description": "System events and status"},
            {"name": "process", "description": "Process execution events"},
            {"name": "llm", "description": "LLM interactions and responses"},
        ]
    }
