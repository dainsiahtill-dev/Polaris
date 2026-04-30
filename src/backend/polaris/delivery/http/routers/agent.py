"""Agent Router - canonical session/runtime wrapper.

This router preserves the V1 agent-facing HTTP shape, but the implementation
now routes through canonical `roles.session` + `roles.runtime` services
instead of maintaining a private in-memory session model.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from polaris.cells.factory.pipeline.public.types import RunPhase
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
from polaris.cells.roles.runtime.public.service import RoleRuntimeService
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
from polaris.kernelone.context.session_continuity import (
    SessionContinuityEngine,
    SessionContinuityRequest,
    history_pairs_to_messages,
    messages_to_history_pairs,
)
from pydantic import BaseModel, Field

from ._shared import get_state, require_auth

router = APIRouter(prefix="/agent", tags=["agent"])

_AGENT_CONTEXT_FLAG = "agent_router_v1"
_AGENT_SESSION_SCAN_LIMIT = 200
_AGENT_CONTINUITY_ENGINE = SessionContinuityEngine()
_ROLE_RUNTIME = RoleRuntimeService()


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


def _load_agent_session(session_id: str) -> dict[str, Any] | None:
    with RoleSessionService() as service:
        session = service.get_session(session_id)
        if session is None:
            return None
        payload = session.to_dict(include_messages=False)
        if not _is_agent_session_payload(payload):
            return None
        messages = [message.to_dict() for message in service.get_messages(str(session.id), limit=200, offset=0)]
        return _build_agent_session_payload(session_payload=payload, messages=messages)


def _get_or_create_session(workspace: str, role: str, session_id: str | None = None) -> dict[str, Any]:
    if session_id:
        existing = _load_agent_session(session_id)
        if existing is not None:
            return existing

    session_context = {
        _AGENT_CONTEXT_FLAG: True,
        "workspace": workspace,
    }
    session = RoleSessionService.create_ad_hoc_session(
        role=role,
        workspace=workspace,
        host_kind=RoleHostKind.API_SERVER.value,
        title=f"Agent {role} session",
        context_config=session_context,
    )
    return _build_agent_session_payload(
        session_payload=session.to_dict(include_messages=False),
        messages=[],
    )


def _project_agent_turn(
    *,
    service: RoleSessionService,
    session_id: str,
    role: str,
    workspace: str,
    session_title: str,
    session_context: dict[str, Any],
) -> tuple[tuple[tuple[str, str], ...], dict[str, Any]]:
    prior_messages = [message.to_dict() for message in service.get_messages(session_id, limit=50, offset=0)]
    projection = _AGENT_CONTINUITY_ENGINE.project(
        SessionContinuityRequest(
            session_id=session_id,
            role=role,
            workspace=workspace,
            session_title=session_title,
            messages=history_pairs_to_messages(_message_history_pairs(prior_messages)),
            session_context_config=session_context,
            incoming_context={
                "role": role,
                "host_kind": RoleHostKind.API_SERVER.value,
            },
            history_limit=10,
        )
    )
    if projection.changed:
        service.update_session(
            session_id=session_id,
            context_config=projection.persisted_context_config,
        )
    return messages_to_history_pairs(projection.recent_messages), dict(projection.prompt_context)


async def _execute_agent_message(
    session_id: str,
    message: str,
    role: str,
    workspace: str,
) -> dict[str, Any]:
    with RoleSessionService() as service:
        session = service.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        session_payload = session.to_dict(include_messages=False)
        if not _is_agent_session_payload(session_payload):
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        runtime_role = _safe_text(session_payload.get("role")) or role
        runtime_history, runtime_context = _project_agent_turn(
            service=service,
            session_id=session_id,
            role=runtime_role,
            workspace=workspace,
            session_title=_safe_text(session_payload.get("title")) or f"Agent {runtime_role} session",
            session_context=_normalize_session_context(session_payload.get("context_config")),
        )
        service.add_message(
            session_id=session_id,
            role="user",
            content=message,
        )

    result = await _ROLE_RUNTIME.execute_role_session(
        ExecuteRoleSessionCommandV1(
            role=runtime_role,
            session_id=session_id,
            workspace=workspace,
            user_message=message,
            history=runtime_history,
            context=runtime_context,
            stream=False,
        )
    )

    with RoleSessionService() as service:
        service.add_message(
            session_id=session_id,
            role="assistant",
            content=str(result.output or ""),
            thinking=result.thinking,
            meta={
                "tool_calls": [{"name": name} for name in list(result.tool_calls or ()) if _safe_text(name)],
                "status": result.status,
                "error_message": result.error_message,
            },
        )

    return {
        "ok": result.ok,
        "session_id": session_id,
        "reply": result.output,
        "reasoning_summary": result.thinking,
        "tool_calls": list(result.tool_calls),
        "error": result.error_message if not result.ok else None,
    }


async def _stream_agent_response(
    *,
    session_id: str,
    message: str,
    workspace: str,
    role: str,
    output_queue: asyncio.Queue,
) -> None:
    with RoleSessionService() as service:
        session = service.get_session(session_id)
        if session is None:
            await output_queue.put({"type": "error", "data": {"error": f"Session {session_id} not found"}})
            await output_queue.put({"type": "done"})
            return
        session_payload = session.to_dict(include_messages=False)
        runtime_role = _safe_text(session_payload.get("role")) or role
        runtime_history, runtime_context = _project_agent_turn(
            service=service,
            session_id=session_id,
            role=runtime_role,
            workspace=workspace,
            session_title=_safe_text(session_payload.get("title")) or f"Agent {runtime_role} session",
            session_context=_normalize_session_context(session_payload.get("context_config")),
        )
        service.add_message(
            session_id=session_id,
            role="user",
            content=message,
        )

    command = ExecuteRoleSessionCommandV1(
        role=runtime_role,
        session_id=session_id,
        workspace=workspace,
        user_message=message,
        history=runtime_history,
        context=runtime_context,
        stream=True,
    )

    response_parts: list[str] = []
    thinking_parts: list[str] = []
    assistant_saved = False
    await output_queue.put(
        {
            "type": "thinking_start",
            "data": {"message": "Starting..."},
        }
    )
    try:
        async for event in _ROLE_RUNTIME.stream_chat_turn(command):
            event_type = _safe_text(event.get("type"))
            if event_type == "content_chunk":
                response_parts.append(_safe_text(event.get("content")))
                await output_queue.put(
                    {
                        "type": "content_chunk",
                        "data": {"content": _safe_text(event.get("content"))},
                    }
                )
                continue
            if event_type == "thinking_chunk":
                thinking_parts.append(_safe_text(event.get("content")))
                await output_queue.put(
                    {
                        "type": "thinking_chunk",
                        "data": {"content": _safe_text(event.get("content"))},
                    }
                )
                continue
            if event_type == "tool_call":
                await output_queue.put(
                    {
                        "type": "tool_call",
                        "data": {
                            "tool": event.get("tool"),
                            "args": event.get("args") or {},
                        },
                    }
                )
                continue
            if event_type == "tool_result":
                await output_queue.put(
                    {
                        "type": "tool_result",
                        "data": event.get("result", {}),
                    }
                )
                continue
            if event_type == "fingerprint":
                await output_queue.put(
                    {
                        "type": "fingerprint",
                        "data": {"fingerprint": event.get("fingerprint")},
                    }
                )
                continue
            if event_type == "complete":
                result = event.get("result")
                content = _safe_text(
                    getattr(result, "output", None) or getattr(result, "content", None) or "".join(response_parts)
                )
                thinking = _safe_text(getattr(result, "thinking", None) or "".join(thinking_parts))
                tool_calls = [
                    {"name": name} for name in list(getattr(result, "tool_calls", ()) or ()) if _safe_text(name)
                ]
                with RoleSessionService() as service:
                    service.add_message(
                        session_id=session_id,
                        role="assistant",
                        content=content,
                        thinking=thinking or None,
                        meta={
                            "tool_calls": tool_calls,
                            "status": getattr(result, "status", None),
                            "error_message": getattr(result, "error_message", None),
                        },
                    )
                assistant_saved = True
                await output_queue.put(
                    {
                        "type": "complete",
                        "data": {
                            "content": content,
                            "thinking": thinking or None,
                            "tool_calls": tool_calls,
                        },
                    }
                )
                break
            if event_type == "error":
                await output_queue.put(
                    {
                        "type": "error",
                        "data": {"error": _safe_text(event.get("error")) or "Unknown error"},
                    }
                )
                return
    except (RuntimeError, ValueError) as exc:
        await output_queue.put(
            {
                "type": "error",
                "data": {"error": str(exc)},
            }
        )
        return
    finally:
        if response_parts and not assistant_saved:
            with RoleSessionService() as service:
                service.add_message(
                    session_id=session_id,
                    role="assistant",
                    content="".join(response_parts),
                    thinking="".join(thinking_parts) or None,
                    meta={},
                )
        await output_queue.put({"type": "done"})


@router.get("/sessions", dependencies=[Depends(require_auth)])
async def list_agent_sessions(
    request: Request,
    limit: int = 20,
) -> dict[str, Any]:
    get_state(request)
    with RoleSessionService() as service:
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
                    messages=[message.to_dict() for message in service.get_messages(str(session.id), limit=20, offset=0)],
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
    session = _load_agent_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
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
    if _load_agent_session(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
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


@router.get("/sessions/{session_id}/memory/artifacts/{artifact_id}", dependencies=[Depends(require_auth)])
async def read_agent_session_memory_artifact(
    request: Request,
    session_id: str,
    artifact_id: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict[str, Any]:
    get_state(request)
    if _load_agent_session(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
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


@router.get("/sessions/{session_id}/memory/episodes/{episode_id}", dependencies=[Depends(require_auth)])
async def read_agent_session_memory_episode(
    request: Request,
    session_id: str,
    episode_id: str,
) -> dict[str, Any]:
    get_state(request)
    if _load_agent_session(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
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


@router.get("/sessions/{session_id}/memory/state", dependencies=[Depends(require_auth)])
async def read_agent_session_memory_state(
    request: Request,
    session_id: str,
    path: str,
) -> dict[str, Any]:
    get_state(request)
    if _load_agent_session(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
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


@router.post("/sessions/{session_id}/messages", dependencies=[Depends(require_auth)])
async def send_agent_message(
    request: Request,
    session_id: str,
    payload: SessionMessageRequest,
) -> dict[str, Any]:
    state = get_state(request)
    result = await _execute_agent_message(
        session_id="",
        message=payload.message.strip(),
        role=payload.role,
        workspace=str(state.settings.workspace),
    )
    return result


@router.post("/sessions/{session_id}/messages/stream", dependencies=[Depends(require_auth)])
async def send_agent_message_stream(
    request: Request,
    session_id: str,
    payload: SessionMessageRequest,
) -> StreamingResponse:
    state = get_state(request)
    session = _load_agent_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    message = payload.message.strip()
    role = payload.role
    output_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

    async def event_generator():
        producer_task = asyncio.create_task(
            _stream_agent_response(
                session_id=session_id,
                message=message,
                workspace=state.settings.workspace,
                role=role,
                output_queue=output_queue,
            )
        )
        try:
            while True:
                event = await output_queue.get()
                if event.get("type") == "done":
                    break
                yield f"event: {event.get('type', 'message')}\n"
                yield f"data: {json.dumps(event.get('data', {}), ensure_ascii=False)}\n\n"
                # Force flush to ensure immediate delivery (P0: SSE yield blocking fix)
                await asyncio.sleep(0)
        except (RuntimeError, ValueError) as exc:
            yield f"event: error\ndata: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
        finally:
            if not producer_task.done():
                producer_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer_task

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Transfer-Encoding": "chunked",
        },
    )


@router.delete("/sessions/{session_id}", dependencies=[Depends(require_auth)])
async def delete_agent_session(
    request: Request,
    session_id: str,
) -> dict[str, Any]:
    get_state(request)
    with RoleSessionService() as service:
        session = service.get_session(session_id)
        if session is None or not _is_agent_session_payload(session.to_dict(include_messages=False)):
            return {"ok": False, "error": "Session not found"}
        success = service.delete_session(session_id, soft=False)
    if success:
        return {"ok": True, "message": f"Session {session_id} deleted"}
    return {"ok": False, "error": "Session not found"}


@router.post("/turn", dependencies=[Depends(require_auth)])
async def agent_turn(
    request: Request,
    payload: AgentTurnPayload,
) -> dict[str, Any]:
    state = get_state(request)
    workspace = payload.workspace or state.settings.workspace
    message = payload.message.strip()
    role = payload.role
    stream = bool(payload.stream)

    session = _get_or_create_session(str(workspace), role)
    session_id = _safe_text(session.get("session_id"))

    if stream:
        return {
            "ok": True,
            "session_id": session_id,
            "stream_url": f"/v2/agent/sessions/{session_id}/messages/stream",
        }

    response = await _execute_agent_message(
        session_id=session_id,
        message=message,
        role=role,
        workspace=str(workspace),
    )
    return {
        "ok": bool(response.get("ok")),
        "session_id": session_id,
        "reply": response.get("reply", ""),
        "reasoning_summary": response.get("reasoning_summary"),
        "phase": RunPhase.PENDING.value,
        "error": response.get("error"),
    }
