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
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from polaris.cells.audit.evidence.public.service import RoleSessionAuditService
from polaris.cells.llm.dialogue.public.service import generate_role_response_streaming
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
    EVENT_TYPE_ERROR,
    EVENT_TYPE_FINGERPRINT,
    EVENT_TYPE_THINKING_CHUNK,
    EVENT_TYPE_TOOL_CALL,
    EVENT_TYPE_TOOL_RESULT,
)
from pydantic import BaseModel
from starlette.responses import JSONResponse

from ._shared import StructuredHTTPException, get_state, require_auth, structured_error_response
from .sse_utils import create_sse_response, sse_event_generator

if TYPE_CHECKING:
    from polaris.cells.runtime.state_owner.public.service import AppState

logger = logging.getLogger(__name__)

router = APIRouter()
_SESSION_CONTINUITY_ENGINE = SessionContinuityEngine()


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


class ExportToWorkflowRequest(BaseModel):
    """Request to export session to workflow"""

    target: Literal["pm", "director", "factory"]
    export_kind: Literal["session_bundle", "artifacts_only", "messages_only"] = "session_bundle"
    include_audit_log: bool = False


# ==================== Helper Functions ====================


def _build_directive_from_artifacts(artifacts: list[Any]) -> str:
    """Build a directive string from session artifacts.

    Args:
        artifacts: List of artifacts from the session

    Returns:
        Combined directive text
    """
    directives = []

    # Look for specific artifact types that contain directives
    for artifact in artifacts:
        content = getattr(artifact, "content", "") or ""
        artifact_type = getattr(artifact, "type", "") or ""

        # Prioritize certain artifact types
        if artifact_type in ("directive", "requirement", "goal"):
            directives.append(content)
        elif artifact_type in ("plan", "specification"):
            directives.append(f"Plan: {content}")

    # If no specific directives found, use all text artifacts
    if not directives:
        for artifact in artifacts:
            content = getattr(artifact, "content", "") or ""
            artifact_type = getattr(artifact, "type", "") or ""
            if artifact_type in ("message", "text", "code") and content:
                # Truncate long content
                if len(content) > 500:
                    content = content[:500] + "..."
                directives.append(content)

    return "\n\n".join(directives) if directives else "Continue from exported session"


def _build_task_filter_from_artifacts(artifacts: list[Any]) -> str:
    """Build a task filter from session artifacts.

    Args:
        artifacts: List of artifacts from the session

    Returns:
        Task filter string for Director
    """
    # Extract task-related artifacts
    tasks = []

    for artifact in artifacts:
        content = getattr(artifact, "content", "") or ""
        artifact_type = getattr(artifact, "type", "") or ""

        if artifact_type in ("task", "todo", "action_item"):
            tasks.append(content)

    if tasks:
        return "Execute tasks: " + "; ".join(tasks[:5])  # Limit to first 5 tasks

    # Fallback to using directive
    directive = _build_directive_from_artifacts(artifacts)
    return directive[:200] if directive else "Execute ready tasks"


# ==================== Session Endpoints ====================


@router.post("/v2/roles/sessions", dependencies=[Depends(require_auth)])
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
    state = get_state(request)

    try:
        with RoleSessionService() as service:
            session = service.create_session(
                role=payload.role,
                host_kind=payload.host_kind,  # type: ignore[arg-type]
                workspace=payload.workspace or str(state.settings.workspace or ""),
                session_type=payload.session_type,  # type: ignore[arg-type]
                attachment_mode=payload.attachment_mode,  # type: ignore[arg-type]
                title=payload.title,
                context_config=payload.context_config,
                capability_profile=payload.capability_profile,
            )

            return {
                "ok": True,
                "session": session.to_dict(),
            }

    except (RuntimeError, ValueError) as e:
        return {
            "ok": False,
            "error": str(e),
        }
    return {}  # type: ignore[return]


@router.get("/v2/roles/sessions", dependencies=[Depends(require_auth)])
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
    state: AppState = get_state(request)

    try:
        with RoleSessionService() as service:
            sessions = service.get_sessions(
                role=role,
                host_kind=host_kind,
                workspace=workspace or str(str(state.settings.workspace or "")),
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
        return {
            "ok": False,
            "error": str(e),
        }
    return {}  # type: ignore[return]


@router.get("/v2/roles/sessions/{session_id}", dependencies=[Depends(require_auth)])
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
        with RoleSessionService() as service:
            session = service.get_session(session_id)

            if not session:
                return JSONResponse(  # type: ignore[return-value]
                    {"ok": False, "error": f"Session not found: {session_id}"},
                    status_code=404,
                )

            return {
                "ok": True,
                "session": session.to_dict(),
            }

    except (RuntimeError, ValueError) as e:
        return {
            "ok": False,
            "error": str(e),
        }
    return {}  # type: ignore[return]


@router.put("/v2/roles/sessions/{session_id}", dependencies=[Depends(require_auth)])
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
        with RoleSessionService() as service:
            session = service.update_session(
                session_id=session_id,
                title=payload.title,
                context_config=payload.context_config,
                capability_profile=payload.capability_profile,
                state=payload.state,
            )

            if not session:
                raise HTTPException(
                    status_code=404,
                    detail=f"Session not found: {session_id}",
                )

            return {
                "ok": True,
                "session": session.to_dict(),
            }

    except (RuntimeError, ValueError) as e:
        return {
            "ok": False,
            "error": str(e),
        }
    return {}  # type: ignore[return]


@router.delete("/v2/roles/sessions/{session_id}", dependencies=[Depends(require_auth)])
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
        with RoleSessionService() as service:
            success = service.delete_session(session_id, soft=soft)

            if not success:
                return JSONResponse(  # type: ignore[return-value]
                    {"ok": False, "error": f"Session not found: {session_id}"},
                    status_code=404,
                )

            return {
                "ok": True,
            }

    except (RuntimeError, ValueError) as e:
        return {
            "ok": False,
            "error": str(e),
        }


# ==================== Message Endpoints ====================


@router.get("/v2/roles/sessions/{session_id}/messages", dependencies=[Depends(require_auth)])
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
        with RoleSessionService() as service:
            session = service.get_session(session_id)
            if not session:
                raise HTTPException(
                    status_code=404,
                    detail=f"Session not found: {session_id}",
                )

            messages = service.get_messages(session_id, limit=limit, offset=offset)

            return {
                "ok": True,
                "messages": [m.to_dict() for m in messages],
                "session": session.to_dict(),
            }

    except (RuntimeError, ValueError) as e:
        return {
            "ok": False,
            "error": str(e),
        }
    return {}  # type: ignore[return]


@router.post("/v2/roles/sessions/{session_id}/messages", dependencies=[Depends(require_auth)])
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
        with RoleSessionService() as service:
            session = service.add_message(
                session_id=session_id,
                role=payload.role,
                content=payload.content,
                thinking=payload.thinking,
                meta=payload.meta,
            )

            if not session:
                raise HTTPException(
                    status_code=404,
                    detail=f"Session not found: {session_id}",
                )

            return {
                "ok": True,
                "session": session.to_dict(),
            }

    except (RuntimeError, ValueError) as e:
        return {
            "ok": False,
            "error": str(e),
        }


@router.post("/v2/roles/sessions/{session_id}/messages/stream", dependencies=[Depends(require_auth)])
async def send_message_stream(
    request: Request,
    session_id: str,
    payload: SendMessageRequest,
) -> Any:
    """发送消息（流式）

    POST /v2/roles/sessions/{session_id}/messages/stream

    使用 SSE 进行流式响应。
    """
    state: AppState = get_state(request)

    try:
        with RoleSessionService() as service:
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
            try:
                session_context = json.loads(context_config_raw) if context_config_raw else None
            except json.JSONDecodeError:
                logger.warning(
                    "Role session %s has invalid context_config JSON; falling back to None",
                    session_id,
                )
                session_context = None

            projection = _SESSION_CONTINUITY_ENGINE.project(  # type: ignore[attr-defined]
                SessionContinuityRequest(
                    session_id=session_id,
                    role=session_role,
                    workspace=str(str(state.settings.workspace or "")),
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
            runtime_history = messages_to_history_pairs(projection.recent_messages)  # type: ignore[attr-defined]
            runtime_context = dict(projection.prompt_context)  # type: ignore[attr-defined]
            if projection.changed:  # type: ignore[attr-defined]
                service.update_session(
                    session_id=session_id,
                    context_config=projection.persisted_context_config,  # type: ignore[attr-defined]
                )

            # 保存用户消息
            service.add_message(
                session_id=session_id,
                role=payload.role,
                content=payload.content,
            )

            async def _run_role_session_dialogue(queue: asyncio.Queue[dict[str, Any]]) -> None:
                output_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
                producer_task = asyncio.create_task(
                    generate_role_response_streaming(
                        workspace=str(str(state.settings.workspace or "")),
                        settings=state.settings,
                        role=session_role,
                        message=payload.content,
                        output_queue=output_queue,
                        context=runtime_context,
                        session_id=session_id,
                        history=runtime_history,
                    )
                )
                response_parts: list[str] = []
                thinking_parts: list[str] = []
                assistant_saved = False
                try:
                    while True:
                        event = await output_queue.get()
                        event_type = str(event.get("type") or "").strip()
                        event_data = event.get("data")
                        event_payload = event_data if isinstance(event_data, dict) else {}

                        if event_type == "content_chunk":
                            response_parts.append(str(event_payload.get("content") or ""))
                            await queue.put(event)
                            continue

                        if event_type == EVENT_TYPE_THINKING_CHUNK:
                            thinking_parts.append(str(event_payload.get("content") or ""))
                            await queue.put(event)
                            continue

                        if event_type in {EVENT_TYPE_TOOL_CALL, EVENT_TYPE_TOOL_RESULT, EVENT_TYPE_FINGERPRINT}:
                            await queue.put(event)
                            continue

                        if event_type == EVENT_TYPE_COMPLETE:
                            response = str(event_payload.get("content") or "") or "".join(response_parts)
                            thinking = str(event_payload.get("thinking") or "") or "".join(thinking_parts) or None
                            if response:
                                with RoleSessionService() as save_service:
                                    save_service.add_message(
                                        session_id=session_id,
                                        role="assistant",
                                        content=response,
                                        thinking=thinking,
                                    )
                                assistant_saved = True
                            await queue.put(event)
                            break

                        if event_type == EVENT_TYPE_ERROR:
                            await queue.put(event)
                            break

                        if event_type == "done":
                            if response_parts and not assistant_saved:
                                with RoleSessionService() as save_service:
                                    save_service.add_message(
                                        session_id=session_id,
                                        role="assistant",
                                        content="".join(response_parts),
                                        thinking="".join(thinking_parts) or None,
                                    )
                            await queue.put(
                                {
                                    "type": "complete",
                                    "data": {
                                        "content": "".join(response_parts),
                                        "thinking": "".join(thinking_parts),
                                    },
                                }
                            )
                            break
                finally:
                    if not producer_task.done():
                        producer_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await producer_task

            return create_sse_response(sse_event_generator(_run_role_session_dialogue, timeout=180.0))

    except HTTPException:
        raise
    except (RuntimeError, ValueError) as e:
        logger.error(f"Role session action failed: {e}")
        return structured_error_response(
            status_code=500,
            code="internal_error",
            message="An internal error occurred while processing the role session.",
            details={"exception": str(e)},
        )


# ==================== Attachment Endpoints ====================


@router.post("/v2/roles/sessions/{session_id}/actions/attach", dependencies=[Depends(require_auth)])
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
        with RoleSessionService() as service:
            attachment = service.attach_session(
                session_id=session_id,
                run_id=payload.run_id,
                task_id=payload.task_id,
                mode=payload.mode,
                note=payload.note,
            )

            if not attachment:
                return {
                    "ok": False,
                    "error": f"Session not found: {session_id}",
                }

            session = service.get_session(session_id)

            return {
                "ok": True,
                "attachment": attachment.to_dict(),
                "session": session.to_dict() if session else None,
            }

    except (RuntimeError, ValueError) as e:
        return {
            "ok": False,
            "error": str(e),
        }


@router.post("/v2/roles/sessions/{session_id}/actions/detach", dependencies=[Depends(require_auth)])
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
        with RoleSessionService() as service:
            success = service.detach_session(session_id)

            if not success:
                return {
                    "ok": False,
                    "error": f"Session not found: {session_id}",
                }

            session = service.get_session(session_id)

            return {
                "ok": True,
                "session": session.to_dict() if session else None,
            }

    except (RuntimeError, ValueError) as e:
        return {
            "ok": False,
            "error": str(e),
        }


# ==================== Export Endpoints ====================


@router.get("/v2/roles/sessions/{session_id}/artifacts", dependencies=[Depends(require_auth)])
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
    state = get_state(request)

    try:
        with RoleSessionService() as service:
            session = service.get_session(session_id)
            if not session:
                raise HTTPException(
                    status_code=404,
                    detail=f"Session not found: {session_id}",
                )

        # Use artifact service to list artifacts
        artifact_service = RoleSessionArtifactService(Path(str(state.settings.workspace or "")))
        artifacts = artifact_service.list_artifacts(session_id, artifact_type)

        return {
            "ok": True,
            "artifacts": [a.to_dict() for a in artifacts],
        }

    except (RuntimeError, ValueError) as e:
        return {
            "ok": False,
            "error": str(e),
        }


@router.get("/v2/roles/sessions/{session_id}/audit", dependencies=[Depends(require_auth)])
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
    state = get_state(request)

    try:
        with RoleSessionService() as service:
            session = service.get_session(session_id)
            if not session:
                raise HTTPException(
                    status_code=404,
                    detail=f"Session not found: {session_id}",
                )

        # Use audit service to get events
        audit_service = RoleSessionAuditService(Path(str(state.settings.workspace or "")))
        events = audit_service.get_events(session_id, event_type, limit, offset)

        return {
            "ok": True,
            "audit_events": events,
        }

    except (RuntimeError, ValueError) as e:
        return {
            "ok": False,
            "error": str(e),
        }


# ==================== Context OS Memory Endpoints ====================


@router.get("/v2/roles/sessions/{session_id}/memory/search", dependencies=[Depends(require_auth)])
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
        return {"ok": False, "error": str(exc)}

    with RoleSessionContextMemoryService() as service:
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


@router.get("/v2/roles/sessions/{session_id}/memory/artifacts/{artifact_id}", dependencies=[Depends(require_auth)])
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
        return {"ok": False, "error": str(exc)}

    with RoleSessionContextMemoryService() as service:
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


@router.get("/v2/roles/sessions/{session_id}/memory/episodes/{episode_id}", dependencies=[Depends(require_auth)])
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
        return {"ok": False, "error": str(exc)}

    with RoleSessionContextMemoryService() as service:
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


@router.get("/v2/roles/sessions/{session_id}/memory/state", dependencies=[Depends(require_auth)])
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
        return {"ok": False, "error": str(exc)}

    with RoleSessionContextMemoryService() as service:
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


@router.post("/v2/roles/sessions/{session_id}/actions/export", dependencies=[Depends(require_auth)])
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
        with RoleSessionService() as service:
            export_data = service.export_session(
                session_id,
                include_messages=payload.include_messages,
            )

            if not export_data:
                return {
                    "ok": False,
                    "error": f"Session not found: {session_id}",
                }

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
        return {
            "ok": False,
            "error": str(e),
        }


# ==================== Export to Workflow Endpoint ====================


@router.post("/v2/roles/sessions/{session_id}/actions/export-to-workflow", dependencies=[Depends(require_auth)])
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
        # Initialize services
        artifact_service = RoleSessionArtifactService(Path(str(state.settings.workspace or "")))
        audit_service = RoleSessionAuditService(Path(str(state.settings.workspace or "")))

        # Verify session exists
        with RoleSessionService() as service:
            session = service.get_session(session_id)
            if not session:
                raise HTTPException(
                    status_code=404,
                    detail=f"Session not found: {session_id}",
                )

        # 1. Collect session content
        artifacts = artifact_service.list_artifacts(session_id)
        events = audit_service.get_events(session_id, limit=1000)

        # 2. Build export bundle
        export_bundle = {
            "session_id": session_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "target": payload.target,
            "export_kind": payload.export_kind,
            "artifacts": [
                {"id": a.id, "type": a.type, "content": a.content, "metadata": a.metadata} for a in artifacts
            ],
            "event_count": len(events) if payload.include_audit_log else 0,
        }

        # 3. Persist export bundle and create target workflow
        import json

        from polaris.infrastructure.storage import LocalFileSystemAdapter
        from polaris.kernelone.fs import KernelFileSystem

        workspace_root = Path(str(state.settings.workspace or "")).resolve()
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
            directive = _build_directive_from_artifacts(artifacts)

            result = await cmd_service.execute_pm_run(
                workspace=str(str(state.settings.workspace or "")),
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
            task_filter = _build_task_filter_from_artifacts(artifacts)

            result = await cmd_service.execute_director_run(
                workspace=str(str(state.settings.workspace or "")),
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

            factory_service = FactoryRunService(workspace=Path(str(state.settings.workspace or "")))
            directive = _build_directive_from_artifacts(artifacts)

            config = FactoryConfig(
                name=f"export_from_{session_id}",
                description=f"Factory run exported from session {session_id}",
                stages=["docs_generation", "pm_planning", "director_dispatch", "quality_gate"],
                auto_dispatch=True,
            )

            run = await factory_service.create_run(config)
            run_id = run.id

            # Start the factory run
            await factory_service.start_run(run_id)

            # Record export reference in metadata
            run.metadata["export_session_id"] = session_id
            run.metadata["export_bundle_path"] = str(export_path)
            run.metadata["directive"] = directive

        # 4. Record export event
        audit_service.append_audit_event(
            session_id=session_id,
            event_type="workflow_exported",
            details={
                "target": payload.target,
                "run_id": run_id,
                "artifact_count": len(artifacts),
                "export_kind": payload.export_kind,
            },
        )

        return {
            "ok": True,
            "exported_to": payload.target,
            "run_id": run_id,
            "session_id": session_id,
            "artifact_count": len(artifacts),
        }

    except (RuntimeError, ValueError) as e:
        return {
            "ok": False,
            "error": str(e),
        }


# ==================== Capabilities Endpoint ====================


@router.get("/v2/roles/capabilities/{role}", dependencies=[Depends(require_auth)])
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
        return {
            "ok": False,
            "error": str(e),
        }
