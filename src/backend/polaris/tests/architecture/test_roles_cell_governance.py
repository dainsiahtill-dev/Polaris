"""Governance checks for roles runtime/kernel/adapters manifests.

These tests intentionally validate the local ``cell.yaml`` truth only.
Catalog reconciliation is handled separately.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from docs.governance.ci.scripts.run_catalog_governance_gate import (
    _RULE_DECLARED_CELL_DEPENDENCIES_MATCH_IMPORTS,
    CatalogCell,
    GovernanceIssue,
    _build_cell_index,
    _check_declared_cell_dependencies,
)

BACKEND_ROOT = Path(__file__).resolve().parents[3]
CELLS_ROOT = BACKEND_ROOT / "polaris" / "cells"
CATALOG_PATH = BACKEND_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"

_ROLES_RUNTIME_DEPENDENCY_CASES = (
    (
        "events.fact_stream",
        "polaris/cells/roles/runtime/public/cli_runner.py",
    ),
    (
        "runtime.task_runtime",
        "polaris/cells/roles/runtime/internal/worker_pool.py",
    ),
)


def _load_cell_yaml(cell_id: str) -> dict[str, object]:
    parts = cell_id.split(".")
    path = CELLS_ROOT.joinpath(*parts) / "cell.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path} did not parse as a mapping"
    return data


def _deps(cell_id: str) -> set[str]:
    cell = _load_cell_yaml(cell_id)
    deps = cell.get("depends_on")
    assert isinstance(deps, list), f"{cell_id} depends_on must be a list"
    return {str(item).strip() for item in deps if str(item).strip()}


def _catalog_cells() -> tuple[CatalogCell, ...]:
    payload = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), f"{CATALOG_PATH} did not parse as a mapping"
    return tuple(_build_cell_index(payload))


def _without_roles_runtime_dependency(dependency: str) -> tuple[CatalogCell, ...]:
    cells = _catalog_cells()
    return tuple(
        replace(
            cell,
            depends_on=tuple(item for item in cell.depends_on if item != dependency),
        )
        if cell.cell_id == "roles.runtime"
        else cell
        for cell in cells
    )


def test_roles_runtime_depends_on_matches_imports() -> None:
    deps = _deps("roles.runtime")
    expected = {
        "archive.run_archive",
        "audit.diagnosis",
        "chief_engineer.blueprint",
        "code_intelligence.engine",
        "cognitive.knowledge_distiller",
        "context.engine",
        "director.execution",
        "events.fact_stream",
        "factory.cognitive_runtime",
        "factory.verification_guard",
        "finops.budget_guard",
        "llm.control_plane",
        "policy.permission",
        "policy.workspace_guard",
        "qa.audit_verdict",
        "roles.engine",
        "roles.kernel",
        "roles.profile",
        "roles.session",
        "runtime.execution_broker",
        "runtime.projection",
        "runtime.state_owner",
        "runtime.task_market",
        "runtime.task_runtime",
    }
    assert deps == expected
    assert "kernelone.events" not in deps


@pytest.mark.parametrize(
    ("dependency", "source_path"),
    _ROLES_RUNTIME_DEPENDENCY_CASES,
)
def test_roles_runtime_missing_declared_import_dependency_is_caught_by_catalog_gate(
    dependency: str,
    source_path: str,
) -> None:
    """Removing either real cross-cell edge produces the precise gate violation."""

    issues: list[GovernanceIssue] = []
    _check_declared_cell_dependencies(
        repo_root=BACKEND_ROOT,
        cells=_without_roles_runtime_dependency(dependency),
        issues=issues,
    )

    assert issues == [
        GovernanceIssue(
            rule_id=_RULE_DECLARED_CELL_DEPENDENCIES_MATCH_IMPORTS,
            severity="high",
            message=f"roles.runtime imports {dependency} but does not declare it in depends_on",
            path=source_path,
        )
    ]


def test_roles_kernel_depends_on_matches_imports() -> None:
    deps = _deps("roles.kernel")
    expected = {
        "audit.evidence",
        "context.engine",
        "control_plane.run_ledger",
        "director.execution",
        "director.runtime",
        "events.fact_stream",
        "factory.cognitive_runtime",
        "llm.control_plane",
        "policy.permission",
        "roles.profile",
        "roles.scout",
        "roles.session",
        "runtime.execution_broker",
        "runtime.task_runtime",
        "storage.layout",
    }
    assert deps == expected
    assert "llm.provider_runtime" not in deps
    assert "policy.workspace_guard" not in deps
    assert "finops.budget_guard" not in deps
    assert "kernelone.core" not in deps


def test_roles_adapters_depends_on_matches_imports() -> None:
    deps = _deps("roles.adapters")
    expected = {
        "chief_engineer.blueprint",
        "director.execution",
        "director.runtime",
        "director.tasking",
        "events.fact_stream",
        "factory.cognitive_runtime",
        "factory.pipeline",
        "orchestration.pm_planning",
        "orchestration.workflow_runtime",
        "roles.engine",
        "roles.kernel",
        "roles.profile",
        "roles.runtime",
        "roles.session",
        "runtime.task_runtime",
    }
    assert deps == expected
    assert "policy.workspace_guard" not in deps
