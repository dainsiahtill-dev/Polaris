"""Governance checks for roles runtime/kernel/adapters manifests.

These tests intentionally validate the local ``cell.yaml`` truth only.
Catalog reconciliation is handled separately.
"""

from __future__ import annotations

from pathlib import Path

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CELLS_ROOT = BACKEND_ROOT / "polaris" / "cells"


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


def test_roles_runtime_depends_on_matches_imports() -> None:
    deps = _deps("roles.runtime")
    expected = {
        "architect.design",
        "chief_engineer.blueprint",
        "director.execution",
        "finops.budget_guard",
        "llm.control_plane",
        "orchestration.pm_planning",
        "qa.audit_verdict",
        "roles.adapters",
        "roles.engine",
        "roles.kernel",
        "roles.profile",
        "roles.session",
        "runtime.state_owner",
    }
    assert deps == expected
    assert "kernelone.events" not in deps


def test_roles_kernel_depends_on_matches_imports() -> None:
    deps = _deps("roles.kernel")
    expected = {
        "director.execution",
        "llm.dialogue",
        "roles.adapters",
        "roles.profile",
        "roles.session",
        "runtime.task_runtime",
    }
    assert deps == expected
    assert "llm.provider_runtime" not in deps
    assert "policy.workspace_guard" not in deps
    assert "audit.evidence" not in deps
    assert "finops.budget_guard" not in deps


def test_roles_adapters_depends_on_matches_imports() -> None:
    deps = _deps("roles.adapters")
    expected = {
        "director.execution",
        "factory.pipeline",
        "llm.dialogue",
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
