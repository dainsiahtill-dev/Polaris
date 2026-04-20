"""Public service exports for `llm.provider_config` cell."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import Any

from polaris.cells.llm.provider_config.internal.provider_context import (
    ProviderRequestContext,
    resolve_provider_request_context,
)
from polaris.cells.llm.provider_config.internal.settings_sync import sync_settings_from_llm
from polaris.cells.llm.provider_config.internal.test_context import (
    LlmTestExecutionContext,
    resolve_llm_test_execution_context,
)
from polaris.cells.llm.provider_config.public.contracts import (
    IProviderConfigService,
    LlmProviderConfigError,
    ProviderConfigResultV1,
    ProviderConfigValidationError,
    ProviderNotFoundError,
    ResolveLlmTestExecutionContextCommandV1,
    ResolveProviderContextCommandV1,
    RoleNotConfiguredError,
    SyncSettingsFromLlmCommandV1,
)


def _extract_workspace_and_cache_root(settings: Any) -> tuple[str, str]:
    """Extract workspace and cache_root strings from a settings object.

    This helper lives at the cell boundary (public layer) so that the internal
    module stays free of Settings coupling. It accesses only the two primitive
    attributes needed by internal functions.
    """
    workspace = getattr(settings, "workspace", "") or ""
    cache_root = getattr(settings, "ramdisk_root", "") or ""
    return str(workspace), str(cache_root)


def _to_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value) and not isinstance(value, type):
        payload = asdict(value)  # type: ignore[arg-type]
        if isinstance(payload, dict):
            return payload
    if isinstance(value, Mapping):
        return dict(value)
    return {"value": value}


def resolve_provider_context_contract(
    settings: Any,
    command: ResolveProviderContextCommandV1,
) -> ProviderConfigResultV1:
    """Resolve provider context and map to contract result."""
    try:
        workspace, cache_root = _extract_workspace_and_cache_root(settings)
        context = resolve_provider_request_context(
            workspace=workspace,
            cache_root=cache_root,
            provider_id=command.provider_id,
            api_key=command.api_key,
            headers=dict(command.headers) if command.headers else None,
        )
        return ProviderConfigResultV1(
            ok=True,
            workspace=command.workspace,
            provider_id=command.provider_id,
            provider_type=context.provider_type or "unknown",
            provider_cfg=dict(context.provider_cfg or {}),
        )
    except LlmProviderConfigError as exc:
        return ProviderConfigResultV1(
            ok=False,
            workspace=command.workspace,
            provider_id=command.provider_id,
            provider_type="unknown",
            provider_cfg={},
            error_code=exc.code,
            error_message=str(exc),
        )
    except (RuntimeError, ValueError) as exc:
        return ProviderConfigResultV1(
            ok=False,
            workspace=command.workspace,
            provider_id=command.provider_id,
            provider_type="unknown",
            provider_cfg={},
            error_code="provider_context_error",
            error_message=str(exc),
        )


def resolve_test_execution_context_contract(
    settings: Any,
    command: ResolveLlmTestExecutionContextCommandV1,
) -> Mapping[str, Any]:
    """Resolve test execution context and normalize to mapping."""
    workspace, cache_root = _extract_workspace_and_cache_root(settings)
    context = resolve_llm_test_execution_context(
        workspace=workspace,
        cache_root=cache_root,
        payload=command.payload,
    )
    return _to_dict(context)


class LlmProviderConfigService(IProviderConfigService):
    """Contract-first provider config facade."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    async def resolve_provider_context(
        self,
        command: ResolveProviderContextCommandV1,
    ) -> ProviderConfigResultV1:
        return resolve_provider_context_contract(self._settings, command)

    async def resolve_test_context(
        self,
        command: ResolveLlmTestExecutionContextCommandV1,
    ) -> Mapping[str, Any]:
        return resolve_test_execution_context_contract(self._settings, command)

    def sync_settings(self, command: SyncSettingsFromLlmCommandV1) -> None:
        sync_settings_from_llm(self._settings, dict(command.llm_config))


__all__ = [
    "IProviderConfigService",
    "LlmProviderConfigError",
    "LlmProviderConfigService",
    "LlmTestExecutionContext",
    "ProviderConfigResultV1",
    "ProviderConfigValidationError",
    "ProviderNotFoundError",
    "ProviderRequestContext",
    "ResolveLlmTestExecutionContextCommandV1",
    "ResolveProviderContextCommandV1",
    "RoleNotConfiguredError",
    "SyncSettingsFromLlmCommandV1",
    "resolve_llm_test_execution_context",
    "resolve_provider_context_contract",
    "resolve_provider_request_context",
    "resolve_test_execution_context_contract",
    "sync_settings_from_llm",
]
