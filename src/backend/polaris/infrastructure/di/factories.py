"""Factory functions for DI-enabled singleton creation.

This module provides factory functions for creating instances that
were previously implemented as global singletons. These factories
support Dependency Injection (DI) pattern for better test isolation.

Usage:
    # In bootstrap/assembly.py
    from polaris.infrastructure.di.factories import (
        create_tool_spec_registry,
        create_theme_manager,
        create_metrics_collector,
        create_kernel_audit_runtime,
        create_omniscient_audit_bus,
    )

    container.register_singleton(ToolSpecRegistry, create_tool_spec_registry)
    container.register_singleton(ThemeManager, create_theme_manager)
    container.register_singleton(MetricsCollector, create_metrics_collector)

    # In tests/conftest.py
    @pytest.fixture
    def tool_spec_registry():
        return ToolSpecRegistry()  # Fresh instance for each test
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.public.service import MetricsCollector
    from polaris.delivery.cli.textual.styles import ThemeManager
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    from .container import DIContainer


# =============================================================================
# ToolSpecRegistry Factory
# =============================================================================


def create_tool_spec_registry(_: DIContainer) -> type[ToolSpecRegistry]:
    """Factory function for ToolSpecRegistry.

    Creates a fresh ToolSpecRegistry instance for DI injection.
    Note: ToolSpecRegistry uses class-level storage, so this factory
    triggers the lazy migration from contracts.py specs.

    Args:
        _: DIContainer (unused, for signature compatibility).

    Returns:
        A ToolSpecRegistry class reference (class-level registry pattern).
    """
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry, migrate_from_contracts_specs

    # Ensure migration from contracts.py is complete
    migrate_from_contracts_specs()

    # ToolSpecRegistry is a class-level registry, not an instance singleton
    # Return the class itself for type registration
    return ToolSpecRegistry


def reset_tool_spec_registry_for_test() -> None:
    """Reset ToolSpecRegistry for test isolation.

    Clears all registered tool specs to provide a clean slate for tests.
    Also resets the cached registry reference in contracts.py to ensure
    re-initialization on next access.
    """
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    # Clear the registry data for a clean test slate
    ToolSpecRegistry.clear()


# =============================================================================
# ThemeManager Factory
# =============================================================================


def create_theme_manager(_: DIContainer) -> ThemeManager:
    """Factory function for ThemeManager.

    Creates a fresh ThemeManager instance for DI injection.
    Unlike the singleton get_instance(), this always creates a new instance.

    Args:
        _: DIContainer (unused, for signature compatibility).

    Returns:
        A new ThemeManager instance.
    """
    from polaris.delivery.cli.textual.styles import ThemeManager

    return ThemeManager()


def reset_theme_manager_for_test() -> None:
    """Reset ThemeManager singleton for test isolation.

    Keep reset side-effect free for unrelated tests: do not import the textual
    UI stack if it is not already loaded.
    """
    import sys

    module = sys.modules.get("polaris.delivery.cli.textual.styles")
    if module is None:
        return
    theme_manager_cls = getattr(module, "ThemeManager", None)
    if theme_manager_cls is not None:
        theme_manager_cls._instance = None


# =============================================================================
# MetricsCollector Factory
# =============================================================================


def create_metrics_collector(_: DIContainer) -> MetricsCollector:
    """Factory function for MetricsCollector.

    Creates a fresh MetricsCollector instance for DI injection.
    Also resets the global metric objects for clean test state.

    Args:
        _: DIContainer (unused, for signature compatibility).

    Returns:
        A new MetricsCollector instance.
    """
    from polaris.cells.roles.kernel.public.service import MetricsCollector

    # Reset global metrics for clean state
    MetricsCollector.reset()
    with MetricsCollector._lock:
        MetricsCollector._instance = None

    return MetricsCollector()


def reset_metrics_collector_for_test() -> None:
    """Reset MetricsCollector for test isolation.

    Clears the singleton instance and resets all global metric objects.
    """
    from polaris.cells.roles.kernel.public.service import MetricsCollector

    MetricsCollector.reset()
    with MetricsCollector._lock:
        MetricsCollector._instance = None


def reset_role_action_registry_for_test() -> None:
    """Reset RoleActionRegistry for test isolation."""
    from polaris.cells.roles.kernel.public.service import reset_role_action_registry_for_test as _reset

    _reset()


# =============================================================================
# DI Registration Helper
# =============================================================================


def register_all_factories(container: DIContainer) -> None:
    """Register all singleton factories in the DI container.

    This function provides a convenient way to register all
    factory functions at once during application bootstrap.

    Args:
        container: The DI container to register factories in.
    """
    from polaris.cells.roles.kernel.public.service import MetricsCollector
    from polaris.delivery.cli.textual.styles import ThemeManager
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    # Register class-level registries (ToolSpecRegistry)
    # ToolSpecRegistry uses class-level singleton pattern, returns class not instance
    container.register_singleton(ToolSpecRegistry, create_tool_spec_registry)  # type: ignore[arg-type]

    # Register instance singletons
    container.register_singleton(ThemeManager, create_theme_manager)
    container.register_singleton(MetricsCollector, create_metrics_collector)

    # Note: KernelAuditRuntime and OmniscientAuditBus are multi-instance
    # singletons (per runtime_root / per name), so they are not registered
    # as singletons in the container. Use their get_instance() methods directly.


# No-op stubs for functions removed during refactor but still referenced by
# polaris.infrastructure.di.__init__ lazy loader and tests/conftest.py.
# TODO(2026-04-17): Clean up __init__.py mapping and conftest.py once these
# singletons have canonical reset paths.


def create_kernel_audit_runtime(runtime_root: Any = None) -> Any:
    """Stub: KernelAuditRuntime is now a multi-instance singleton; use get_instance()."""
    raise NotImplementedError("create_kernel_audit_runtime is deprecated; use KernelAuditRuntime.get_instance()")


def create_omniscient_audit_bus(name: str = "default") -> Any:
    """Stub: OmniscientAuditBus is now a multi-instance singleton; use get_instance()."""
    raise NotImplementedError("create_omniscient_audit_bus is deprecated; use OmniscientAuditBus.get_instance()")


def create_provider_manager() -> Any:
    """Stub: ProviderManager is no longer a DI singleton."""
    raise NotImplementedError("create_provider_manager is deprecated")


def reset_kernel_audit_runtime_for_test() -> None:
    """Stub: no-op for test compatibility."""


def reset_omniscient_audit_bus_for_test() -> None:
    """Stub: no-op for test compatibility."""


def reset_provider_manager_for_test() -> None:
    """Stub: no-op for test compatibility."""


def reset_role_profile_registry_for_test() -> None:
    """Stub: delegates to canonical location if available."""
    try:
        from polaris.cells.roles.profile.public.service import reset_role_profile_registry_for_test as _reset

        _reset()
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "create_kernel_audit_runtime",
    "create_metrics_collector",
    "create_omniscient_audit_bus",
    "create_provider_manager",
    "create_theme_manager",
    "create_tool_spec_registry",
    "register_all_factories",
    "reset_kernel_audit_runtime_for_test",
    "reset_metrics_collector_for_test",
    "reset_omniscient_audit_bus_for_test",
    "reset_provider_manager_for_test",
    "reset_role_action_registry_for_test",
    "reset_role_profile_registry_for_test",
    "reset_theme_manager_for_test",
    "reset_tool_spec_registry_for_test",
]
