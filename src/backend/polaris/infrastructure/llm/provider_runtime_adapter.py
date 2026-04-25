"""Infrastructure adapter bridging KernelOne LLM runtime to Polaris LLM modules.

This module provides the AppLLMRuntimeAdapter that integrates with the DI container.
Settings must be obtained from the composition root (DI container), not instantiated directly.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import TYPE_CHECKING, Any, Coroutine

from polaris.kernelone.llm import KernelLLMRuntimeAdapter, config_store as llm_config

logger = logging.getLogger(__name__)


class AppLLMRuntimeAdapter(KernelLLMRuntimeAdapter):
    """Infrastructure adapter bridging KernelOne LLM runtime to Polaris LLM modules."""

    def get_role_model(self, role: str) -> tuple[str, str]:
        from polaris.kernelone.llm.runtime_config import get_role_model

        provider_id, model = get_role_model(role)
        return str(provider_id or "").strip(), str(model or "").strip()

    def load_provider_config(
        self,
        *,
        workspace: str,
        provider_id: str,
        settings: Any | None = None,
    ) -> dict[str, Any]:
        """Load provider configuration for a given role and provider.

        Args:
            workspace: Workspace path.
            provider_id: Provider identifier.
            settings: Optional Settings instance. If not provided, the adapter
                must be resolvable from the DI container.

        Settings下沉 prevention: Callers in service / cell layers must pass
        settings explicitly rather than letting this adapter instantiate it.
        """
        from polaris.kernelone.storage.io_paths import build_cache_root

        if settings is None:
            settings = _resolve_settings_from_di()

        cache_root = build_cache_root(getattr(settings, "ramdisk_root", "") or "", workspace)
        llm_payload = llm_config.load_llm_config(workspace, cache_root, settings=settings)
        providers = llm_payload.get("providers") if isinstance(llm_payload, dict) else {}
        if not isinstance(providers, dict):
            return {}
        provider_cfg = providers.get(provider_id)
        return dict(provider_cfg) if isinstance(provider_cfg, dict) else {}

    def get_provider_instance(self, provider_type: str) -> Any:
        from polaris.infrastructure.llm.providers import provider_manager

        return provider_manager.get_provider_instance(provider_type)

    def record_provider_failure(self, provider_type: str) -> None:
        from polaris.infrastructure.llm.providers import provider_manager

        try:
            provider_manager.record_provider_failure(provider_type)
        except (RuntimeError, ValueError) as exc:
            logger.debug("record_provider_failure failed: %s", exc)


def _resolve_settings_from_di() -> Any:
    """Resolve Settings from the composition root.

    This adapter must not instantiate ad-hoc Settings objects. If a caller is
    outside the DI graph, it must pass ``settings`` explicitly.
    """
    try:
        from polaris.bootstrap.config import Settings as SettingsCls
        from polaris.infrastructure.di.container import DIContainer, get_container

        container = get_container()
        if inspect.isawaitable(container):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                # Cast container to Coroutine for asyncio.run
                from typing import cast

                container = asyncio.run(cast("Coroutine[Any, Any, DIContainer]", container))  # type: ignore[arg-type]
            else:
                close = getattr(container, "close", None)
                if callable(close):
                    close()
                raise RuntimeError(
                    "Cannot resolve Settings synchronously from an async DI container. Pass settings explicitly."
                )
        # Type narrowing: after the above check, container is guaranteed to be DIContainer
        if not isinstance(container, DIContainer):
            raise RuntimeError("Container is not a DIContainer instance.")
        if container.has_registration(SettingsCls):
            return container.resolve(SettingsCls)
        raise RuntimeError("Settings is not registered in the DI container.")
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.debug("Failed to resolve Settings from DI container: %s", exc)
        raise RuntimeError("Settings resolution via DI failed.") from exc
