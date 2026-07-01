"""Architecture fence for retired dependency-injection factory stubs."""

from __future__ import annotations

import ast
from pathlib import Path

from polaris.infrastructure import di
from polaris.infrastructure.di import factories

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
RETIRED_DI_FACTORY_EXPORTS = frozenset(
    {
        "create_kernel_audit_runtime",
        "create_omniscient_audit_bus",
        "create_provider_manager",
        "reset_kernel_audit_runtime_for_test",
        "reset_omniscient_audit_bus_for_test",
        "reset_provider_manager_for_test",
    }
)


def _function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def _production_python_files() -> list[Path]:
    return [
        path
        for path in sorted(POLARIS_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
        and "tests" not in path.parts
        and "generated" not in path.parts
        and not path.name.startswith("test_")
    ]


def test_di_public_boundary_does_not_export_dead_factories() -> None:
    """DI public exports must not preserve removed NotImplemented factory stubs."""
    assert not (set(di.__all__) & RETIRED_DI_FACTORY_EXPORTS)
    assert not (set(getattr(factories, "__all__", ())) & RETIRED_DI_FACTORY_EXPORTS)
    assert not (set(getattr(di, "_ATTR_TO_MODULE", {})) & RETIRED_DI_FACTORY_EXPORTS)


def test_di_factories_module_does_not_define_dead_stubs() -> None:
    """Removed DI factories must not return as no-op or NotImplemented stubs."""
    source_path = POLARIS_ROOT / "infrastructure/di/factories.py"
    assert not (_function_names(source_path) & RETIRED_DI_FACTORY_EXPORTS)


def test_production_code_does_not_import_dead_di_factories() -> None:
    """Production code must use canonical singleton owners instead of dead DI stubs."""
    violations: list[str] = []
    for path in _production_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module not in {"polaris.infrastructure.di", "polaris.infrastructure.di.factories"}:
                continue
            imported = {alias.name for alias in node.names} & RETIRED_DI_FACTORY_EXPORTS
            if imported:
                rel = path.relative_to(BACKEND_ROOT).as_posix()
                violations.append(f"{rel}: {node.module} import {sorted(imported)}")

    assert violations == [], (
        "Production code must use canonical KernelAuditRuntime/OmniscientAuditBus/"
        "ProviderManager owners instead of dead DI factory stubs:\n" + "\n".join(violations)
    )
