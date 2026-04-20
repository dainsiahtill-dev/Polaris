"""Integration tests for the entire roles.engine cell.

Verifies:
1. No import-time circular-dependency crash.
2. All public contracts are importable.
3. The cell top-level __init__ exposes the correct public API.
"""

from __future__ import annotations


def test_cell_top_level_imports_without_error() -> None:
    """The cell top-level __init__ must import without raising."""
    from polaris.cells.roles.engine import public as cell

    assert cell is not None


def test_cell_entry_exposes_public_contracts() -> None:
    """Public contracts must be reachable from the cell entry."""
    from polaris.cells.roles.engine import (
        ClassifyTaskQueryV1,
        EngineExecutionResultV1,
        EngineRegistrySnapshotQueryV1,
        EngineRegistrySnapshotResultV1,
        EngineSelectedEventV1,
        EngineSelectionResultV1,
        IRoleEngineService,
        RegisterEngineCommandV1,
        RolesEngineError,
        SelectEngineCommandV1,
    )

    # All must be non-None classes
    assert ClassifyTaskQueryV1 is not None
    assert SelectEngineCommandV1 is not None
    assert RegisterEngineCommandV1 is not None
    assert EngineSelectedEventV1 is not None
    assert EngineSelectionResultV1 is not None
    assert EngineExecutionResultV1 is not None
    assert EngineRegistrySnapshotQueryV1 is not None
    assert EngineRegistrySnapshotResultV1 is not None
    assert RolesEngineError is not None
    assert IRoleEngineService is not None


def test_internal_modules_import_without_error() -> None:
    """Internal sub-modules must import without raising."""
    from polaris.cells.roles.engine import internal as intern

    assert intern is not None
    assert hasattr(intern, "base")
    assert hasattr(intern, "classifier")
    assert hasattr(intern, "registry")
    assert hasattr(intern, "react")
    assert hasattr(intern, "plan_solve")
    assert hasattr(intern, "tot")
    assert hasattr(intern, "sequential_adapter")
    assert hasattr(intern, "hybrid")


def test_engine_registry_singleton_accessible() -> None:
    """EngineRegistry must be reachable from internal."""
    from polaris.cells.roles.engine.internal.registry import get_engine_registry

    registry = get_engine_registry()
    assert registry is not None
    # get_engine_registry returns the same object
    assert get_engine_registry() is registry


def test_task_classifier_singleton_accessible() -> None:
    """TaskClassifier must be reachable from internal."""
    from polaris.cells.roles.engine.internal.classifier import get_task_classifier

    classifier = get_task_classifier()
    assert classifier is not None


def test_hybrid_engine_factory_accessible() -> None:
    """get_hybrid_engine must be importable."""
    from polaris.cells.roles.engine.internal.hybrid import get_hybrid_engine

    engine = get_hybrid_engine()
    assert engine is not None
