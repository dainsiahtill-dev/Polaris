"""Agent Router - canonical session/runtime wrapper.

This router preserves the V1 agent-facing HTTP shape, but the implementation
now routes through canonical `roles.session` + `roles.runtime` services
instead of maintaining a private in-memory session model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from polaris.cells.roles.session.public import (
    RoleHostKind,
    RoleSessionContextMemoryService,
    RoleSessionService,
    SessionState,
)
from polaris.cells.roles.session.public.contracts import (
    GetRoleSessionStateQueryV1,
    ReadRoleSessionArtifactQueryV1,
    ReadRoleSessionEpisodeQueryV1,
    SearchRoleSessionMemoryQueryV1,
)
from polaris.delivery.http.legacy_engine_retired import legacy_engine_retired
from polaris.delivery.http.workspace import active_workspace_value
from pydantic import BaseModel, Field

from ._shared import StructuredHTTPException, get_state, require_auth

router = APIRouter(prefix="/agent", tags=["agent"])

_AGENT_CONTEXT_FLAG = "agent_router_v1"
_AGENT_SESSION_SCAN_LIMIT = 200


class SessionMessageRequest(BaseModel):
    model_config = {"extra": "forbid"}
    message: str = Field(..., min_length=1, max_length=20000)
    role: Literal["pm", "architect", "chief_engineer", "director", "qa", "assistant"] = "assistant"


class AgentTurnPayload(BaseModel):
    model_config = {"extra": "forbid"}
    session_id: str | None = Field(default=None, max_length=128)
    workspace: str | None = Field(default=None, max_length=4096)
    message: str = Field(..., min_length=1, max_length=20000)
    role: Literal["pm", "architect", "chief_engineer", "director", "qa", "assistant"] = "assistant"
    stream: bool = False


def _agent_workspace(request: Request) -> str:
    workspace = active_workspace_value(get_state(request).settings)
    requested = str(request.query_params.get("workspace") or "").strip()
    if requested and Path(requested).resolve() != Path(workspace).resolve():
        raise StructuredHTTPException(status_code=404, code="WORKSPACE_NOT_FOUND", message="Workspace not found")
    return str(Path(workspace).resolve())


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_session_context(raw: Any) -> dict[str, Any]:
    return dict(raw) if isinstance(raw, dict) else {}


def _is_agent_session_payload(payload: dict[str, Any]) -> bool:
    context = _normalize_session_context(payload.get("context_config"))
    return bool(context.get(_AGENT_CONTEXT_FLAG))


def _message_history_pairs(messages: list[dict[str, Any]]) -> tuple[tuple[str, str], ...]:
    history: list[tuple[str, str]] = []
    for item in messages:
        role = _safe_text(item.get("role"))
        content = _safe_text(item.get("content"))
        if role and content:
            history.append((role, content))
    return tuple(history)


def _extract_recent_tool_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for message in messages[-10:]:
        meta = message.get("meta")
        if not isinstance(meta, dict):
            continue
        tool_calls = meta.get("tool_calls")
        if isinstance(tool_calls, list):
            tools.extend(item for item in tool_calls if isinstance(item, dict))
    return tools[-5:]


def _extract_failure_summary(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    errors: list[str] = []
    for message in messages:
        role = _safe_text(message.get("role")).lower()
        content = _safe_text(message.get("content"))
        if role in {"system", "assistant"} and "error" in content.lower():
            errors.append(content)
    if not errors:
        return None
    return {
        "count": len(errors),
        "last_error": errors[-1][:200],
    }


def _build_agent_session_payload(
    *,
    session_payload: dict[str, Any],
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    context_config = _normalize_session_context(session_payload.get("context_config"))
    return {
        "session_id": _safe_text(session_payload.get("id")),
        "workspace": session_payload.get("workspace"),
        "created_at": session_payload.get("created_at"),
        "updated_at": session_payload.get("updated_at"),
        "message_count": int(session_payload.get("message_count") or len(messages)),
        "role": _safe_text(session_payload.get("role")) or "assistant",
        "context": context_config,
        "history": [
            {
                "role": _safe_text(item.get("role")),
                "content": _safe_text(item.get("content")),
                "thinking": item.get("thinking"),
                "meta": item.get("meta") if isinstance(item.get("meta"), dict) else {},
            }
            for item in messages
        ],
        "recent_tools": _extract_recent_tool_calls(messages),
        "failure_summary": _extract_failure_summary(messages),
    }


def _load_agent_session(session_id: str, *, workspace: str | None = None) -> dict[str, Any] | None:
    if not workspace:
        return None
    with RoleSessionService(
        workspace=workspace, read_only=True, enable_persistence=False, enable_events=False
    ) as service:
        session = service.get_session(session_id)
        if session is None:
            return None
        payload = session.to_dict(include_messages=False)
        if not _is_agent_session_payload(payload):
            return None
        messages = [message.to_dict() for message in service.get_messages(str(session.id), limit=200, offset=0)]
        return _build_agent_session_payload(session_payload=payload, messages=messages)


def _get_or_create_session(workspace: str, role: str, session_id: str | None = None) -> dict[str, Any]:
    """Reject retired execution before preflight, storage or scheduling."""
    raise legacy_engine_retired("Legacy _get_or_create_session")


@router.get("/sessions", dependencies=[Depends(require_auth)])
async def list_agent_sessions(
    request: Request,
    limit: int = 20,
) -> dict[str, Any]:
    get_state(request)
    with RoleSessionService(
        workspace=_agent_workspace(request), read_only=True, enable_persistence=False, enable_events=False
    ) as service:
        candidate_sessions = service.get_sessions(
            host_kind=RoleHostKind.API_SERVER.value,
            session_type="standalone",
            state=SessionState.ACTIVE.value,
            limit=max(limit * 5, _AGENT_SESSION_SCAN_LIMIT),
        )
        payloads: list[dict[str, Any]] = []
        for session in candidate_sessions:
            session_payload = session.to_dict(include_messages=False)
            if not _is_agent_session_payload(session_payload):
                continue
            payloads.append(
                _build_agent_session_payload(
                    session_payload=session_payload,
                    messages=[
                        message.to_dict() for message in service.get_messages(str(session.id), limit=20, offset=0)
                    ],
                )
            )
            if len(payloads) >= limit:
                break
    return {
        "sessions": payloads,
        "total": len(payloads),
    }


@router.get("/sessions/{session_id}", dependencies=[Depends(require_auth)])
async def get_agent_session(
    request: Request,
    session_id: str,
) -> dict[str, Any]:
    get_state(request)
    session = _load_agent_session(session_id, workspace=_agent_workspace(request))
    if session is None:
        raise StructuredHTTPException(
            status_code=404,
            code="SESSION_NOT_FOUND",
            message="Session not found",
        )
    return session


@router.get("/sessions/{session_id}/memory/search", dependencies=[Depends(require_auth)])
async def search_agent_session_memory(
    request: Request,
    session_id: str,
    q: str,
    kind: str | None = None,
    entity: str | None = None,
    limit: int = 6,
) -> dict[str, Any]:
    get_state(request)
    if _load_agent_session(session_id, workspace=_agent_workspace(request)) is None:
        raise StructuredHTTPException(
            status_code=404,
            code="SESSION_NOT_FOUND",
            message=f"Session {session_id} not found",
        )
    try:
        query = SearchRoleSessionMemoryQueryV1(
            session_id=session_id,
            query=q,
            kind=kind,
            entity=entity,
            limit=limit,
        )
    except ValueError:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_QUERY",
            message="Invalid query parameters",
        ) from None

    with (
        RoleSessionService(
            workspace=_agent_workspace(request), read_only=True, enable_events=False, enable_persistence=False
        ) as historical_service,
        RoleSessionContextMemoryService(session_service=historical_service) as service,
    ):
        result = service.search_memory(query)

    if not result.ok:
        return {
            "ok": False,
            "error_code": result.error_code,
            "error": result.error_message or "search_memory failed",
        }

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


@router.get("/sessions/{session_id}/memory/artifacts/{artifact_id}", dependencies=[Depends(require_auth)])
async def read_agent_session_memory_artifact(
    request: Request,
    session_id: str,
    artifact_id: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict[str, Any]:
    get_state(request)
    if _load_agent_session(session_id, workspace=_agent_workspace(request)) is None:
        raise StructuredHTTPException(
            status_code=404,
            code="SESSION_NOT_FOUND",
            message=f"Session {session_id} not found",
        )
    try:
        query = ReadRoleSessionArtifactQueryV1(
            session_id=session_id,
            artifact_id=artifact_id,
            start_line=start_line,
            end_line=end_line,
        )
    except ValueError:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_QUERY",
            message="Invalid query parameters",
        ) from None

    with (
        RoleSessionService(
            workspace=_agent_workspace(request), read_only=True, enable_events=False, enable_persistence=False
        ) as historical_service,
        RoleSessionContextMemoryService(session_service=historical_service) as service,
    ):
        result = service.read_artifact(query)

    if not result.ok:
        return {
            "ok": False,
            "error_code": result.error_code,
            "error": result.error_message or "read_artifact failed",
        }

    return {
        "ok": True,
        "session_id": session_id,
        "artifact": dict(result.payload or {}),
    }


@router.get("/sessions/{session_id}/memory/episodes/{episode_id}", dependencies=[Depends(require_auth)])
async def read_agent_session_memory_episode(
    request: Request,
    session_id: str,
    episode_id: str,
) -> dict[str, Any]:
    get_state(request)
    if _load_agent_session(session_id, workspace=_agent_workspace(request)) is None:
        raise StructuredHTTPException(
            status_code=404,
            code="SESSION_NOT_FOUND",
            message=f"Session {session_id} not found",
        )
    try:
        query = ReadRoleSessionEpisodeQueryV1(
            session_id=session_id,
            episode_id=episode_id,
        )
    except ValueError:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_QUERY",
            message="Invalid query parameters",
        ) from None

    with (
        RoleSessionService(
            workspace=_agent_workspace(request), read_only=True, enable_events=False, enable_persistence=False
        ) as historical_service,
        RoleSessionContextMemoryService(session_service=historical_service) as service,
    ):
        result = service.read_episode(query)

    if not result.ok:
        return {
            "ok": False,
            "error_code": result.error_code,
            "error": result.error_message or "read_episode failed",
        }

    return {
        "ok": True,
        "session_id": session_id,
        "episode": dict(result.payload or {}),
    }


@router.get("/sessions/{session_id}/memory/state", dependencies=[Depends(require_auth)])
async def read_agent_session_memory_state(
    request: Request,
    session_id: str,
    path: str,
) -> dict[str, Any]:
    get_state(request)
    if _load_agent_session(session_id, workspace=_agent_workspace(request)) is None:
        raise StructuredHTTPException(
            status_code=404,
            code="SESSION_NOT_FOUND",
            message=f"Session {session_id} not found",
        )
    try:
        query = GetRoleSessionStateQueryV1(
            session_id=session_id,
            path=path,
        )
    except ValueError:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_QUERY",
            message="Invalid query parameters",
        ) from None

    with (
        RoleSessionService(
            workspace=_agent_workspace(request), read_only=True, enable_events=False, enable_persistence=False
        ) as historical_service,
        RoleSessionContextMemoryService(session_service=historical_service) as service,
    ):
        result = service.get_state(query)

    if not result.ok:
        return {
            "ok": False,
            "error_code": result.error_code,
            "error": result.error_message or "get_state failed",
        }

    return {
        "ok": True,
        "session_id": session_id,
        "path": path,
        "value": result.payload,
    }


@router.post("/sessions/{session_id}/messages", dependencies=[Depends(require_auth)])
async def send_agent_message(
    request: Request,
    session_id: str,
    payload: SessionMessageRequest,
) -> dict[str, Any]:
    """Reject retired execution before preflight, storage or scheduling."""
    raise legacy_engine_retired("Legacy send_agent_message")


@router.delete("/sessions/{session_id}", dependencies=[Depends(require_auth)])
async def delete_agent_session(
    request: Request,
    session_id: str,
) -> dict[str, Any]:
    """Reject retired execution before preflight, storage or scheduling."""
    raise legacy_engine_retired("Legacy delete_agent_session")


@router.post("/turn", dependencies=[Depends(require_auth)])
async def agent_turn(
    request: Request,
    payload: AgentTurnPayload,
) -> dict[str, Any]:
    """Reject retired execution before preflight, storage or scheduling."""
    raise legacy_engine_retired("Legacy agent_turn")
