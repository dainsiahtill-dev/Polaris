"""G-4 re-export parity gate for KernelOne LLM contracts.

This module enforces that:
1. All LLM core types live in contracts/core.py (single source of truth).
2. engine/contracts.py and toolkit/contracts.py re-export them without modification.
3. Consumer files (executor.py, model_catalog.py) import from contracts,
   never directly from contracts.core.
4. StreamEventType values are identical across all re-export layers.

Audit target: polaris/kernelone/llm/
"""

from __future__ import annotations

import ast
from pathlib import Path

from polaris.kernelone.llm.contracts import core as core_contracts
from polaris.kernelone.llm.engine import contracts as engine_contracts
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.toolkit import contracts as toolkit_contracts

BACKEND_ROOT = Path(__file__).resolve().parents[3]
LLM_ENGINE_ROOT = BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "engine"
POLARIS_ROOT = BACKEND_ROOT / "polaris"
ROLES_KERNEL_ROOT = BACKEND_ROOT / "polaris" / "cells" / "roles" / "kernel"
CORE_CONTRACT_OWNER = BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "contracts" / "core.py"
ALLOWED_CORE_CONTRACT_IMPORTERS = {
    BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "contracts" / "__init__.py",
    BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "engine" / "contracts.py",
    BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "toolkit" / "contracts.py",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helper: collect names imported from a specific module in a specific file.
# ─────────────────────────────────────────────────────────────────────────────


def _imported_names(file_path: Path, *, level: int, module: str) -> set[str]:
    """Return the set of names that file_path imports from the given module."""
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(file_path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == level and node.module == module:
            names.update(alias.name for alias in node.names)
    return names


def _imports_forbidden_module(file_path: Path, forbidden_module: str) -> bool:
    """Return whether file_path imports forbidden_module through Python imports."""

    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(file_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == forbidden_module or alias.name.startswith(f"{forbidden_module}."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == forbidden_module or module.startswith(f"{forbidden_module}."):
                return True
    return False


def _production_python_files(root: Path) -> list[Path]:
    """Return non-test Python files under root for architecture boundary checks."""

    files: list[Path] = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts or path.name.startswith("test_"):
            continue
        files.append(path)
    return files


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: core import sanity
# ─────────────────────────────────────────────────────────────────────────────


def test_executor_module_import_succeeds() -> None:
    assert AIExecutor.__name__ == "AIExecutor"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: type identity — engine.contracts re-exports are the same objects
# ─────────────────────────────────────────────────────────────────────────────


def test_stream_event_type_reexport_identity() -> None:
    """engine.contracts must re-export (not redefine) StreamEventType."""
    assert engine_contracts.StreamEventType is core_contracts.StreamEventType


def test_toolkit_contracts_stream_event_type_identity() -> None:
    """toolkit.contracts must re-export the same StreamEventType."""
    assert toolkit_contracts.StreamEventType is core_contracts.StreamEventType


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: engine/contracts.__all__ covers all contracts.core types
# ─────────────────────────────────────────────────────────────────────────────


def test_engine_contracts_all_includes_core_types() -> None:
    """engine/contracts.py __all__ must list every type from contracts.core.__all__.

    Without __all__ the re-export imports are invisible to ruff --fix,
    which will delete them as "unused". This test detects that regression.
    """
    shared = set(core_contracts.__all__)
    engine_all = set(getattr(engine_contracts, "__all__", ()))
    missing = shared - engine_all
    assert not missing, (
        f"engine/contracts.py __all__ is missing re-exported shared types: {sorted(missing)}. "
        "Add them to __all__ to prevent ruff --fix from removing the imports."
    )


def test_toolkit_contracts_all_includes_core_types() -> None:
    """toolkit/contracts.py __all__ must list every core contract type."""

    shared = set(core_contracts.__all__)
    toolkit_all = set(getattr(toolkit_contracts, "__all__", ()))
    missing = shared - toolkit_all
    assert not missing, (
        f"toolkit/contracts.py __all__ is missing re-exported shared types: {sorted(missing)}. "
        "Add them to __all__ so toolkit consumers do not drift from contracts.core."
    )


def test_core_contract_reexport_identity_across_engine_and_toolkit() -> None:
    """Every core contract must be the same object through both re-export layers."""

    mismatches: list[str] = []
    for name in core_contracts.__all__:
        shared_obj = getattr(core_contracts, name)
        engine_obj = getattr(engine_contracts, name, None)
        toolkit_obj = getattr(toolkit_contracts, name, None)
        if engine_obj is not shared_obj:
            mismatches.append(f"engine.contracts.{name}")
        if toolkit_obj is not shared_obj:
            mismatches.append(f"toolkit.contracts.{name}")

    assert not mismatches, f"core contract re-export drift detected: {mismatches}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: consumer files import core types via contracts, not directly
# ─────────────────────────────────────────────────────────────────────────────


def test_executor_imports_stream_event_type_from_contracts() -> None:
    """executor.py must import StreamEventType from .contracts, not contracts.core."""
    executor = BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "engine" / "executor.py"
    names = _imported_names(executor, level=1, module="contracts")
    assert "StreamEventType" in names, (
        "executor.py must import StreamEventType from .contracts to keep "
        "contracts.core -> contracts -> executor parity explicit."
    )


def test_model_catalog_imports_modelspec_from_contracts() -> None:
    """model_catalog.py must import ModelSpec from .contracts, not contracts.core."""
    catalog = BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "engine" / "model_catalog.py"
    names = _imported_names(catalog, level=1, module="contracts")
    assert "ModelSpec" in names, (
        "model_catalog.py must import ModelSpec from .contracts to keep "
        "contracts.core -> contracts -> model_catalog parity explicit."
    )


def test_engine_production_code_does_not_import_core_contracts_directly() -> None:
    """Engine runtime code must consume core contracts through engine.contracts."""

    forbidden = "polaris.kernelone.llm.contracts.core"
    findings: list[str] = []
    for path in LLM_ENGINE_ROOT.rglob("*.py"):
        if "tests" in path.parts or path.name == "contracts.py":
            continue
        text = path.read_text(encoding="utf-8")
        if forbidden in text or "from ..contracts.core import" in text:
            findings.append(str(path.relative_to(BACKEND_ROOT)))

    assert not findings, f"engine code bypasses engine.contracts re-export boundary: {findings}"


def test_roles_kernel_production_code_does_not_import_core_contracts_directly() -> None:
    """roles.kernel must consume LLM core types through engine.contracts."""

    forbidden = "polaris.kernelone.llm.contracts.core"
    findings = [
        str(path.relative_to(BACKEND_ROOT))
        for path in _production_python_files(ROLES_KERNEL_ROOT)
        if _imports_forbidden_module(path, forbidden)
    ]

    assert not findings, f"roles.kernel bypasses engine.contracts re-export boundary: {findings}"


def test_only_contract_modules_import_core_contracts_directly() -> None:
    """Production code must not bypass the LLM contract re-export boundary."""

    forbidden = "polaris.kernelone.llm.contracts.core"
    findings = [
        str(path.relative_to(BACKEND_ROOT))
        for path in _production_python_files(POLARIS_ROOT)
        if path != CORE_CONTRACT_OWNER
        and path not in ALLOWED_CORE_CONTRACT_IMPORTERS
        and _imports_forbidden_module(path, forbidden)
    ]

    assert not findings, f"production code imports contracts.core directly instead of a contract surface: {findings}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: StreamEventType value-level parity — catches new enum members
#        that are added to contracts.core but not handled by consumers
# ─────────────────────────────────────────────────────────────────────────────


def test_stream_event_type_values_match_across_layers() -> None:
    """All enum values defined in contracts.core StreamEventType must exist
    in both engine.contracts and toolkit.contracts.

    If a new value (e.g. AUDIO_CHUNK) is added to contracts.core but not
    imported by engine or toolkit contracts, consumer code that matches on
    StreamEventType may silently ignore it. This test forces an explicit
    decision at the re-export boundary.
    """
    shared_values = {e.value for e in core_contracts.StreamEventType}
    engine_values = {e.value for e in engine_contracts.StreamEventType}
    toolkit_values = {e.value for e in toolkit_contracts.StreamEventType}

    missing_in_engine = shared_values - engine_values
    missing_in_toolkit = shared_values - toolkit_values

    assert not missing_in_engine, (
        f"engine.contracts is missing StreamEventType values: {sorted(missing_in_engine)}. "
        "Import the new value in engine/contracts.py to prevent consumer drift."
    )
    assert not missing_in_toolkit, (
        f"toolkit.contracts is missing StreamEventType values: {sorted(missing_in_toolkit)}. "
        "Import the new value in toolkit/contracts.py to prevent consumer drift."
    )


def test_stream_event_type_member_count() -> None:
    """Sanity-check: StreamEventType must have at least the current 7 values.

    If a value is accidentally removed from contracts.core, this test fails.
    """
    count = len(list(core_contracts.StreamEventType))
    assert count >= 7, (
        f"StreamEventType has {count} values (expected ≥7). "
        "A value may have been accidentally removed from contracts/core.py."
    )
