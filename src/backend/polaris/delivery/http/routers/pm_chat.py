"""PM Role Chat Router - 通过统一的AI平台层进行PM对话

与interview和docs_dialogue保持一致，复用平台层基础设施。
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.cells.llm.evaluation.public.service import load_llm_test_index
from polaris.delivery.http.schemas.common import PMChatPingResponse, PMChatStatusResponse
from polaris.delivery.http.workspace import active_workspace_value, requested_or_active_workspace
from polaris.kernelone.llm import config_store as llm_config
from polaris.kernelone.storage.io_paths import build_cache_root

from ._shared import StructuredHTTPException, get_state, require_auth
from .role_runtime_chat import execute_role_chat_nonstreaming

router = APIRouter()


def _workspace_value(settings: Any) -> str:
    """Resolve the active desktop workspace from compatible settings fields."""
    return active_workspace_value(settings)


def _context_workspace(payload: dict[str, Any]) -> str:
    """Read an explicit workspace from a PM-chat payload context."""
    context = payload.get("context")
    if not isinstance(context, dict):
        return ""
    workspace = context.get("workspace")
    return workspace.strip() if isinstance(workspace, str) else ""


def _workspace_for_pm_request(settings: Any, requested: str = "", payload: dict[str, Any] | None = None) -> str:
    """Resolve workspace from query/body context before falling back to active desktop state.

    Mirrors ``role_chat._workspace_for_role_request`` so a client setting
    ``context.workspace`` (or a ``workspace`` query param) is honored instead of
    being silently ignored and run against the wrong workspace.
    """
    return requested_or_active_workspace(settings, requested or _context_workspace(payload or {}))


@router.get("/v2/pm/chat/ping", response_model=PMChatPingResponse, dependencies=[Depends(require_auth)])
def pm_chat_ping() -> dict[str, str]:
    """Health check for the PM chat router."""
    return {"status": "ok", "message": "PM Chat router is working", "role": "pm"}


@router.post("/v2/pm/chat", dependencies=[Depends(require_auth)])
async def pm_chat(request: Request, payload: dict[str, Any], workspace: str = "") -> dict[str, Any]:
    """Chat with the PM role LLM (non-streaming).

    Returns:
        Response, thinking trace, and model metadata.
    """
    state = get_state(request)
    workspace = _workspace_for_pm_request(state.settings, workspace, payload)

    message = str(payload.get("message") or "").strip()
    if not message:
        raise StructuredHTTPException(
            status_code=422,
            code="MISSING_MESSAGE",
            message="message is required",
        )

    try:
        result = await execute_role_chat_nonstreaming(
            role="pm",
            workspace=workspace,
            message=message,
            payload=payload,
            default_domain="document",
            host_kind="pm_chat_http",
        )
        return {"ok": True, **result}

    except (RuntimeError, ValueError) as exc:
        raise StructuredHTTPException(
            status_code=500,
            code="ROLE_RESPONSE_ERROR",
            message="Generation failed",
        ) from exc


@router.get("/v2/pm/chat/status", response_model=PMChatStatusResponse, dependencies=[Depends(require_auth)])
def pm_chat_status(request: Request) -> dict[str, Any]:
    """Get PM role LLM configuration readiness.

    Returns:
        Ready state, provider info, and debug details.
    """
    state = get_state(request)
    workspace = _workspace_value(state.settings)

    try:
        cache_root = build_cache_root(state.settings.ramdisk_root or "", workspace)

        # 评测索引用于补充状态，不应误判为“未配置”。
        index = load_llm_test_index(workspace)
        role_status = (index.get("roles") or {}).get("pm") if isinstance(index, dict) else None
        llm_test_ready = bool(isinstance(role_status, dict) and role_status.get("ready"))

        # 加载配置获取详细信息
        config = llm_config.load_llm_config(workspace, cache_root, settings=state.settings)

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
                message="Provider not found",
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
            message="Status check failed",
            details={
                "exception": traceback.format_exc(),
            },
        ) from exc
