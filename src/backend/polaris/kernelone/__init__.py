"""KernelOne technical runtime layer for Polaris."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "_runtime_config",
    "audit",
    "auth_context",
    "common",
    "constants",
    "contracts",
    "db",
    "effect",
    "errors",
    "events",
    "exceptions",
    "fs",
    "llm",
    "locks",
    "resilience",
    "resource",
    "runtime",
    "scheduler",
    "state_machine",
    "storage",
    "tool_execution",
    "tool_state",
    "trace",
    "workflow",
]

_LAZY_MODULES = {
    "_runtime_config": "polaris.kernelone._runtime_config",
    "audit": "polaris.kernelone.audit",
    "auth_context": "polaris.kernelone.auth_context",
    "common": "polaris.kernelone.common",
    "constants": "polaris.kernelone.constants",
    "contracts": "polaris.kernelone.contracts",
    "db": "polaris.kernelone.db",
    "effect": "polaris.kernelone.effect",
    "errors": "polaris.kernelone.errors",
    "events": "polaris.kernelone.events",
    "exceptions": "polaris.kernelone.exceptions",
    "fs": "polaris.kernelone.fs",
    "llm": "polaris.kernelone.llm",
    "locks": "polaris.kernelone.locks",
    "resilience": "polaris.kernelone.resilience",
    "resource": "polaris.kernelone.resource",
    "runtime": "polaris.kernelone.runtime",
    "scheduler": "polaris.kernelone.scheduler",
    "state_machine": "polaris.kernelone.state_machine",
    "storage": "polaris.kernelone.storage",
    "tool_execution": "polaris.kernelone.tool_execution",
    "tool_state": "polaris.kernelone.tool_state",
    "trace": "polaris.kernelone.trace",
    "workflow": "polaris.kernelone.workflow",
}


def __getattr__(name: str) -> Any:
    module_path = _LAZY_MODULES.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return import_module(module_path)
