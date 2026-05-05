"""Internal module exports for `llm.evaluation`.

Uses lazy loading via __getattr__ to avoid circular imports with
roles.runtime at module import time.
"""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "EvaluationRunner",
    "run_readiness_tests",
    "run_readiness_tests_streaming",
]

_INTERNAL_MODULES = {
    "EvaluationRunner": "polaris.cells.llm.evaluation.internal.runner",
    "run_readiness_tests": "polaris.cells.llm.evaluation.internal.readiness_tests",
    "run_readiness_tests_streaming": "polaris.cells.llm.evaluation.internal.readiness_tests",
}


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name = _INTERNAL_MODULES[name]
    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
