"""Role Chat Router - 通用角色对话状态查询与对话接口

提供角色LLM配置状态查询接口，以及统一的角色对话（非流式/流式）接口。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.cells.llm.dialogue.public import (
    generate_role_response,
    generate_role_response_streaming,
    get_registered_roles,
)
from polaris.cells.llm.evaluation.public.service import load_llm_test_index
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
from polaris.kernelone.llm import config_store as llm_config
from polaris.kernelone.storage.io_paths import build_cache_root

from ._shared import (
    StructuredHTTPException,
    ensure_required_roles_ready,
    get_state,
    require_auth,
    require_role,
)

router = APIRouter()


@router.get("/v2/role/chat/ping", dependencies=[Depends(require_auth)], response_model=RoleChatPingResponse)
async def role_chat_ping() -> dict[str, Any]:
    """健康检查端点"""
    return {
        "status": "ok",
        "message": "Role Chat router is working",
        "supported_roles": get_registered_roles(),
    }


async def _load_llm_test_index_async(settings: Any) -> dict[str, Any]:
    """异步加载 LLM 测试索引（将同步文件 I/O 移到线程池）"""
    import asyncio

    return await asyncio.to_thread(load_llm_test_index, settings)


async def _load_llm_config_async(workspace: str, cache_root: str, settings: Any) -> dict[str, Any]:
    """异步加载 LLM 配置（将同步文件 I/O 移到线程池）"""
    import asyncio

    return await asyncio.to_thread(llm_config.load_llm_config, workspace, cache_root, settings)


@router.get("/v2/role/{role}/chat/status", dependencies=[Depends(require_auth)], response_model=RoleChatStatusResponse)
async def role_chat_status(
    request: Request,
    role: str,
) -> dict[str, Any]:
    """获取指定角色LLM配置状态

    Args:
        role: 角色标识 (pm, architect, director, qa, 等)
    """
    state = get_state(request)

    try:
        cache_root = build_cache_root(
            "",  # ramdisk_root (empty string as default)
            str(state.settings.workspace),
        )

        # 评测索引用于补充状态，不应误判为“未配置”。
        # 使用线程池执行文件 I/O 操作，避免阻塞事件循环
        index = await _load_llm_test_index_async(state.settings)
        role_status = (index.get("roles") or {}).get(role) if isinstance(index, dict) else None
        llm_test_ready = bool(isinstance(role_status, dict) and role_status.get("ready"))

        # 加载配置获取详细信息（使用线程池执行文件 I/O）
        config = await _load_llm_config_async(
            str(state.settings.workspace),
            cache_root,
            state.settings,
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
                "error": f"{role.upper()} role not configured",
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
                "error": f"{role.upper()} role provider or model not set",
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
                "error": f"Provider '{provider_id}' not found",
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
            "code": getattr(exc, "code", "internal_error"),
            "message": str(exc),
            "details": {
                "exception": traceback.format_exc(),
            },
        }


@router.get("/v2/role/chat/roles", dependencies=[Depends(require_auth)], response_model=RoleListResponse)
async def list_supported_roles() -> dict[str, Any]:
    """列出所有支持的角色"""
    return {
        "roles": get_registered_roles(),
        "count": len(get_registered_roles()),
    }


# ============================================================================
# LLM Events API - 实时 LLM 调用状态
# ============================================================================


@router.get("/v2/role/{role}/llm-events", dependencies=[Depends(require_auth)], response_model=RoleLLMEventsResponse)
async def get_role_llm_events(
    role: str,
    run_id: str | None = None,
    task_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """获取指定角色的 LLM 调用事件历史

    Args:
        role: 角色标识 (pm, architect, director, qa, chief_engineer)
    """
    from ..roles.events import get_global_emitter

    emitter = get_global_emitter()
    events = emitter.get_events(run_id=run_id, task_id=task_id, role=role, limit=limit)

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
        "events": [e.to_dict() for e in events],
        "stats": stats,
    }


@router.get("/v2/role/llm-events", dependencies=[Depends(require_auth)], response_model=AllLLMEventsResponse)
async def get_all_llm_events(
    run_id: str | None = None,
    task_id: str | None = None,
    role: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """获取所有角色的 LLM 调用事件历史"""
    from ..roles.events import get_global_emitter

    emitter = get_global_emitter()
    events = emitter.get_events(run_id=run_id, task_id=task_id, role=role, limit=limit)

    return {
        "events": [e.to_dict() for e in events],
        "count": len(events),
    }


@router.get("/v2/role/cache-stats", dependencies=[Depends(require_auth)], response_model=CacheStatsResponse)
async def get_llm_cache_stats() -> dict[str, Any]:
    """获取 LLM 缓存统计信息"""
    from ..roles.kernel_components import get_global_llm_cache

    cache = get_global_llm_cache()
    return cache.get_stats()


@router.post(
    "/v2/role/cache-clear",
    dependencies=[Depends(require_auth), Depends(require_role([UserRole.ADMIN, UserRole.DEVELOPER]))],
    response_model=CacheClearResponse,
)
async def clear_llm_cache() -> dict[str, Any]:
    """清空 LLM 缓存"""
    from ..roles.kernel_components import get_global_llm_cache

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
            message=f"Role '{role}' is not supported. Supported roles: {supported}",
        )


@router.post("/v2/role/{role}/chat", dependencies=[Depends(require_auth)], response_model=RoleChatResponse)
async def role_chat(
    request: Request,
    role: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """通过指定角色的 LLM 进行对话（非流式）

    Request:
        {
            "message": "用户消息",
            "context": {...}  # 可选的上下文信息
        }

    Response:
        {
            "ok": true,
            "response": "AI回复",
            "thinking": "思考过程（如果有）",
            "role": "pm",
            "model": "使用的模型",
            "provider": "使用的provider"
        }
    """
    state = get_state(request)

    _validate_role(role)

    message = str(payload.get("message") or "").strip()
    if not message:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_REQUEST",
            message="message is required",
        )

    try:
        ensure_required_roles_ready(state, default_roles=[role])
    except StructuredHTTPException:
        raise
    except (RuntimeError, ValueError) as exc:
        raise StructuredHTTPException(
            status_code=409,
            code="LLM_NOT_READY",
            message=str(exc),
        ) from exc

    try:
        result = await generate_role_response(
            workspace=str(state.settings.workspace),
            settings=state.settings,
            role=role,
            message=message,
            context=payload.get("context"),
        )
        return {"ok": True, **result}
    except (RuntimeError, ValueError) as exc:
        raise StructuredHTTPException(
            status_code=500,
            code="GENERATION_FAILED",
            message=str(exc),
        ) from exc


@router.post("/v2/role/{role}/chat/stream", dependencies=[Depends(require_auth)])
async def role_chat_stream(
    request: Request,
    role: str,
    payload: dict[str, Any],
) -> Any:
    """通过指定角色的 LLM 进行对话（流式 SSE）

    Request:
        {
            "message": "用户消息",
            "context": {...}  # 可选的上下文信息
        }

    Response: SSE stream with events:
        - thinking_chunk: 思考过程片段
        - content_chunk: 内容片段
        - complete: 完成事件，包含完整响应
        - error: 错误事件
        - complete: 结束标记
    """
    from polaris.delivery.http.routers.sse_utils import (
        create_sse_response,
        sse_event_generator,
    )

    state = get_state(request)

    _validate_role(role)

    message = str(payload.get("message") or "").strip()
    if not message:
        return create_sse_response(_error_sse_generator("message is required"))

    try:
        ensure_required_roles_ready(state, default_roles=[role])
    except StructuredHTTPException as exc:
        return create_sse_response(_error_sse_generator(str(exc.structured_message)))
    except (RuntimeError, ValueError) as exc:
        return create_sse_response(_error_sse_generator(str(exc)))

    async def _run_role_dialogue(queue: asyncio.Queue) -> None:
        """运行角色对话并输出到队列"""
        await generate_role_response_streaming(
            workspace=str(state.settings.workspace),
            settings=state.settings,
            role=role,
            message=message,
            output_queue=queue,
            context=payload.get("context"),
        )

    return create_sse_response(sse_event_generator(_run_role_dialogue, timeout=180.0))


async def _error_sse_generator(message: str) -> Any:
    """SSE 错误事件生成器"""
    yield f"event: error\ndata: {message}\n\n"
    yield "event: complete\ndata: {}\n\n"
