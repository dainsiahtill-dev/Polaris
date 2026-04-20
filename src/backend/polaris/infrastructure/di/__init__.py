"""Dependency Injection infrastructure for Polaris."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "DIContainer",
    "DIContainerScope",
    "ScopeContext",
    "cleanup_all_scopes",
    "create_kernel_audit_runtime",
    "create_metrics_collector",
    "create_omniscient_audit_bus",
    "create_provider_manager",
    "create_theme_manager",
    "create_tool_spec_registry",
    "get_container",
    "get_current_scope",
    "register_all_factories",
    "reset_container",
    "reset_kernel_audit_runtime_for_test",
    "reset_metrics_collector_for_test",
    "reset_omniscient_audit_bus_for_test",
    "reset_provider_manager_for_test",
    "reset_role_action_registry_for_test",
    "reset_role_profile_registry_for_test",
    "reset_theme_manager_for_test",
    "reset_tool_spec_registry_for_test",
]

_ATTR_TO_MODULE = {
    "DIContainer": "polaris.infrastructure.di.container",
    "get_container": "polaris.infrastructure.di.container",
    "reset_container": "polaris.infrastructure.di.container",
    "create_kernel_audit_runtime": "polaris.infrastructure.di.factories",
    "create_metrics_collector": "polaris.infrastructure.di.factories",
    "create_omniscient_audit_bus": "polaris.infrastructure.di.factories",
    "create_provider_manager": "polaris.infrastructure.di.factories",
    "create_theme_manager": "polaris.infrastructure.di.factories",
    "create_tool_spec_registry": "polaris.infrastructure.di.factories",
    "register_all_factories": "polaris.infrastructure.di.factories",
    "reset_kernel_audit_runtime_for_test": "polaris.infrastructure.di.factories",
    "reset_metrics_collector_for_test": "polaris.infrastructure.di.factories",
    "reset_omniscient_audit_bus_for_test": "polaris.infrastructure.di.factories",
    "reset_provider_manager_for_test": "polaris.infrastructure.di.factories",
    "reset_role_action_registry_for_test": "polaris.infrastructure.di.factories",
    "reset_role_profile_registry_for_test": "polaris.infrastructure.di.factories",
    "reset_theme_manager_for_test": "polaris.infrastructure.di.factories",
    "reset_tool_spec_registry_for_test": "polaris.infrastructure.di.factories",
    "DIContainerScope": "polaris.infrastructure.di.scope",
    "ScopeContext": "polaris.infrastructure.di.scope",
    "cleanup_all_scopes": "polaris.infrastructure.di.scope",
    "get_current_scope": "polaris.infrastructure.di.scope",
}


def __getattr__(name: str) -> Any:
    module_path = _ATTR_TO_MODULE.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_path)
    return getattr(module, name)
