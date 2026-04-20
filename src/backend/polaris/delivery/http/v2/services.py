"""API routes for new domain services.

Exposes BackgroundTask, Todo, Token, Security, and Transcript services.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from polaris.delivery.http.dependencies import (
    get_background_task_service as get_background_task_service_dep,
    require_auth,
)
from polaris.domain.services import (
    get_security_service,
    get_todo_service,
    get_token_service,
    get_transcript_service,
)
from polaris.domain.services.background_task import (
    BackgroundTask,
    BackgroundTaskService,
)
from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS
from pydantic import BaseModel

logger = logging.getLogger(__name__)
from polaris.domain.services.tool_timeout_service import ToolTier

router = APIRouter(prefix="/services", tags=["Services"])


# Request/Response models
class CreateTaskRequest(BaseModel):
    command: str
    timeout: int = DEFAULT_OPERATION_TIMEOUT_SECONDS
    cwd: str = "."
    tier: str = "background"


class TaskResponse(BaseModel):
    id: str
    command: str
    state: str
    timeout: int
    result: dict | None


class TodoCreateRequest(BaseModel):
    content: str
    priority: str = "medium"
    tags: list[str] = []


class TodoItemResponse(BaseModel):
    id: str
    content: str
    status: str
    priority: str
    tags: list[str]


class SecurityCheckRequest(BaseModel):
    command: str


class SecurityCheckResponse(BaseModel):
    is_safe: bool
    reason: str = ""
    suggested_alternative: str | None = None


class TokenStatusResponse(BaseModel):
    used_tokens: int
    budget_limit: int | None
    remaining_tokens: int | None
    percent_used: float
    is_exceeded: bool


# Background Tasks endpoints
@router.post("/tasks", response_model=TaskResponse, dependencies=[Depends(require_auth)])
async def create_background_task(
    request: CreateTaskRequest,
    service: BackgroundTaskService = Depends(get_background_task_service_dep),
) -> TaskResponse:
    """Create and queue a background task."""
    tier = ToolTier.BACKGROUND
    if request.tier in ["foreground", "background", "critical", "fast"]:
        tier = ToolTier(request.tier)

    task = BackgroundTask(
        command=request.command,
        timeout=request.timeout,
        cwd=request.cwd,
        tier=tier,
    )

    await service.submit(task)

    return TaskResponse(
        id=task.id,
        command=task.command,
        state=task.state.name,
        timeout=task.timeout,
        result=None,
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse, dependencies=[Depends(require_auth)])
async def get_background_task(
    task_id: str,
    service: BackgroundTaskService = Depends(get_background_task_service_dep),
) -> TaskResponse:
    """Get background task by ID."""
    task = service.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return TaskResponse(
        id=task.id,
        command=task.command,
        state=task.state.name,
        timeout=task.timeout,
        result=task.result.to_dict() if task.result else None,
    )


@router.get("/tasks", dependencies=[Depends(require_auth)])
async def list_background_tasks(
    state: str | None = Query(None, description="Filter by state"),
    service: BackgroundTaskService = Depends(get_background_task_service_dep),
) -> list[TaskResponse]:
    """List all background tasks."""
    tasks = service.list_tasks()

    if state:
        tasks = [t for t in tasks if t.state.name.lower() == state.lower()]

    return [
        TaskResponse(
            id=t.id,
            command=t.command,
            state=t.state.name,
            timeout=t.timeout,
            result=t.result.to_dict() if t.result else None,
        )
        for t in tasks
    ]


# Todo endpoints
@router.post("/todos", response_model=TodoItemResponse, dependencies=[Depends(require_auth)])
async def create_todo(request: TodoCreateRequest) -> TodoItemResponse:
    """Create a new todo item."""
    todo_service = get_todo_service()

    from polaris.domain.services.todo_service import Priority

    priority = Priority.MEDIUM
    if request.priority.lower() == "critical":
        priority = Priority.CRITICAL
    elif request.priority.lower() == "high":
        priority = Priority.HIGH
    elif request.priority.lower() == "low":
        priority = Priority.LOW

    item = todo_service.add_item(
        content=request.content,
        priority=priority,
        tags=request.tags,
    )

    return TodoItemResponse(
        id=item.id,
        content=item.content,
        status=item.status.value,
        priority=item.priority.value,
        tags=item.tags,
    )


@router.get("/todos", dependencies=[Depends(require_auth)])
async def list_todos(
    status: str | None = Query(None, description="Filter by status"),
) -> list[TodoItemResponse]:
    """List all todo items."""
    todo_service = get_todo_service()
    items = todo_service.list_items()

    if status:
        items = [i for i in items if i.status.value.lower() == status.lower()]

    return [
        TodoItemResponse(
            id=i.id,
            content=i.content,
            status=i.status.value,
            priority=i.priority.value,
            tags=i.tags,
        )
        for i in items
    ]


@router.get("/todos/summary", dependencies=[Depends(require_auth)])
async def get_todo_summary() -> dict[str, Any]:
    """Get todo summary and next action."""
    todo_service = get_todo_service()
    summary = todo_service.get_summary()
    next_item = todo_service.get_next_item()

    return {
        "summary": summary,
        "next_action": {
            "id": next_item.id,
            "content": next_item.content,
            "priority": next_item.priority.value,
        }
        if next_item
        else None,
    }


@router.post("/todos/{item_id}/done", dependencies=[Depends(require_auth)])
async def mark_todo_done(item_id: str) -> dict[str, bool]:
    """Mark a todo item as done."""
    todo_service = get_todo_service()

    try:
        todo_service.mark_done(item_id)
        return {"ok": True}
    except (RuntimeError, ValueError) as e:
        logger.error("mark_todo_done failed: %s", e)
        raise HTTPException(status_code=400, detail="internal error")


# Token Budget endpoints
@router.get("/tokens/status", response_model=TokenStatusResponse, dependencies=[Depends(require_auth)])
async def get_token_status() -> TokenStatusResponse:
    """Get current token budget status."""
    token_service = get_token_service()
    status = token_service.get_budget_status()

    return TokenStatusResponse(
        used_tokens=status.used_tokens,
        budget_limit=status.budget_limit,
        remaining_tokens=status.remaining_tokens,
        percent_used=status.percent_used,
        is_exceeded=status.is_exceeded,
    )


@router.post("/tokens/record", dependencies=[Depends(require_auth)])
async def record_token_usage(tokens: int) -> dict[str, Any]:
    """Record token usage."""
    token_service = get_token_service()
    token_service.record_usage(tokens)
    status = token_service.get_budget_status()

    return {
        "ok": True,
        "recorded": tokens,
        "total_used": status.used_tokens,
        "remaining": status.remaining_tokens,
    }


# Security endpoints
@router.post("/security/check", response_model=SecurityCheckResponse, dependencies=[Depends(require_auth)])
async def check_security(request: SecurityCheckRequest) -> SecurityCheckResponse:
    """Check if a command is safe to execute."""
    security_service = get_security_service(".")
    result = security_service.is_command_safe(request.command)

    return SecurityCheckResponse(
        is_safe=result.is_safe,
        reason=result.reason or "",
        suggested_alternative=result.suggested_alternative,
    )


# Transcript endpoints
@router.get("/transcript", dependencies=[Depends(require_auth)])
async def get_transcript(
    limit: int = Query(100, ge=1, le=1000),
    message_type: str | None = Query(None, description="Filter by message type"),
) -> list[dict[str, Any]]:
    """Get transcript messages."""
    transcript_service = get_transcript_service()
    messages = transcript_service.get_messages(limit=limit)

    if message_type:
        messages = [m for m in messages if m.get("type") == message_type]

    return messages


@router.get("/transcript/session", dependencies=[Depends(require_auth)])
async def get_transcript_session_info() -> dict[str, Any]:
    """Get current transcript session info."""
    transcript_service = get_transcript_service()
    session = transcript_service._current_session

    if not session:
        return {"active": False}

    return {
        "active": True,
        "session_id": session.session_id,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "metadata": session.metadata,
        "message_count": len(transcript_service.get_messages(limit=10000)),
    }


@router.post("/transcript/message", dependencies=[Depends(require_auth)])
async def record_transcript_message(
    role: str,
    content: str,
    metadata: dict | None = None,
) -> dict[str, bool]:
    """Record a message to the transcript."""
    transcript_service = get_transcript_service()
    transcript_service.record_message(
        role=role,
        content=content,
        metadata=metadata or {},
    )
    return {"ok": True}
