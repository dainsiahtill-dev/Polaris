"""Public service exports for `llm.provider_runtime` cell."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.cells.llm.provider_runtime.internal.cell_executor import (
    CellAIExecutor,
    CellAIRequest,
    CellAIResponse,
    ResponseNormalizer,
    TaskType,
    normalize_list,
    split_lines,
    truncate_text,
)
from polaris.cells.llm.provider_runtime.internal.provider_actions import run_provider_action
from polaris.cells.llm.provider_runtime.internal.providers import (
    get_provider_manager,
)
from polaris.cells.llm.provider_runtime.internal.runtime_invoke import (
    RuntimeProviderInvokeResult,
    invoke_role_runtime_provider,
    normalize_provider_type,
    resolve_provider_api_key,
)
from polaris.cells.llm.provider_runtime.internal.runtime_support import (
    get_role_runtime_provider_kind,
    is_codex_provider,
    is_role_runtime_supported,
)
from polaris.cells.llm.provider_runtime.public.contracts import (
    ILlmProviderRuntimeService,
    InvokeProviderActionCommandV1,
    InvokeRoleProviderCommandV1,
    LlmProviderRuntimeError,
    ProviderInvocationResultV1,
    QueryRoleRuntimeProviderSupportV1,
    UnsupportedProviderTypeError,
)

# Re-export BaseProvider, ProviderInfo, and ProviderManager from kernelone for backward compatibility
# Internal code should use AppLLMRuntimeAdapter.get_provider_instance() instead
from polaris.kernelone.llm.providers import BaseProvider, ProviderInfo, ProviderManager


def _to_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "to_dict") and callable(value.to_dict):  # pragma: no branch - tiny helper
        result = value.to_dict()
        if isinstance(result, Mapping):
            return dict(result)
    return {"value": value}


def execute_provider_action(command: InvokeProviderActionCommandV1) -> ProviderInvocationResultV1:
    """Execute provider action with contract-level error mapping."""
    try:
        payload = run_provider_action(
            action=command.action,  # type: ignore[arg-type]
            provider_type=command.provider_type,
            provider_cfg=dict(command.provider_cfg),
            api_key=command.api_key,
        )
        return ProviderInvocationResultV1(
            ok=True,
            status="ok",
            provider_kind=command.provider_type,
            payload=dict(payload),
        )
    except LlmProviderRuntimeError as exc:
        return ProviderInvocationResultV1(
            ok=False,
            status="failed",
            provider_kind=command.provider_type,
            payload={},
            error_code=exc.code,
            error_message=str(exc),
        )
    except (RuntimeError, ValueError) as exc:
        return ProviderInvocationResultV1(
            ok=False,
            status="failed",
            provider_kind=command.provider_type,
            payload={},
            error_code="provider_action_error",
            error_message=str(exc),
        )


def execute_role_provider(command: InvokeRoleProviderCommandV1) -> ProviderInvocationResultV1:
    """Invoke role provider runtime with contract-level error mapping."""
    try:
        result = invoke_role_runtime_provider(
            role=command.role,
            workspace=command.workspace,
            prompt=command.prompt,
            fallback_model=command.fallback_model,
            timeout=command.timeout,
            blocked_provider_types=command.blocked_provider_types,
        )
        payload = dict(_to_mapping(result))
        provider_kind = str(payload.get("provider_kind") or "generic")
        return ProviderInvocationResultV1(
            ok=True,
            status="ok",
            provider_kind=provider_kind,
            payload=payload,
        )
    except (RuntimeError, ValueError) as exc:
        return ProviderInvocationResultV1(
            ok=False,
            status="failed",
            provider_kind="generic",
            payload={},
            error_code="runtime_provider_invoke_error",
            error_message=str(exc),
        )


def query_role_provider_support(
    query: QueryRoleRuntimeProviderSupportV1,
    *,
    provider_id: str | None = None,
    provider_cfg: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Return support metadata for role/provider combination."""
    cfg = dict(provider_cfg or {})
    supported = is_role_runtime_supported(query.role, provider_id, cfg)
    kind = get_role_runtime_provider_kind(query.role, provider_id, cfg)
    return {
        "workspace": query.workspace,
        "role": query.role,
        "provider_id": provider_id,
        "provider_kind": kind,
        "supported": bool(supported),
    }


class LlmProviderRuntimeService(ILlmProviderRuntimeService):
    """Contract-first provider runtime facade."""

    async def invoke_provider_action(
        self,
        command: InvokeProviderActionCommandV1,
    ) -> ProviderInvocationResultV1:
        return execute_provider_action(command)

    async def invoke_role_provider(
        self,
        command: InvokeRoleProviderCommandV1,
    ) -> ProviderInvocationResultV1:
        return execute_role_provider(command)


__all__ = [
    "BaseProvider",
    "CellAIExecutor",
    "CellAIRequest",
    "CellAIResponse",
    "ILlmProviderRuntimeService",
    "InvokeProviderActionCommandV1",
    "InvokeRoleProviderCommandV1",
    "LlmProviderRuntimeService",
    "ProviderInfo",
    "ProviderInvocationResultV1",
    "ProviderManager",
    "QueryRoleRuntimeProviderSupportV1",
    "ResponseNormalizer",
    "RuntimeProviderInvokeResult",
    "TaskType",
    "UnsupportedProviderTypeError",
    "execute_provider_action",
    "execute_role_provider",
    "get_provider_manager",
    "get_role_runtime_provider_kind",
    "invoke_role_runtime_provider",
    "is_codex_provider",
    "is_role_runtime_supported",
    "normalize_list",
    "normalize_provider_type",
    "query_role_provider_support",
    "resolve_provider_api_key",
    "run_provider_action",
    "split_lines",
    "truncate_text",
]
