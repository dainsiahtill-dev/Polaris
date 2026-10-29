"""Role Session Router - 统一角色会话 API

提供完整的 RoleSession 管理接口：
- Session CRUD
- 消息发送/接收
- 附着管理
- 产物导出

这是 Polaris 角色多宿主架构的核心 API。
"""

import json
import logging
from pathlib import Path
from typing import Any, Literal, cast

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
from polaris.delivery.http.legacy_engine_retired import legacy_engine_retired
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
from pydantic import BaseModel

from ._shared import StructuredHTTPException, ensure_required_roles_ready, get_state, require_auth

logger = logging.getLogger(__name__)

router = APIRouter()
ROLE_SESSION_AUDIT_EVENT_TYPES = frozenset(RoleSessionAuditService.EVENT_TYPES)


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
    return cast("str", (active_workspace_value(settings)))


def _active_workspace(request: Request) -> str:
    """Resolve active workspace from the request app state."""
    return _workspace_value(get_state(request).settings)


def _canonical_workspace_path(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(raw).expanduser().resolve())
    except OSError:
        return str(Path(raw).expanduser().absolute())


def _validated_requested_workspace(active_workspace: str, requested_workspace: str | None) -> str:
    active = str(active_workspace or "").strip()
    requested = str(requested_workspace or "").strip()
    if not requested:
        return active
    if not active:
        return requested
    if _canonical_workspace_path(requested) != _canonical_workspace_path(active):
        raise StructuredHTTPException(
            status_code=400,
            code="WORKSPACE_MISMATCH",
            message=(
                "Role session workspace must match the active workspace; "
                "switch workspace first or omit payload.workspace."
            ),
        )
    return active


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
    requested = str(request.query_params.get("workspace") or "").strip()
    if requested and _canonical_workspace_path(requested) != _canonical_workspace_path(workspace):
        raise HTTPException(status_code=404, detail="Workspace not found")
    return RoleSessionService(
        workspace=workspace or None, read_only=True, enable_events=False, enable_persistence=False
    )


# ==================== Session Endpoints ====================


@router.post("/v2/roles/sessions", dependencies=[Depends(require_auth)], response_model=SessionResponse)
async def create_session(
    request: Request,
    payload: CreateSessionRequest,
) -> dict[str, Any]:
    """Reject retired RoleSession mutations before storage access."""
    raise legacy_engine_retired("RoleSession create_session")


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
    """Reject retired RoleSession mutations before storage access."""
    raise legacy_engine_retired("RoleSession update_session")


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
    """Reject retired RoleSession mutations before storage access."""
    raise legacy_engine_retired("RoleSession delete_session")


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
    """Reject retired RoleSession mutations before storage access."""
    raise legacy_engine_retired("RoleSession send_message")


@router.post("/v2/roles/sessions/{session_id}/messages/jetstream", dependencies=[Depends(require_auth)])
async def send_message_jetstream(
    request: Request,
    session_id: str,
    payload: SendMessageRequest,
) -> dict[str, Any]:
    """Reject retired RoleSession mutations before storage access."""
    raise legacy_engine_retired("RoleSession send_message_jetstream")


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
    """Reject retired RoleSession mutations before storage access."""
    raise legacy_engine_retired("RoleSession attach_session")


@router.post(
    "/v2/roles/sessions/{session_id}/actions/detach",
    dependencies=[Depends(require_auth)],
    response_model=SessionResponse,
)
async def detach_session(
    request: Request,
    session_id: str,
) -> dict[str, Any]:
    """Reject retired RoleSession mutations before storage access."""
    raise legacy_engine_retired("RoleSession detach_session")


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
        artifact_service = RoleSessionArtifactService(_workspace_path_for_session(session, request), read_only=True)
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
        audit_service = RoleSessionAuditService(_workspace_path_for_session(session, request), read_only=True)
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
    """Reject retired RoleSession mutations before storage access."""
    raise legacy_engine_retired("RoleSession append_audit_event")


@router.post("/v2/roles/sessions/{session_id}/audit/export", dependencies=[Depends(require_auth)])
async def export_audit_log(
    request: Request,
    session_id: str,
) -> dict[str, Any]:
    """Reject retired RoleSession mutations before storage access."""
    raise legacy_engine_retired("RoleSession export_audit_log")


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

    with (
        _role_session_service(request) as historical_service,
        RoleSessionContextMemoryService(session_service=historical_service) as service,
    ):
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

    with (
        _role_session_service(request) as historical_service,
        RoleSessionContextMemoryService(session_service=historical_service) as service,
    ):
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

    with (
        _role_session_service(request) as historical_service,
        RoleSessionContextMemoryService(session_service=historical_service) as service,
    ):
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

    with (
        _role_session_service(request) as historical_service,
        RoleSessionContextMemoryService(session_service=historical_service) as service,
    ):
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
    """Reject retired RoleSession mutations before storage access."""
    raise legacy_engine_retired("RoleSession export_session")


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
    """Reject retired RoleSession mutations before storage access."""
    raise legacy_engine_retired("RoleSession export_to_workflow")


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
