"""G-4 re-export parity gate for KernelOne LLM contracts.

This module enforces that:
1. All shared types live in shared_contracts.py (single source of truth).
2. engine/contracts.py and toolkit/contracts.py re-export them without modification.
3. Consumer files (executor.py, model_catalog.py) import from contracts,
   never directly from shared_contracts.
4. StreamEventType values are identical across all re-export layers.

Audit target: polaris/kernelone/llm/
"""

from __future__ import annotations

import ast
from pathlib import Path

from polaris.kernelone.llm import shared_contracts
from polaris.kernelone.llm.engine import contracts as engine_contracts
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.toolkit import contracts as toolkit_contracts

BACKEND_ROOT = Path(__file__).resolve().parents[2]


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
    assert engine_contracts.StreamEventType is shared_contracts.StreamEventType


def test_toolkit_contracts_stream_event_type_identity() -> None:
    """toolkit.contracts must re-export the same StreamEventType."""
    assert toolkit_contracts.StreamEventType is shared_contracts.StreamEventType


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: engine/contracts.__all__ covers all shared_contracts types
# ─────────────────────────────────────────────────────────────────────────────

def test_engine_contracts_all_includes_shared_types() -> None:
    """engine/contracts.py __all__ must list every type from shared_contracts.__all__.

    Without __all__ the re-export imports are invisible to ruff --fix,
    which will delete them as "unused". This test detects that regression.
    """
    shared = set(shared_contracts.__all__)
    engine_all = set(getattr(engine_contracts, "__all__", ()))
    missing = shared - engine_all
    assert not missing, (
        f"engine/contracts.py __all__ is missing re-exported shared types: {sorted(missing)}. "
        "Add them to __all__ to prevent ruff --fix from removing the imports."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: consumer files import shared types via contracts, not directly
# ─────────────────────────────────────────────────────────────────────────────

def test_executor_imports_stream_event_type_from_contracts() -> None:
    """executor.py must import StreamEventType from .contracts, not shared_contracts."""
    executor = BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "engine" / "executor.py"
    names = _imported_names(executor, level=1, module="contracts")
    assert "StreamEventType" in names, (
        "executor.py must import StreamEventType from .contracts to keep "
        "shared_contracts -> contracts -> executor parity explicit."
    )


def test_model_catalog_imports_modelspec_from_contracts() -> None:
    """model_catalog.py must import ModelSpec from .contracts, not shared_contracts."""
    catalog = BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "engine" / "model_catalog.py"
    names = _imported_names(catalog, level=1, module="contracts")
    assert "ModelSpec" in names, (
        "model_catalog.py must import ModelSpec from .contracts to keep "
        "shared_contracts -> contracts -> model_catalog parity explicit."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: StreamEventType value-level parity — catches new enum members
#        that are added to shared_contracts but not handled by consumers
# ─────────────────────────────────────────────────────────────────────────────

def test_stream_event_type_values_match_across_layers() -> None:
    """All enum values defined in shared_contracts.StreamEventType must exist
    in both engine.contracts and toolkit.contracts.

    If a new value (e.g. AUDIO_CHUNK) is added to shared_contracts but not
    imported by engine or toolkit contracts, consumer code that matches on
    StreamEventType may silently ignore it. This test forces an explicit
    decision at the re-export boundary.
    """
    shared_values = {e.value for e in shared_contracts.StreamEventType}
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

    If a value is accidentally removed from shared_contracts, this test fails.
    """
    count = len(list(shared_contracts.StreamEventType))
    assert count >= 7, (
        f"StreamEventType has {count} values (expected ≥7). "
        "A value may have been accidentally removed from shared_contracts.py."
    )
