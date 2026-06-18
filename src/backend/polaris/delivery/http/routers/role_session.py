"""Role Session Router - 统一角色会话 API

提供完整的 RoleSession 管理接口：
- Session CRUD
- 消息发送/接收
- 附着管理
- 产物导出

这是 Polaris 角色多宿主架构的核心 API。
"""

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from polaris.cells.audit.evidence.public.service import RoleSessionAuditService
from polaris.cells.roles.session.public import (
    AttachmentMode,
    RoleHostKind,
    RoleSessionArtifactService,
    RoleSessionContextMemoryService,
    RoleSessionService,
    SessionType,
)
from polaris.cells.roles.session.public.contracts import (
    GetRoleSessionStateQueryV1,
    ReadRoleSessionArtifactQueryV1,
    ReadRoleSessionEpisodeQueryV1,
    SearchRoleSessionMemoryQueryV1,
)
from polaris.delivery.http.schemas.common import (
    ArtifactDetailResponse,
    ArtifactListResponse,
    AuditLogResponse,
    EpisodeDetailResponse,
    MemorySearchResponse,
    MemoryStateResponse,
    MessageListResponse,
    RoleCapabilitiesResponse,
    SessionDeleteResponse,
    SessionExportResponse,
    SessionListResponse,
    SessionResponse,
    WorkflowExportResponse,
)
from polaris.delivery.http.workspace import active_workspace_value
from polaris.domain.entities.capability import get_role_capabilities as get_caps
from polaris.kernelone.constants import DEFAULT_DIRECTOR_MAX_PARALLELISM
from polaris.kernelone.context.session_continuity import (
    SessionContinuityEngine,
    SessionContinuityRequest,
    history_pairs_to_messages,
    messages_to_history_pairs,
)
from polaris.kernelone.events.constants import (
    EVENT_TYPE_COMPLETE,
    EVENT_TYPE_THINKING_CHUNK,
)
from pydantic import BaseModel

from ._shared import StructuredHTTPException, ensure_required_roles_ready, get_state, legacy_sse_removed, require_auth
from .role_chat_jetstream import execute_role_chat_jetstream

logger = logging.getLogger(__name__)

router = APIRouter()
_SESSION_CONTINUITY_ENGINE = SessionContinuityEngine()
ROLE_SESSION_AUDIT_EVENT_TYPES = frozenset(RoleSessionAuditService.EVENT_TYPES)
_ROLE_SESSION_JETSTREAM_TASKS: set[asyncio.Task[None]] = set()


def _track_role_session_jetstream_task(task: asyncio.Task[None]) -> None:
    _ROLE_SESSION_JETSTREAM_TASKS.add(task)

    def _on_done(done_task: asyncio.Task[None]) -> None:
        _ROLE_SESSION_JETSTREAM_TASKS.discard(done_task)
        with contextlib.suppress(asyncio.CancelledError):
            exc = done_task.exception()
            if exc is not None:
                logger.warning("role_session_jetstream background task failed: %s", exc)

    task.add_done_callback(_on_done)


# ==================== Request/Response Models ====================


class CreateSessionRequest(BaseModel):
    """创建会话请求"""

    role: str
    host_kind: str | None = RoleHostKind.ELECTRON_WORKBENCH.value
    workspace: str | None = None
    session_type: str | None = SessionType.WORKBENCH.value
    attachment_mode: str | None = AttachmentMode.ISOLATED.value
    title: str | None = None
    context_config: dict[str, Any] | None = None
    capability_profile: dict[str, Any] | None = None


class UpdateSessionRequest(BaseModel):
    """更新会话请求"""

    title: str | None = None
    context_config: dict[str, Any] | None = None
    capability_profile: dict[str, Any] | None = None
    state: str | None = None


class AttachRequest(BaseModel):
    """附着请求"""

    run_id: str | None = None
    task_id: str | None = None
    mode: str = AttachmentMode.ATTACHED_READONLY.value
    note: str | None = None


class SendMessageRequest(BaseModel):
    """发送消息请求"""

    role: str  # user / assistant / system
    content: str
    thinking: str | None = None
    meta: dict[str, Any] | None = None


class ExportRequest(BaseModel):
    """导出请求"""

    include_messages: bool = True
    format: str = "json"  # json / markdown


class AppendAuditEventRequest(BaseModel):
    """Append a role-session audit event."""

    event_type: str
    details: dict[str, Any] | None = None


class ExportToWorkflowRequest(BaseModel):
    """Request to export session to workflow"""

    target: Literal["pm", "director", "factory"]
    export_kind: Literal["session_bundle", "artifacts_only", "messages_only"] = "session_bundle"
    include_audit_log: bool = False


# ==================== Helper Functions ====================


def _record_value(record: Any, key: str, default: Any = None) -> Any:
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def _record_text(record: Any, key: str) -> str:
    value = _record_value(record, key, "")
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def _safe_records(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _ensure_workflow_export_runtime_ready(state: Any, target: str) -> None:
    """Fail closed before exporting role-session evidence into role workflows."""

    if target == "pm":
        ensure_required_roles_ready(state, default_roles=["pm"], force_first="pm")
    elif target == "director":
        ensure_required_roles_ready(state, default_roles=["director"], force_first="director")
    elif target == "factory":
        roles = ["pm", "chief_engineer", "director"]
        if bool(getattr(state.settings, "qa_enabled", True)):
            roles.append("qa")
        ensure_required_roles_ready(state, default_roles=roles, force_roles=roles)


def _non_negative_int(value: Any, fallback: int = 0) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str):
        token = value.strip()
        if token.isdigit():
            return int(token)
    return fallback


def _safe_export_token(value: str) -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value or "").strip())
    return token.strip("._") or "session"


def _serialize_artifact(artifact: Any) -> dict[str, Any]:
    return {
        "id": _record_text(artifact, "id"),
        "type": _record_text(artifact, "type"),
        "content": _record_value(artifact, "content", None),
        "metadata": _record_value(artifact, "metadata", {}) or {},
    }


def _serialize_message(message: Any) -> dict[str, Any]:
    to_dict = getattr(message, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, dict):
            return payload
    return {
        "id": _record_text(message, "id"),
        "role": _record_text(message, "role"),
        "content": _record_text(message, "content"),
        "thinking": _record_value(message, "thinking", None),
        "meta": _record_value(message, "meta", {}) or {},
        "created_at": _record_value(message, "created_at", None),
    }


def _serialize_audit_event(event: Any) -> dict[str, Any]:
    to_dict = getattr(event, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, dict):
            return payload
    if isinstance(event, dict):
        return dict(event)
    return {
        "event_type": _record_text(event, "event_type") or _record_text(event, "type"),
        "timestamp": _record_value(event, "timestamp", None),
        "details": _record_value(event, "details", {}) or {},
    }


def _message_directive_lines(messages: list[Any]) -> list[str]:
    lines: list[str] = []
    for message in messages:
        role = _record_text(message, "role") or "message"
        if role == "system":
            continue
        content = _record_text(message, "content")
        if not content:
            continue
        if len(content) > 700:
            content = content[:700] + "..."
        lines.append(f"{role}: {content}")
    return lines


def _build_directive_from_artifacts(artifacts: list[Any], messages: list[Any] | None = None) -> str:
    """Build a directive string from session artifacts and conversation messages.

    Args:
        artifacts: List of artifacts from the session
        messages: Optional RoleSession messages to preserve desktop dialogue intent

    Returns:
        Combined directive text
    """
    directives = []

    # Look for specific artifact types that contain directives
    for artifact in artifacts:
        content = _record_text(artifact, "content")
        artifact_type = _record_text(artifact, "type")

        # Prioritize certain artifact types
        if artifact_type in ("directive", "requirement", "goal"):
            directives.append(content)
        elif artifact_type in ("plan", "specification"):
            directives.append(f"Plan: {content}")

    # If no specific directives found, use all text artifacts
    if not directives:
        for artifact in artifacts:
            content = _record_text(artifact, "content")
            artifact_type = _record_text(artifact, "type")
            if artifact_type in ("message", "text", "code") and content:
                # Truncate long content
                if len(content) > 500:
                    content = content[:500] + "..."
                directives.append(content)

    if messages:
        directives.extend(_message_directive_lines(messages))

    return "\n\n".join(directives) if directives else "Continue from exported session"


def _build_task_filter_from_artifacts(artifacts: list[Any], messages: list[Any] | None = None) -> str:
    """Build a task filter from session artifacts and conversation messages.

    Args:
        artifacts: List of artifacts from the session
        messages: Optional RoleSession messages to preserve desktop dialogue intent

    Returns:
        Task filter string for Director
    """
    # Extract task-related artifacts
    tasks = []

    for artifact in artifacts:
        content = _record_text(artifact, "content")
        artifact_type = _record_text(artifact, "type")

        if artifact_type in ("task", "todo", "action_item"):
            tasks.append(content)

    if tasks:
        return "Execute tasks: " + "; ".join(tasks[:5])  # Limit to first 5 tasks

    # Fallback to using directive
    directive = _build_directive_from_artifacts(artifacts, messages)
    return directive[:200] if directive else "Execute ready tasks"


def _session_payload(session: Any) -> dict[str, Any]:
    to_dict = getattr(session, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, dict):
            return payload
    return {}


def _message_total(session: Any, messages: list[Any], offset: int) -> int:
    fallback = max(0, int(offset or 0)) + len(messages)
    payload = _session_payload(session)
    return max(_non_negative_int(payload.get("message_count"), fallback), fallback)


def _audit_total(audit_service: Any, session_id: str, event_type: str | None, events: list[Any], offset: int) -> int:
    fallback = max(0, int(offset or 0)) + len(events)
    get_event_count = getattr(audit_service, "get_event_count", None)
    if not callable(get_event_count):
        return fallback
    return max(_non_negative_int(get_event_count(session_id, event_type), fallback), fallback)


def _workspace_value(settings: Any) -> str:
    """Resolve active desktop workspace from compatible settings fields."""
    return active_workspace_value(settings)


def _active_workspace(request: Request) -> str:
    """Resolve active workspace from the request app state."""
    return _workspace_value(get_state(request).settings)


def _session_workspace(session: Any, request: Request) -> str:
    """Prefer persisted session workspace before active settings fallback."""
    workspace = str(getattr(session, "workspace", "") or "").strip()
    return workspace or _active_workspace(request)


def _workspace_path_for_session(session: Any, request: Request) -> Path:
    """Return workspace path for session-scoped evidence services."""
    return Path(_session_workspace(session, request))


def _role_session_service(request: Request) -> RoleSessionService:
    """Construct RoleSessionService bound to the active desktop workspace."""
    workspace = _active_workspace(request)
    return RoleSessionService(workspace=workspace or None)


# ==================== Session Endpoints ====================


@router.post("/v2/roles/sessions", dependencies=[Depends(require_auth)], response_model=SessionResponse)
async def create_session(
    request: Request,
    payload: CreateSessionRequest,
) -> dict[str, Any]:
    """创建新会话

    POST /v2/roles/sessions

    Request:
        {
            "role": "pm",
            "host_kind": "electron_workbench",
            "workspace": "/path/to/workspace",
            "session_type": "workbench",
            "attachment_mode": "isolated",
            "title": "My PM Session",
            "context_config": {},
            "capability_profile": {}
        }

    Response:
        {
            "ok": true,
            "session": {...}
        }
    """
    try:
        with _role_session_service(request) as service:
            session = service.create_session(
                role=payload.role,
                host_kind=payload.host_kind,
                workspace=payload.workspace or _active_workspace(request),
                session_type=payload.session_type,
                attachment_mode=payload.attachment_mode,
                title=payload.title,
                context_config=payload.context_config,
                capability_profile=payload.capability_profile,
            )

            return {
                "ok": True,
                "session": session.to_dict(),
            }

    except (RuntimeError, ValueError) as e:
        raise StructuredHTTPException(
            status_code=400,
            code="REQUEST_ERROR",
            message=str(e),
        ) from e


@router.get("/v2/roles/sessions", dependencies=[Depends(require_auth)], response_model=SessionListResponse)
async def list_sessions(
    request: Request,
    role: str | None = None,
    host_kind: str | None = None,
    workspace: str | None = None,
    session_type: str | None = None,
    state_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """列出会话

    GET /v2/roles/sessions?role=pm&host_kind=electron_workbench&...

    Response:
        {
            "ok": true,
            "sessions": [...],
            "total": 100
        }
    """
    try:
        with _role_session_service(request) as service:
            sessions = service.get_sessions(
                role=role,
                host_kind=host_kind,
                workspace=workspace or _active_workspace(request),
                session_type=session_type,
                state=state_filter,
                limit=limit,
                offset=offset,
            )

            return {
                "ok": True,
                "sessions": [s.to_dict() for s in sessions],
                "total": len(sessions),
            }

    except (RuntimeError, ValueError) as e:
        raise StructuredHTTPException(
            status_code=400,
            code="REQUEST_ERROR",
            message=str(e),
        ) from e


@router.get("/v2/roles/sessions/{session_id}", dependencies=[Depends(require_auth)], response_model=SessionResponse)
async def get_session(
    request: Request,
    session_id: str,
) -> dict[str, Any]:
    """获取会话详情

    GET /v2/roles/sessions/{session_id}

    Response:
        {
            "ok": true,
            "session": {...}
        }
    """
    try:
        with _role_session_service(request) as service:
            session = service.get_session(session_id)

            if not session:
                raise StructuredHTTPException(
                    status_code=404,
                    code="SESSION_NOT_FOUND",
                    message=f"Session not found: {session_id}",
                )

            return {
                "ok": True,
                "session": session.to_dict(),
            }

    except (RuntimeError, ValueError) as e:
        raise StructuredHTTPException(
            status_code=400,
            code="REQUEST_ERROR",
            message=str(e),
        ) from e


@router.put("/v2/roles/sessions/{session_id}", dependencies=[Depends(require_auth)], response_model=SessionResponse)
async def update_session(
    request: Request,
    session_id: str,
    payload: UpdateSessionRequest,
) -> dict[str, Any]:
    """更新会话

    PUT /v2/roles/sessions/{session_id}

    Request:
        {
            "title": "New Title",
            "context_config": {},
            "capability_profile": {},
            "state": "archived"
        }

    Response:
        {
            "ok": true,
            "session": {...}
        }
    """
    try:
        with _role_session_service(request) as service:
            session = service.update_session(
                session_id=session_id,
                title=payload.title,
                context_config=payload.context_config,
                capability_profile=payload.capability_profile,
                state=payload.state,
            )

            if not session:
                raise StructuredHTTPException(
                    status_code=404,
                    code="SESSION_NOT_FOUND",
                    message=f"Session not found: {session_id}",
                )

            return {
                "ok": True,
                "session": session.to_dict(),
            }

    except (RuntimeError, ValueError) as e:
        raise StructuredHTTPException(
            status_code=400,
            code="REQUEST_ERROR",
            message=str(e),
        ) from e


@router.delete(
    "/v2/roles/sessions/{session_id}",
    dependencies=[Depends(require_auth)],
    response_model=SessionDeleteResponse,
)
async def delete_session(
    request: Request,
    session_id: str,
    soft: bool = True,
) -> dict[str, Any]:
    """删除会话

    DELETE /v2/roles/sessions/{session_id}?soft=true

    Response:
        {
            "ok": true
        }
    """
    try:
        with _role_session_service(request) as service:
            success = service.delete_session(session_id, soft=soft)

            if not success:
                raise StructuredHTTPException(
                    status_code=404,
                    code="SESSION_NOT_FOUND",
                    message=f"Session not found: {session_id}",
                )

            return {
                "ok": True,
            }

    except (RuntimeError, ValueError) as e:
        raise StructuredHTTPException(
            status_code=400,
            code="REQUEST_ERROR",
            message=str(e),
        ) from e


# ==================== Message Endpoints ====================


@router.get(
    "/v2/roles/sessions/{session_id}/messages", dependencies=[Depends(require_auth)], response_model=MessageListResponse
)
async def get_messages(
    request: Request,
    session_id: str,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """获取会话消息

    GET /v2/roles/sessions/{session_id}/messages?limit=100&offset=0

    Response:
        {
            "ok": true,
            "messages": [...],
            "session": {...}
        }
    """
    try:
        with _role_session_service(request) as service:
            session = service.get_session(session_id)
            if not session:
                raise StructuredHTTPException(
                    status_code=404,
                    code="SESSION_NOT_FOUND",
                    message=f"Session not found: {session_id}",
                )

            messages = service.get_messages(session_id, limit=limit, offset=offset)
            session_payload = _session_payload(session)

            return {
                "ok": True,
                "messages": [m.to_dict() for m in messages],
                "session": session_payload,
                "total": _message_total(session, messages, offset),
            }

    except (RuntimeError, ValueError) as e:
        raise StructuredHTTPException(
            status_code=400,
            code="REQUEST_ERROR",
            message=str(e),
        ) from e


@router.post(
    "/v2/roles/sessions/{session_id}/messages", dependencies=[Depends(require_auth)], response_model=SessionResponse
)
async def send_message(
    request: Request,
    session_id: str,
    payload: SendMessageRequest,
) -> dict[str, Any]:
    """发送消息（非流式）

    POST /v2/roles/sessions/{session_id}/messages

    Request:
        {
            "role": "user",
            "content": "Hello PM",
            "thinking": null,
            "meta": {}
        }

    Response:
        {
            "ok": true,
            "session": {...}
        }
    """
    try:
        with _role_session_service(request) as service:
            session = service.add_message(
                session_id=session_id,
                role=payload.role,
                content=payload.content,
                thinking=payload.thinking,
                meta=payload.meta,
            )

            if not session:
                raise StructuredHTTPException(
                    status_code=404,
                    code="SESSION_NOT_FOUND",
                    message=f"Session not found: {session_id}",
                )

            return {
                "ok": True,
                "session": session.to_dict(),
            }

    except (RuntimeError, ValueError) as e:
        raise StructuredHTTPException(
            status_code=400,
            code="REQUEST_ERROR",
            message=str(e),
        ) from e


@router.post("/v2/roles/sessions/{session_id}/messages/stream", dependencies=[Depends(require_auth)])
async def send_message_stream(
    request: Request,
    session_id: str,
    payload: SendMessageRequest,
) -> Any:
    """Removed SSE endpoint; use the Nat-JetStream role-session endpoint."""
    del request, payload
    legacy_sse_removed(f"/v2/roles/sessions/{session_id}/messages/jetstream")


@router.post("/v2/roles/sessions/{session_id}/messages/jetstream", dependencies=[Depends(require_auth)])
async def send_message_jetstream(
    request: Request,
    session_id: str,
    payload: SendMessageRequest,
) -> dict[str, Any]:
    """Start a RoleSession chat turn and publish chunks over Nat-JetStream."""
    try:
        with _role_session_service(request) as service:
            session = service.get_session(session_id)
            if not session:
                raise StructuredHTTPException(
                    status_code=404,
                    code="session_not_found",
                    message=f"Session not found: {session_id}",
                    details={"session_id": session_id},
                )

            session_role = str(session.role or "").strip()
            prior_messages = service.get_messages(session_id, limit=50, offset=0)
            history = tuple(
                (
                    str(message.role or "").strip(),
                    str(message.content or "").strip(),
                )
                for message in prior_messages
                if str(message.role or "").strip() and str(message.content or "").strip()
            )
            context_config_raw = str(session.context_config or "").strip()
            session_workspace = _session_workspace(session, request)
            try:
                session_context = json.loads(context_config_raw) if context_config_raw else None
            except json.JSONDecodeError:
                logger.warning(
                    "Role session %s has invalid context_config JSON; falling back to None",
                    session_id,
                )
                session_context = None

            projection = await _SESSION_CONTINUITY_ENGINE.project(
                SessionContinuityRequest(
                    session_id=session_id,
                    role=session_role,
                    workspace=session_workspace,
                    session_title=str(session.title or "").strip(),
                    messages=history_pairs_to_messages(history),
                    session_context_config=session_context,
                    incoming_context={
                        "role": session_role,
                        "host_kind": RoleHostKind.API_SERVER.value,
                    },
                    history_limit=10,
                )
            )
            runtime_history = messages_to_history_pairs(projection.recent_messages)
            runtime_context = dict(projection.prompt_context)
            if projection.changed:
                service.update_session(
                    session_id=session_id,
                    context_config=projection.persisted_context_config,
                )

            service.add_message(
                session_id=session_id,
                role=payload.role,
                content=payload.content,
            )

        async def _run_role_session_jetstream_dialogue() -> None:
            response_parts: list[str] = []
            thinking_parts: list[str] = []
            assistant_saved = False

            async def _collect_and_persist(chunk: dict[str, Any]) -> None:
                nonlocal assistant_saved
                event_type = str(chunk.get("type") or "").strip()
                event_data = chunk.get("data")
                event_payload = event_data if isinstance(event_data, dict) else {}

                if event_type == "content_chunk":
                    response_parts.append(str(event_payload.get("content") or ""))
                    return

                if event_type == EVENT_TYPE_THINKING_CHUNK:
                    thinking_parts.append(str(event_payload.get("content") or ""))
                    return

                if event_type == EVENT_TYPE_COMPLETE:
                    response = str(event_payload.get("content") or "") or "".join(response_parts)
                    thinking = str(event_payload.get("thinking") or "") or "".join(thinking_parts) or None
                    if response:
                        with _role_session_service(request) as save_service:
                            save_service.add_message(
                                session_id=session_id,
                                role="assistant",
                                content=response,
                                thinking=thinking,
                            )
                        assistant_saved = True
                    return

                if event_type == "done" and response_parts and not assistant_saved:
                    with _role_session_service(request) as save_service:
                        save_service.add_message(
                            session_id=session_id,
                            role="assistant",
                            content="".join(response_parts),
                            thinking="".join(thinking_parts) or None,
                        )

            await execute_role_chat_jetstream(
                workspace=session_workspace,
                role=session_role,
                message=payload.content,
                payload=None,
                default_domain="general",
                host_kind="role_session_ws_jetstream",
                context=runtime_context,
                session_id=session_id,
                history=runtime_history,
                on_chunk=_collect_and_persist,
            )

        task = asyncio.create_task(_run_role_session_jetstream_dialogue())
        _track_role_session_jetstream_task(task)
        return {
            "ok": True,
            "session_id": session_id,
            "status": "started",
            "channel": f"chat:{session_id}",
            "subject": f"hp.runtime.chat.{session_id}",
            "transport": "nat-jetstream",
        }

    except HTTPException:
        raise
    except (RuntimeError, ValueError) as e:
        logger.error(f"Role session jetstream action failed: {e}")
        raise StructuredHTTPException(
            status_code=500,
            code="INTERNAL_ERROR",
            message="An internal error occurred while processing the role session.",
            details={"exception": str(e)},
        ) from e


# ==================== Attachment Endpoints ====================


@router.post(
    "/v2/roles/sessions/{session_id}/actions/attach",
    dependencies=[Depends(require_auth)],
    response_model=SessionResponse,
)
async def attach_session(
    request: Request,
    session_id: str,
    payload: AttachRequest,
) -> dict[str, Any]:
    """附着会话到工作流

    POST /v2/roles/sessions/{session_id}/actions/attach

    Request:
        {
            "run_id": "run_xxx",
            "task_id": "task_xxx",
            "mode": "attached_readonly",
            "note": "Attaching for review"
        }

    Response:
        {
            "ok": true,
            "attachment": {...}
        }
    """
    try:
        with _role_session_service(request) as service:
            attachment = service.attach_session(
                session_id=session_id,
                run_id=payload.run_id,
                task_id=payload.task_id,
                mode=payload.mode,
                note=payload.note,
            )

            if not attachment:
                raise StructuredHTTPException(
                    status_code=404,
                    code="SESSION_NOT_FOUND",
                    message=f"Session not found: {session_id}",
                )

            session = service.get_session(session_id)

            return {
                "ok": True,
                "attachment": attachment.to_dict(),
                "session": session.to_dict() if session else None,
            }

    except (RuntimeError, ValueError) as e:
        raise StructuredHTTPException(
            status_code=400,
            code="REQUEST_ERROR",
            message=str(e),
        ) from e


@router.post(
    "/v2/roles/sessions/{session_id}/actions/detach",
    dependencies=[Depends(require_auth)],
    response_model=SessionResponse,
)
async def detach_session(
    request: Request,
    session_id: str,
) -> dict[str, Any]:
    """解除会话的工作流附着

    POST /v2/roles/sessions/{session_id}/actions/detach

    Response:
        {
            "ok": true,
            "session": {...}
        }
    """
    try:
        with _role_session_service(request) as service:
            success = service.detach_session(session_id)

            if not success:
                raise StructuredHTTPException(
                    status_code=404,
                    code="SESSION_NOT_FOUND",
                    message=f"Session not found: {session_id}",
                )

            session = service.get_session(session_id)

            return {
                "ok": True,
                "session": session.to_dict() if session else None,
            }

    except (RuntimeError, ValueError) as e:
        raise StructuredHTTPException(
            status_code=400,
            code="REQUEST_ERROR",
            message=str(e),
        ) from e


# ==================== Export Endpoints ====================


@router.get(
    "/v2/roles/sessions/{session_id}/artifacts",
    dependencies=[Depends(require_auth)],
    response_model=ArtifactListResponse,
)
async def get_artifacts(
    request: Request,
    session_id: str,
    artifact_type: str | None = None,
) -> dict[str, Any]:
    """获取会话产物

    GET /v2/roles/sessions/{session_id}/artifacts?artifact_type=code

    Response:
        {
            "ok": true,
            "artifacts": [...]
        }
    """
    try:
        with _role_session_service(request) as service:
            session = service.get_session(session_id)
            if not session:
                raise StructuredHTTPException(
                    status_code=404,
                    code="SESSION_NOT_FOUND",
                    message=f"Session not found: {session_id}",
                )

        # Use artifact service to list artifacts
        artifact_service = RoleSessionArtifactService(_workspace_path_for_session(session, request))
        artifacts = artifact_service.list_artifacts(session_id, artifact_type)

        return {
            "ok": True,
            "artifacts": [a.to_dict() for a in artifacts],
            "total": len(artifacts),
        }

    except (RuntimeError, ValueError) as e:
        raise StructuredHTTPException(
            status_code=400,
            code="REQUEST_ERROR",
            message=str(e),
        ) from e


@router.get(
    "/v2/roles/sessions/{session_id}/audit", dependencies=[Depends(require_auth)], response_model=AuditLogResponse
)
async def get_audit(
    request: Request,
    session_id: str,
    event_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """获取会话审计日志

    GET /v2/roles/sessions/{session_id}/audit?event_type=message_sent&limit=100&offset=0

    Response:
        {
            "ok": true,
            "audit_events": [...]
        }
    """
    try:
        with _role_session_service(request) as service:
            session = service.get_session(session_id)
            if not session:
                raise StructuredHTTPException(
                    status_code=404,
                    code="SESSION_NOT_FOUND",
                    message=f"Session not found: {session_id}",
                )

        # Use audit service to get events
        audit_service = RoleSessionAuditService(_workspace_path_for_session(session, request))
        events = audit_service.get_events(session_id, event_type, limit, offset)

        return {
            "ok": True,
            "audit_events": events,
            "total": _audit_total(audit_service, session_id, event_type, events, offset),
        }

    except (RuntimeError, ValueError) as e:
        raise StructuredHTTPException(
            status_code=400,
            code="REQUEST_ERROR",
            message=str(e),
        ) from e


@router.post("/v2/roles/sessions/{session_id}/audit/events", dependencies=[Depends(require_auth)])
async def append_audit_event(
    request: Request,
    session_id: str,
    payload: AppendAuditEventRequest,
) -> dict[str, Any]:
    """Append a role-session audit event without invoking a workflow."""
    event_type = str(payload.event_type or "").strip()
    if event_type not in ROLE_SESSION_AUDIT_EVENT_TYPES:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_AUDIT_EVENT_TYPE",
            message=f"Unsupported role-session audit event type: {event_type}",
        )

    try:
        with _role_session_service(request) as service:
            session = service.get_session(session_id)
            if not session:
                raise StructuredHTTPException(
                    status_code=404,
                    code="SESSION_NOT_FOUND",
                    message=f"Session not found: {session_id}",
                )

        audit_service = RoleSessionAuditService(_workspace_path_for_session(session, request))
        event = audit_service.append_audit_event(
            session_id=session_id,
            event_type=event_type,
            details=payload.details or {},
        )
        return {
            "ok": True,
            "session_id": session_id,
            "event": event,
        }
    except (RuntimeError, ValueError) as e:
        raise StructuredHTTPException(
            status_code=400,
            code="REQUEST_ERROR",
            message=str(e),
        ) from e


@router.post("/v2/roles/sessions/{session_id}/audit/export", dependencies=[Depends(require_auth)])
async def export_audit_log(
    request: Request,
    session_id: str,
) -> dict[str, Any]:
    """Export a role-session audit log without starting a workflow."""
    try:
        with _role_session_service(request) as service:
            session = service.get_session(session_id)
            if not session:
                raise StructuredHTTPException(
                    status_code=404,
                    code="SESSION_NOT_FOUND",
                    message=f"Session not found: {session_id}",
                )

        session_workspace = _session_workspace(session, request)
        audit_service = RoleSessionAuditService(Path(session_workspace))

        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        workspace_root = Path(session_workspace).resolve()
        export_dir = workspace_root / get_workspace_metadata_dir_name() / "exports" / "role_sessions"
        export_dir.mkdir(parents=True, exist_ok=True)
        export_target = export_dir / f"{_safe_export_token(session_id)}.audit.json"
        export_path = audit_service.export_audit_log(session_id, export_target)
        event_count = audit_service.get_event_count(session_id, None)

        return {
            "ok": True,
            "session_id": session_id,
            "export_path": str(export_path),
            "event_count": event_count,
        }
    except (RuntimeError, ValueError) as e:
        raise StructuredHTTPException(
            status_code=400,
            code="REQUEST_ERROR",
            message=str(e),
        ) from e


# ==================== Context OS Memory Endpoints ====================


@router.get(
    "/v2/roles/sessions/{session_id}/memory/search",
    dependencies=[Depends(require_auth)],
    response_model=MemorySearchResponse,
)
async def search_session_memory(
    request: Request,
    session_id: str,
    q: str,
    kind: str | None = None,
    entity: str | None = None,
    limit: int = 6,
) -> dict[str, Any]:
    """Search persisted Context OS memory for one role session."""
    try:
        query = SearchRoleSessionMemoryQueryV1(
            session_id=session_id,
            query=q,
            kind=kind,
            entity=entity,
            limit=limit,
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_QUERY",
            message=str(exc),
        ) from exc

    with RoleSessionContextMemoryService() as service:
        result = service.search_memory(query)

    if not result.ok:
        raise StructuredHTTPException(
            status_code=400,
            code=result.error_code or "SEARCH_MEMORY_FAILED",
            message=result.error_message or "search_memory failed",
        )

    items = list(result.payload or [])
    return {
        "ok": True,
        "session_id": session_id,
        "query": q,
        "kind": kind,
        "entity": entity,
        "total": len(items),
        "items": items,
    }


@router.get(
    "/v2/roles/sessions/{session_id}/memory/artifacts/{artifact_id}",
    dependencies=[Depends(require_auth)],
    response_model=ArtifactDetailResponse,
)
async def read_session_memory_artifact(
    request: Request,
    session_id: str,
    artifact_id: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict[str, Any]:
    """Read one persisted Context OS artifact for one role session."""
    try:
        query = ReadRoleSessionArtifactQueryV1(
            session_id=session_id,
            artifact_id=artifact_id,
            start_line=start_line,
            end_line=end_line,
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_QUERY",
            message=str(exc),
        ) from exc

    with RoleSessionContextMemoryService() as service:
        result = service.read_artifact(query)

    if not result.ok:
        raise StructuredHTTPException(
            status_code=400,
            code=result.error_code or "READ_ARTIFACT_FAILED",
            message=result.error_message or "read_artifact failed",
        )

    return {
        "ok": True,
        "session_id": session_id,
        "artifact": dict(result.payload or {}),
    }


@router.get(
    "/v2/roles/sessions/{session_id}/memory/episodes/{episode_id}",
    dependencies=[Depends(require_auth)],
    response_model=EpisodeDetailResponse,
)
async def read_session_memory_episode(
    request: Request,
    session_id: str,
    episode_id: str,
) -> dict[str, Any]:
    """Read one persisted Context OS episode for one role session."""
    try:
        query = ReadRoleSessionEpisodeQueryV1(
            session_id=session_id,
            episode_id=episode_id,
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_QUERY",
            message=str(exc),
        ) from exc

    with RoleSessionContextMemoryService() as service:
        result = service.read_episode(query)

    if not result.ok:
        raise StructuredHTTPException(
            status_code=400,
            code=result.error_code or "READ_EPISODE_FAILED",
            message=result.error_message or "read_episode failed",
        )

    return {
        "ok": True,
        "session_id": session_id,
        "episode": dict(result.payload or {}),
    }


@router.get(
    "/v2/roles/sessions/{session_id}/memory/state",
    dependencies=[Depends(require_auth)],
    response_model=MemoryStateResponse,
)
async def read_session_memory_state(
    request: Request,
    session_id: str,
    path: str,
) -> dict[str, Any]:
    """Read one persisted Context OS state entry for one role session."""
    try:
        query = GetRoleSessionStateQueryV1(
            session_id=session_id,
            path=path,
        )
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_QUERY",
            message=str(exc),
        ) from exc

    with RoleSessionContextMemoryService() as service:
        result = service.get_state(query)

    if not result.ok:
        raise StructuredHTTPException(
            status_code=400,
            code=result.error_code or "GET_STATE_FAILED",
            message=result.error_message or "get_state failed",
        )

    return {
        "ok": True,
        "session_id": session_id,
        "path": path,
        "value": result.payload,
    }


@router.post(
    "/v2/roles/sessions/{session_id}/actions/export",
    dependencies=[Depends(require_auth)],
    response_model=SessionExportResponse,
)
async def export_session(
    request: Request,
    session_id: str,
    payload: ExportRequest,
) -> dict[str, Any]:
    """导出会话

    POST /v2/roles/sessions/{session_id}/actions/export

    Request:
        {
            "include_messages": true,
            "format": "json"
        }

    Response:
        {
            "ok": true,
            "export": {...}
        }
    """
    try:
        with _role_session_service(request) as service:
            export_data = service.export_session(
                session_id,
                include_messages=payload.include_messages,
            )

            if not export_data:
                raise StructuredHTTPException(
                    status_code=404,
                    code="SESSION_NOT_FOUND",
                    message=f"Session not found: {session_id}",
                )

            if payload.format == "markdown":
                # 转换为 Markdown 格式
                md = f"# {export_data.get('title', 'Session Export')}\n\n"
                md += f"- Role: {export_data.get('role')}\n"
                md += f"- Host: {export_data.get('host_kind')}\n"
                md += f"- Created: {export_data.get('created_at')}\n\n"

                if export_data.get("messages"):
                    md += "## Messages\n\n"
                    for msg in export_data["messages"]:
                        md += f"### {msg['role']}\n\n"
                        md += f"{msg['content']}\n\n"

                export_data = {"markdown": md}

            return {
                "ok": True,
                "export": export_data,
            }

    except (RuntimeError, ValueError) as e:
        raise StructuredHTTPException(
            status_code=400,
            code="REQUEST_ERROR",
            message=str(e),
        ) from e


# ==================== Export to Workflow Endpoint ====================


@router.post(
    "/v2/roles/sessions/{session_id}/actions/export-to-workflow",
    dependencies=[Depends(require_auth)],
    response_model=WorkflowExportResponse,
)
async def export_to_workflow(
    request: Request,
    session_id: str,
    payload: ExportToWorkflowRequest,
) -> dict[str, Any]:
    """Export session to workflow

    POST /v2/roles/sessions/{session_id}/actions/export-to-workflow

    Request:
        {
            "target": "pm",  // or "director", "factory"
            "export_kind": "session_bundle",  // or "artifacts_only", "messages_only"
            "include_audit_log": false
        }

    Response:
        {
            "ok": true,
            "exported_to": "pm",
            "run_id": "pm_export_xxx",
            "session_id": "xxx",
            "artifact_count": 5
        }
    """
    state = get_state(request)

    try:
        include_artifacts = payload.export_kind in {"session_bundle", "artifacts_only"}
        include_messages = payload.export_kind in {"session_bundle", "messages_only"}

        # Verify session exists
        with _role_session_service(request) as service:
            session = service.get_session(session_id)
            if not session:
                raise StructuredHTTPException(
                    status_code=404,
                    code="SESSION_NOT_FOUND",
                    message=f"Session not found: {session_id}",
                )
            messages = _safe_records(service.get_messages(session_id, limit=200, offset=0)) if include_messages else []

        _ensure_workflow_export_runtime_ready(state, payload.target)

        session_workspace = _session_workspace(session, request)
        artifact_service = RoleSessionArtifactService(Path(session_workspace))
        audit_service = RoleSessionAuditService(Path(session_workspace))

        # 1. Collect session content
        artifacts = _safe_records(artifact_service.list_artifacts(session_id)) if include_artifacts else []
        events = _safe_records(audit_service.get_events(session_id, limit=1000)) if payload.include_audit_log else []

        # 2. Build export bundle
        export_bundle = {
            "session_id": session_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "target": payload.target,
            "export_kind": payload.export_kind,
            "artifacts": [_serialize_artifact(artifact) for artifact in artifacts],
            "messages": [_serialize_message(message) for message in messages],
            "audit_events": [_serialize_audit_event(event) for event in events],
            "artifact_count": len(artifacts),
            "message_count": len(messages),
            "event_count": len(events),
        }

        # 3. Persist export bundle and create target workflow
        import json

        from polaris.infrastructure.storage import LocalFileSystemAdapter
        from polaris.kernelone.fs import KernelFileSystem

        workspace_root = Path(session_workspace).resolve()
        kernel_fs = KernelFileSystem(str(workspace_root), LocalFileSystemAdapter())
        export_timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        export_filename = f"{session_id}_{export_timestamp}_export.json"
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        export_path = workspace_root / get_workspace_metadata_dir_name() / "exports" / export_filename
        export_rel_path = kernel_fs.to_workspace_relative_path(str(export_path))
        kernel_fs.workspace_write_text(
            export_rel_path,
            json.dumps(export_bundle, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        export_path = workspace_root / export_rel_path

        if payload.target == "pm":
            # Export to PM workflow via OrchestrationCommandService
            from polaris.cells.orchestration.pm_dispatch.public.service import OrchestrationCommandService

            cmd_service = OrchestrationCommandService(state.settings)
            directive = _build_directive_from_artifacts(artifacts, messages)

            result = await cmd_service.execute_pm_run(
                workspace=session_workspace,
                run_type="full",
                options={
                    "directive": directive,
                    "run_director": False,
                    "export_session_id": session_id,
                    "export_bundle_path": str(export_path),
                },
            )
            run_id = result.run_id

        elif payload.target == "director":
            # Export to Director workflow via OrchestrationCommandService
            from polaris.cells.orchestration.pm_dispatch.public.service import OrchestrationCommandService

            cmd_service = OrchestrationCommandService(state.settings)
            task_filter = _build_task_filter_from_artifacts(artifacts, messages)

            result = await cmd_service.execute_director_run(
                workspace=session_workspace,
                options={
                    "task_filter": task_filter,
                    "max_workers": DEFAULT_DIRECTOR_MAX_PARALLELISM,
                    "execution_mode": "parallel",
                    "export_session_id": session_id,
                    "export_bundle_path": str(export_path),
                },
            )
            run_id = result.run_id

        else:
            # Export to Factory via FactoryRunService
            from polaris.cells.factory.pipeline.public.service import FactoryConfig, FactoryRunService
            from polaris.cells.factory.pipeline.public.types import FactoryStartRequest
            from polaris.delivery.http.routers.factory import _schedule_factory_run_task

            factory_service = FactoryRunService(workspace=Path(session_workspace))
            directive = _build_directive_from_artifacts(artifacts, messages)
            start_payload = FactoryStartRequest(
                workspace=session_workspace,
                start_from="architect",
                directive=directive,
                run_director=True,
                director_iterations=1,
                loop=False,
                input_source="role_session",
            )

            config = FactoryConfig(
                name=f"export_from_{session_id}",
                description=directive or f"Factory run exported from session {session_id}",
                stages=[
                    "docs_generation",
                    "pm_planning",
                    "chief_engineer_review",
                    "director_dispatch",
                    "quality_gate",
                ],
                auto_dispatch=True,
            )

            run = await factory_service.create_run(config)
            run_id = run.id

            # Start the factory run
            await factory_service.start_run(run_id)

            # Record export reference in metadata
            await factory_service.update_run_metadata(
                run_id,
                {
                    "export_session_id": session_id,
                    "export_bundle_path": str(export_path),
                    "directive": directive,
                    "input_source": "role_session",
                    "factory_start_request": start_payload.model_dump(mode="json"),
                },
            )
            _schedule_factory_run_task(factory_service, run_id, start_payload, state)

        # 4. Record export event
        audit_service.append_audit_event(
            session_id=session_id,
            event_type="workflow_exported",
            details={
                "target": payload.target,
                "run_id": run_id,
                "artifact_count": len(artifacts),
                "message_count": len(messages),
                "export_kind": payload.export_kind,
            },
        )

        return {
            "ok": True,
            "exported_to": payload.target,
            "run_id": run_id,
            "session_id": session_id,
            "artifact_count": len(artifacts),
            "message_count": len(messages),
        }

    except (RuntimeError, ValueError) as e:
        raise StructuredHTTPException(
            status_code=400,
            code="REQUEST_ERROR",
            message=str(e),
        ) from e


# ==================== Capabilities Endpoint ====================


@router.get(
    "/v2/roles/capabilities/{role}",
    dependencies=[Depends(require_auth)],
    response_model=RoleCapabilitiesResponse,
)
async def get_role_capabilities(
    request: Request,
    role: str,
    host_kind: str | None = None,
) -> dict[str, Any]:
    """获取角色能力配置

    GET /v2/roles/capabilities/{role}?host_kind=electron_workbench

    Response:
        {
            "ok": true,
            "role": "pm",
            "capabilities": {
                "electron_workbench": [...],
                "workflow": [...],
                ...
            }
        }
    """
    try:
        capabilities = get_caps(role, host_kind)

        return {
            "ok": True,
            "role": role,
            "capabilities": capabilities,
        }

    except (RuntimeError, ValueError) as e:
        raise StructuredHTTPException(
            status_code=400,
            code="REQUEST_ERROR",
            message=str(e),
        ) from e
