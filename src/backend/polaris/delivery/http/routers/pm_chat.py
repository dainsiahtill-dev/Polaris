"""PM Role Chat Router - 通过统一的AI平台层进行PM对话

与interview和docs_dialogue保持一致，复用平台层基础设施。
"""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.cells.llm.dialogue.public import generate_role_response, generate_role_response_streaming
from polaris.cells.llm.evaluation.public.service import load_llm_test_index
from polaris.delivery.http.routers.sse_utils import (
    create_sse_response,
    sse_event_generator,
)
from polaris.delivery.http.schemas.common import PMChatPingResponse, PMChatStatusResponse
from polaris.kernelone.llm import config_store as llm_config
from polaris.kernelone.storage.io_paths import build_cache_root

from ._shared import StructuredHTTPException, get_state, require_auth

router = APIRouter()


@router.get("/v2/pm/chat/ping", response_model=PMChatPingResponse, dependencies=[Depends(require_auth)])
def pm_chat_ping() -> dict[str, str]:
    """简单的ping测试端点"""
    return {"status": "ok", "message": "PM Chat router is working", "role": "pm"}


@router.post("/v2/pm/chat", dependencies=[Depends(require_auth)])
async def pm_chat(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """通过PM角色的LLM进行对话（非流式）

    Request:
        {
            "message": "用户消息",
            "context": {...}  # 可选的上下文信息
        }

    Response:
        {
            "response": "AI回复",
            "thinking": "思考过程（如果有）",
            "role": "pm",
            "model": "使用的模型",
            "provider": "使用的provider"
        }
    """
    state = get_state(request)

    message = str(payload.get("message") or "").strip()
    if not message:
        raise StructuredHTTPException(
            status_code=422,
            code="MISSING_MESSAGE",
            message="message is required",
        )

    try:
        result = await generate_role_response(
            workspace=str(state.settings.workspace),
            settings=state.settings,
            role="pm",
            message=message,
            context=payload.get("context"),
        )
        return {"ok": True, **result}

    except (RuntimeError, ValueError) as exc:
        raise StructuredHTTPException(
            status_code=500,
            code="ROLE_RESPONSE_ERROR",
            message=str(exc),
        ) from exc


@router.post("/v2/pm/chat/stream", dependencies=[Depends(require_auth)])
async def pm_chat_stream(request: Request, payload: dict[str, Any]):
    """通过PM角色的LLM进行对话（流式SSE）

    使用统一的sse_event_generator，与interview和docs保持一致。

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
        - done: 结束标记
    """
    state = get_state(request)

    message = str(payload.get("message") or "").strip()
    if not message:
        return create_sse_response(_error_generator("message is required"))

    async def _run_pm_dialogue(queue: asyncio.Queue) -> None:
        """运行PM对话并输出到队列"""
        await generate_role_response_streaming(
            workspace=str(state.settings.workspace),
            settings=state.settings,
            role="pm",
            message=message,
            output_queue=queue,
            context=payload.get("context"),
        )

    return create_sse_response(sse_event_generator(_run_pm_dialogue, timeout=180.0))


async def _error_generator(message: str):
    """错误事件生成器"""
    yield f"event: error\ndata: {message}\n\n"
    yield "event: complete\ndata: {}\n\n"


@router.get("/v2/pm/chat/status", response_model=PMChatStatusResponse, dependencies=[Depends(require_auth)])
def pm_chat_status(request: Request) -> dict[str, Any]:
    """获取PM角色LLM配置状态"""
    state = get_state(request)

    try:
        cache_root = build_cache_root(state.settings.ramdisk_root or "", str(state.settings.workspace))

        # 评测索引用于补充状态，不应误判为“未配置”。
        index = load_llm_test_index(state.settings)
        role_status = (index.get("roles") or {}).get("pm") if isinstance(index, dict) else None
        llm_test_ready = bool(isinstance(role_status, dict) and role_status.get("ready"))

        # 加载配置获取详细信息
        config = llm_config.load_llm_config(str(state.settings.workspace), cache_root, settings=state.settings)

        roles_raw = config.get("roles")
        providers_raw = config.get("providers")
        roles: dict[str, Any] = roles_raw if isinstance(roles_raw, dict) else {}
        providers: dict[str, Any] = providers_raw if isinstance(providers_raw, dict) else {}

        pm_role = roles.get("pm")
        if not isinstance(pm_role, dict):
            raise StructuredHTTPException(
                status_code=409,
                code="PM_ROLE_NOT_CONFIGURED",
                message="PM role not configured",
                details={
                    "roles_keys": list(roles.keys()) if roles else None,
                },
            )

        provider_id = str(pm_role.get("provider_id") or "").strip()
        model = str(pm_role.get("model") or "").strip()

        if not provider_id or not model:
            raise StructuredHTTPException(
                status_code=409,
                code="PM_ROLE_PROVIDER_OR_MODEL_NOT_SET",
                message="PM role provider or model not set",
                details={
                    "pm_role": pm_role,
                    "provider_id": provider_id if provider_id else "(empty)",
                    "model": model if model else "(empty)",
                },
            )

        provider_cfg = providers.get(provider_id)
        if not isinstance(provider_cfg, dict):
            raise StructuredHTTPException(
                status_code=409,
                code="PROVIDER_NOT_FOUND",
                message=f"Provider '{provider_id}' not found",
                details={
                    "pm_role": pm_role,
                    "available_providers": list(providers.keys()),
                },
            )

        return {
            "ready": True,
            "configured": True,
            "llm_test_ready": llm_test_ready,
            "role_config": {
                "provider_id": provider_id,
                "model": model,
                "profile": pm_role.get("profile"),
            },
            "provider_type": provider_cfg.get("type"),
            "debug": {
                "is_role_ready": llm_test_ready,
                "roles_keys": list(roles.keys()),
            },
        }

    except (RuntimeError, ValueError) as exc:
        import traceback

        raise StructuredHTTPException(
            status_code=500,
            code="STATUS_CHECK_ERROR",
            message=str(exc),
            details={
                "exception": traceback.format_exc(),
            },
        ) from exc
