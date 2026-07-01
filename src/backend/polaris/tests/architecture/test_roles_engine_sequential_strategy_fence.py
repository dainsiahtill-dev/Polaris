"""Architecture fence for the roles.engine sequential strategy boundary."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
ROLES_ENGINE_ROOT = BACKEND_ROOT / "polaris" / "cells" / "roles" / "engine"
SEQUENTIAL_ADAPTER = ROLES_ENGINE_ROOT / "internal" / "sequential_adapter.py"
ROLES_ENGINE_CELL = ROLES_ENGINE_ROOT / "cell.yaml"
ROLES_ENGINE_DESCRIPTOR = ROLES_ENGINE_ROOT / "generated" / "descriptor.pack.json"
RETIRED_MARKERS = (
    "compatibility shim",
    "SequentialEngine compatibility",
    "sequential compatibility",
)


def _assert_no_retired_markers(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    lowered = source.lower()
    offenders = [marker for marker in RETIRED_MARKERS if marker.lower() in lowered]
    relative_path = path.relative_to(BACKEND_ROOT)
    assert offenders == [], f"{relative_path} still describes sequential strategy as {offenders}"


def test_sequential_strategy_is_not_documented_as_compatibility_shim() -> None:
    """The active sequential strategy must not be documented as a legacy shim."""
    for path in (SEQUENTIAL_ADAPTER, ROLES_ENGINE_CELL, ROLES_ENGINE_DESCRIPTOR):
        _assert_no_retired_markers(path)


def test_sequential_strategy_source_remains_owned_by_roles_engine() -> None:
    """The sequential strategy remains a roles.engine implementation."""
    source = SEQUENTIAL_ADAPTER.read_text(encoding="utf-8")
    assert "class SequentialEngineAdapter(BaseEngine)" in source
    assert "EngineStrategy.SEQUENTIAL" in source
