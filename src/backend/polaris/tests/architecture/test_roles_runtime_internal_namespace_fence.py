from __future__ import annotations

import importlib
import warnings
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_INTERNAL_INIT = BACKEND_ROOT / "polaris" / "cells" / "roles" / "runtime" / "internal" / "__init__.py"


def test_roles_runtime_internal_root_is_namespace_only() -> None:
    """The internal package root must not act as a compatibility facade."""

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        module = importlib.import_module("polaris.cells.roles.runtime.internal")
        module = importlib.reload(module)

    deprecation_warnings = [warning for warning in caught if issubclass(warning.category, DeprecationWarning)]
    assert deprecation_warnings == []
    assert module.__all__ == []
    assert not hasattr(module, "__deprecated__")
    assert not hasattr(module, "RoleExecutionKernel")
    assert not hasattr(module, "RoleExecutionRequest")
    assert not hasattr(module, "RoleExecutionResponse")


def test_roles_runtime_internal_active_submodules_remain_importable() -> None:
    """Removing root re-exports must not remove active internal implementation modules."""

    process_service = importlib.import_module("polaris.cells.roles.runtime.internal.process_service")
    session_orchestrator = importlib.import_module("polaris.cells.roles.runtime.internal.session_orchestrator")

    assert hasattr(process_service, "spawn_process")
    assert hasattr(process_service, "terminate_process")
    assert hasattr(session_orchestrator, "RoleSessionOrchestrator")


def test_roles_runtime_internal_root_does_not_reintroduce_deprecated_exports() -> None:
    source = RUNTIME_INTERNAL_INIT.read_text(encoding="utf-8")

    assert "DeprecationWarning" not in source
    assert "__deprecated__" not in source
    assert "RoleExecutionRequest" not in source
    assert "RoleExecutionResponse" not in source
    assert "RoleExecutionKernel" not in source
