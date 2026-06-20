"""Role Chat Router - 通用角色对话状态查询与对话接口

提供角色LLM配置状态查询接口，以及统一的角色对话（非流式/流式）接口。
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.cells.llm.dialogue.public import (
    get_registered_roles,
)
from polaris.cells.llm.evaluation.public.service import load_llm_test_index
from polaris.cells.roles.kernel.public.service import get_global_emitter, get_global_llm_cache
from polaris.delivery.http.auth.roles import UserRole
from polaris.delivery.http.schemas import (
    AllLLMEventsResponse,
    CacheClearResponse,
    CacheStatsResponse,
    RoleChatPingResponse,
    RoleChatResponse,
    RoleChatStatusResponse,
    RoleListResponse,
    RoleLLMEventsResponse,
)
from polaris.delivery.http.v2.llm_event_filters import filter_llm_events_by_workspace
from polaris.delivery.http.workspace import (
    active_workspace_value,
    requested_or_active_workspace,
    settings_with_workspace_override,
)
from polaris.kernelone.llm import config_store as llm_config
from polaris.kernelone.storage.io_paths import build_cache_root

from ._shared import (
    StructuredHTTPException,
    ensure_required_roles_ready,
    get_state,
    require_auth,
    require_role,
)
from .role_chat_jetstream import (
    _new_chat_session_id,
    execute_role_chat_jetstream,
)
from .role_runtime_chat import execute_role_chat_nonstreaming

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/v2/role/chat/ping", dependencies=[Depends(require_auth)], response_model=RoleChatPingResponse)
async def role_chat_ping() -> dict[str, Any]:
    """Health check for the role chat router."""
    return {
        "status": "ok",
        "message": "Role Chat router is working",
        "supported_roles": get_registered_roles(),
    }


async def _load_llm_test_index_async(workspace: str) -> dict[str, Any]:
    """异步加载 LLM 测试索引（将同步文件 I/O 移到线程池）"""
    return await asyncio.to_thread(load_llm_test_index, workspace)


async def _load_llm_config_async(workspace: str, cache_root: str, settings: Any) -> dict[str, Any]:
    """异步加载 LLM 配置（将同步文件 I/O 移到线程池）"""
    import asyncio

    return await asyncio.to_thread(llm_config.load_llm_config, workspace, cache_root, settings)


def _workspace_value(settings: Any) -> str:
    """Resolve the active desktop workspace from compatible settings fields."""
    return active_workspace_value(settings)


def _context_workspace(payload: dict[str, Any]) -> str:
    """Read an explicit workspace from a role-chat payload context."""
    context = payload.get("context")
    if not isinstance(context, dict):
        return ""
    workspace = context.get("workspace")
    return workspace.strip() if isinstance(workspace, str) else ""


def _workspace_for_role_request(settings: Any, requested: str = "", payload: dict[str, Any] | None = None) -> str:
    """Resolve workspace from query/body context before falling back to active desktop state."""
    return requested_or_active_workspace(settings, requested or _context_workspace(payload or {}))


def _state_for_workspace(state: Any, workspace: str) -> Any:
    """Return a lightweight state pinned to the resolved role-chat workspace."""
    return SimpleNamespace(settings=settings_with_workspace_override(state.settings, workspace))


@router.get("/v2/role/{role}/chat/status", dependencies=[Depends(require_auth)], response_model=RoleChatStatusResponse)
async def role_chat_status(
    request: Request,
    role: str,
    workspace: str = "",
) -> dict[str, Any]:
    """Get LLM configuration readiness for a specific role.

    Returns:
        Ready state, provider info, and debug details.
    """
    state = get_state(request)
    workspace = _workspace_for_role_request(state.settings, workspace)
    scoped_state = _state_for_workspace(state, workspace)

    try:
        cache_root = build_cache_root(
            "",  # ramdisk_root (empty string as default)
            workspace,
        )

        # 评测索引用于补充状态，不应误判为“未配置”。
        # 使用线程池执行文件 I/O 操作，避免阻塞事件循环
        index = await _load_llm_test_index_async(workspace)
        role_status = (index.get("roles") or {}).get(role) if isinstance(index, dict) else None
        llm_test_ready = bool(isinstance(role_status, dict) and role_status.get("ready"))

        # 加载配置获取详细信息（使用线程池执行文件 I/O）
        config = await _load_llm_config_async(
            workspace,
            cache_root,
            scoped_state.settings,
        )

        roles_raw = config.get("roles")
        roles: dict[str, Any] = roles_raw if isinstance(roles_raw, dict) else {}
        providers_raw = config.get("providers")
        providers: dict[str, Any] = providers_raw if isinstance(providers_raw, dict) else {}

        role_config = roles.get(role)
        if not isinstance(role_config, dict):
            return {
                "ready": False,
                "configured": False,
                "workspace": workspace,
                "error": "Role not configured",
                "debug": {
                    "roles_keys": list(roles.keys()) if roles else None,
                    "supported_roles": get_registered_roles(),
                },
            }

        provider_id = str(role_config.get("provider_id") or "").strip()
        model = str(role_config.get("model") or "").strip()

        if not provider_id or not model:
            return {
                "ready": False,
                "configured": False,
                "workspace": workspace,
                "error": "Role provider or model not set",
                "debug": {
                    "role_config": role_config,
                    "provider_id": provider_id if provider_id else "(empty)",
                    "model": model if model else "(empty)",
                },
            }

        provider_cfg = providers.get(provider_id)
        if not isinstance(provider_cfg, dict):
            return {
                "ready": False,
                "configured": False,
                "workspace": workspace,
                "error": "Provider not found",
                "debug": {
                    "role_config": role_config,
                    "available_providers": list(providers.keys()),
                },
            }

        return {
            "ready": True,
            "configured": True,
            "llm_test_ready": llm_test_ready,
            "role": role,
            "workspace": workspace,
            "role_config": {
                "provider_id": provider_id,
                "model": model,
                "profile": role_config.get("profile"),
            },
            "provider_type": provider_cfg.get("type"),
            "debug": {
                "is_role_ready": llm_test_ready,
                "roles_keys": list(roles.keys()),
            },
        }

    except (RuntimeError, ValueError) as exc:
        import traceback

        return {
            "ready": False,
            "configured": False,
            "llm_test_ready": False,
            "role": role,
            "workspace": workspace,
            "code": getattr(exc, "code", "internal_error"),
            "message": "Status check failed",
            "details": {
                "exception": traceback.format_exc(),
            },
        }


@router.get("/v2/role/chat/roles", dependencies=[Depends(require_auth)], response_model=RoleListResponse)
async def list_supported_roles() -> dict[str, Any]:
    """List all registered LLM roles."""
    return {
        "roles": get_registered_roles(),
        "count": len(get_registered_roles()),
    }


# ============================================================================
# LLM Events API - 实时 LLM 调用状态
# ============================================================================


@router.get("/v2/role/{role}/llm-events", dependencies=[Depends(require_auth)], response_model=RoleLLMEventsResponse)
async def get_role_llm_events(
    request: Request,
    role: str,
    run_id: str | None = None,
    task_id: str | None = None,
    limit: int = 100,
    workspace: str = "",
) -> dict[str, Any]:
    """Get LLM call events for a specific role.

    Returns:
        Events list with categorized stats.
    """
    emitter = get_global_emitter()
    events = emitter.get_events(run_id=run_id, task_id=task_id, role=role, limit=limit)
    state = get_state(request)
    resolved_workspace = requested_or_active_workspace(state.settings, workspace)
    if workspace.strip():
        events = filter_llm_events_by_workspace(events, resolved_workspace)

    # 分类统计
    stats = {
        "total": len(events),
        "call_start": sum(1 for e in events if e.event_type == "llm_call_start"),
        "call_end": sum(1 for e in events if e.event_type == "llm_call_end"),
        "call_error": sum(1 for e in events if e.event_type == "llm_error"),
        "call_retry": sum(1 for e in events if e.event_type == "llm_retry"),
        "validation_pass": sum(1 for e in events if e.event_type == "validation_pass"),
        "validation_fail": sum(1 for e in events if e.event_type == "validation_fail"),
    }

    return {
        "role": role,
        "run_id": run_id,
        "task_id": task_id,
        "workspace": resolved_workspace,
        "events": [e.to_dict() for e in events],
        "stats": stats,
    }


@router.get("/v2/role/llm-events", dependencies=[Depends(require_auth)], response_model=AllLLMEventsResponse)
async def get_all_llm_events(
    request: Request,
    run_id: str | None = None,
    task_id: str | None = None,
    role: str | None = None,
    limit: int = 100,
    workspace: str = "",
) -> dict[str, Any]:
    """Get LLM call events across all roles."""
    emitter = get_global_emitter()
    events = emitter.get_events(run_id=run_id, task_id=task_id, role=role, limit=limit)
    state = get_state(request)
    resolved_workspace = requested_or_active_workspace(state.settings, workspace)
    if workspace.strip():
        events = filter_llm_events_by_workspace(events, resolved_workspace)

    return {
        "events": [e.to_dict() for e in events],
        "count": len(events),
        "workspace": resolved_workspace,
    }


@router.get("/v2/role/cache-stats", dependencies=[Depends(require_auth)], response_model=CacheStatsResponse)
async def get_llm_cache_stats() -> dict[str, Any]:
    """Get LLM cache statistics."""
    cache = get_global_llm_cache()
    return cache.get_stats()


@router.post(
    "/v2/role/cache-clear",
    dependencies=[Depends(require_auth), Depends(require_role([UserRole.ADMIN, UserRole.DEVELOPER]))],
    response_model=CacheClearResponse,
)
async def clear_llm_cache() -> dict[str, Any]:
    """Clear the global LLM cache."""
    cache = get_global_llm_cache()
    cache.clear()
    return {"ok": True, "message": "Cache cleared"}


# ============================================================================
# Unified Role Chat Endpoints
# ============================================================================


def _validate_role(role: str) -> None:
    """Validate that the requested role is supported.

    Raises:
        StructuredHTTPException: If the role is not in the registered roles list.
    """
    supported = get_registered_roles()
    if role not in supported:
        raise StructuredHTTPException(
            status_code=400,
            code="UNSUPPORTED_ROLE",
            message=f"Role '{role}' is not supported",
            details={
                "supported_roles": supported,
            },
        )


@router.post("/v2/role/{role}/chat", dependencies=[Depends(require_auth)], response_model=RoleChatResponse)
async def role_chat(
    request: Request,
    role: str,
    payload: dict[str, Any],
    workspace: str = "",
) -> dict[str, Any]:
    """Chat with a registered LLM role (non-streaming).

    Returns:
        Response, thinking trace, and model metadata.
    """
    state = get_state(request)
    workspace = _workspace_for_role_request(state.settings, workspace, payload)
    scoped_state = _state_for_workspace(state, workspace)

    _validate_role(role)

    message = str(payload.get("message") or "").strip()
    if not message:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_REQUEST",
            message="message is required",
        )

    try:
        ensure_required_roles_ready(scoped_state, default_roles=[role])
    except StructuredHTTPException:
        raise
    except (RuntimeError, ValueError) as exc:
        raise StructuredHTTPException(
            status_code=409,
            code="LLM_NOT_READY",
            message="LLM runtime not ready",
        ) from exc

    try:
        result = await execute_role_chat_nonstreaming(
            role=role,
            message=message,
            workspace=workspace,
            payload=payload,
            default_domain="general",
            host_kind="role_chat_http",
        )
        return {"ok": True, "workspace": workspace, **result}
    except (RuntimeError, ValueError) as exc:
        raise StructuredHTTPException(
            status_code=500,
            code="GENERATION_FAILED",
            message=f"Generation failed: {exc}",
        ) from exc


# ============================================================================
# JetStream-backed Role Chat Endpoint (single realtime HTTP starter)
# ============================================================================


@router.post(
    "/v2/role/{role}/chat/jetstream",
    dependencies=[Depends(require_auth)],
    response_model=None,
)
async def role_chat_jetstream(
    request: Request,
    role: str,
    payload: dict[str, Any],
    workspace: str = "",
) -> dict[str, Any]:
    """Start a role chat turn and stream chunks via NAT JetStream WebSocket.

    Replaces the removed legacy HTTP streaming route. The HTTP
    response returns immediately with a ``session_id``; the LLM runs
    in the background and publishes every chunk (thinking_chunk /
    content_chunk / tool_call / tool_result / complete / error) to the
    JetStream subject ``hp.runtime.chat.<session_id>``.

    The front-end subscribes to ``chat:<session_id>`` over the existing
    ``/v2/ws/runtime`` WebSocket and decodes each RuntimeEventEnvelope
    whose ``channel`` equals ``chat:<session_id>``.

    Returns:
        ``{"session_id", "status", "channel", "subject", "transport"}``

    SECURITY:
    - Server-generated session id; clients cannot guess other users' ids.
    - Subject validated against the platform's SUBJECT_PATTERN.
    - Authorisation is unchanged from the legacy role chat path.
    """
    state = get_state(request)
    workspace = _workspace_for_role_request(state.settings, workspace, payload)
    scoped_state = _state_for_workspace(state, workspace)

    _validate_role(role)

    message = str(payload.get("message") or "").strip()
    if not message:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_REQUEST",
            message="message is required",
        )

    try:
        ensure_required_roles_ready(scoped_state, default_roles=[role])
    except StructuredHTTPException:
        raise
    except (RuntimeError, ValueError) as exc:
        raise StructuredHTTPException(
            status_code=409,
            code="LLM_NOT_READY",
            message="LLM runtime not ready",
        ) from exc

    # Server-generated session id; clients cannot inject or guess another
    # user's chat stream.
    session_id = _new_chat_session_id(role)
    channel = f"chat:{session_id}"
    subject = f"hp.runtime.chat.{session_id}"

    async def _run_chat() -> None:
        # Errors are best-effort reported via the chunk stream itself
        # (an ``error`` chunk with the underlying message).
        try:
            await execute_role_chat_jetstream(
                role=role,
                workspace=workspace,
                message=message,
                payload=payload,
                default_domain="general",
                host_kind="role_chat_jetstream",
                context=payload.get("context"),
                session_id=session_id,
            )
        except (RuntimeError, ValueError, asyncio.CancelledError) as exc:
            logger.warning("role_chat_jetstream background task failed: %s", exc)

    # Fire and forget; the response returns immediately and the LLM keeps
    # publishing chunks to JetStream for any subscribed front-end.
    task = asyncio.create_task(_run_chat())
    _JETSTREAM_CHAT_TASKS.add(task)
    task.add_done_callback(_JETSTREAM_CHAT_TASKS.discard)

    return {
        "session_id": session_id,
        "status": "started",
        "channel": channel,
        "subject": subject,
        "transport": "nat-jetstream",
    }


# Best-effort registry of in-flight chat tasks so the loop does not drop
# them mid-stream (see role_chat_jetstream endpoint above).
_JETSTREAM_CHAT_TASKS: set[asyncio.Task[Any]] = set()
