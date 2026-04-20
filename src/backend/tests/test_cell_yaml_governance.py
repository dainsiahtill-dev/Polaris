"""tests/test_cell_yaml_governance.py

Graph governance invariant tests for Cell YAML declarations.

Scope
-----
This test file covers two categories of checks:

1. **Targeted P0-10/P0-11 regression tests** (always fail-closed):
   These test the specific conflicts that were fixed in this task:
   - llm.control_plane must not claim kernelone/infrastructure paths.
   - llm.control_plane must not own test-index state paths.
   - llm.evaluation must be the sole owner of evaluation index paths.
   - No duplicate state_owners across all cells (catalog-wide).
   - catalog <-> cell.yaml consistency for llm.control_plane and llm.evaluation.

2. **Catalog-wide structural invariants** (recorded-violation / allowlisted):
   The full catalog has pre-existing violations in other cells that are
   out of scope for this task. These are captured in an allowlist so that
   new violations are caught while old ones are tracked without blocking.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

BACKEND_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = BACKEND_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"
CELLS_ROOT = BACKEND_ROOT / "polaris" / "cells"

# Cells that are intentionally designed to span kernelone/infrastructure
# by ACGA architectural decision (e.g., KernelOne-tier cells).
_KERNELONE_OWNER_ALLOWLIST: frozenset[str] = frozenset(
    {
        "audit.evidence",
        # Pre-existing catalog declarations below are acknowledged technical debt
        # to be resolved in future tasks; they must NOT grow.
        "chief_engineer.blueprint",
        "director.execution",
        "policy.permission",
        "finops.budget_guard",
        "events.fact_stream",
        "orchestration.workflow_runtime",
        "storage.layout",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_catalog() -> dict[str, Any]:
    assert CATALOG_PATH.is_file(), f"cells.yaml not found: {CATALOG_PATH}"
    with CATALOG_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _catalog_cells(data: dict[str, Any]) -> list[dict[str, Any]]:
    return data.get("cells", [])


def _load_cell_yaml(cell_id: str) -> dict[str, Any] | None:
    """Load the individual cell.yaml for *cell_id* from polaris/cells/.

    Convention: ``llm.control_plane`` maps to
    ``polaris/cells/llm/control_plane/cell.yaml``.
    Returns None if the file does not exist (not all cells have been migrated yet).
    """
    parts = cell_id.split(".")
    candidate = CELLS_ROOT.joinpath(*parts) / "cell.yaml"
    if not candidate.is_file():
        return None
    with candidate.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _is_infra_path(path: str) -> bool:
    """Return True if *path* targets kernelone/** or infrastructure/**."""
    return bool(
        re.match(r"polaris/kernelone/", path)
        or re.match(r"polaris/infrastructure/", path)
    )


def _write_effects(effects: list[str]) -> list[str]:
    """Return only fs.write effect targets from an effects_allowed list."""
    results: list[str] = []
    for e in effects or []:
        m = re.match(r"fs\.write:(.+)", e)
        if m:
            results.append(m.group(1))
    return results


def _path_prefix_matches(effect_target: str, state_owners: list[str]) -> bool:
    """Check whether *effect_target* is covered by at least one state_owner.

    The matching rule mirrors the ACGA convention:
    - Exact match, OR
    - state_owner ends with ``/*`` and the effect_target starts with the
      directory prefix.
    """
    for owner in state_owners or []:
        if effect_target == owner:
            return True
        if owner.endswith("/*"):
            prefix = owner[:-2]  # strip trailing /*
            if effect_target.startswith(prefix):
                return True
    return False


# ---------------------------------------------------------------------------
# P0-10/P0-11 Targeted Regression Tests (always fail-closed)
# ---------------------------------------------------------------------------


class TestStateOwnerUniqueness:
    """No state path may be claimed by more than one Cell."""

    def test_no_duplicate_state_owners_in_catalog(self) -> None:
        """Catalog-wide: every state_owner path must appear in exactly one Cell."""
        data = _load_catalog()
        cells = _catalog_cells(data)

        seen: dict[str, str] = {}  # path -> first cell id that claimed it
        duplicates: list[str] = []

        for cell in cells:
            cell_id: str = cell.get("id", "<unknown>")
            for path in cell.get("state_owners") or []:
                if path in seen:
                    duplicates.append(
                        f"'{path}' claimed by both '{seen[path]}' and '{cell_id}'"
                    )
                else:
                    seen[path] = cell_id

        assert not duplicates, (
            "state_owner conflict(s) detected in cells.yaml:\n"
            + "\n".join(f"  - {d}" for d in duplicates)
        )

    def test_llm_control_plane_does_not_own_test_index(self) -> None:
        """llm.control_plane must NOT own test-index state paths (P0-10 regression)."""
        data = _load_catalog()
        cells = _catalog_cells(data)
        cp = next((c for c in cells if c.get("id") == "llm.control_plane"), None)
        assert cp is not None, "llm.control_plane not found in catalog"

        forbidden_patterns = [
            "llm_test_index",
            "runtime/llm_tests",
        ]
        for path in cp.get("state_owners") or []:
            for pattern in forbidden_patterns:
                assert pattern not in path, (
                    f"llm.control_plane must not own state path '{path}'; "
                    f"pattern '{pattern}' is reserved for llm.evaluation (P0-10)"
                )

    def test_llm_evaluation_owns_test_index(self) -> None:
        """llm.evaluation must be the sole owner of the evaluation index (P0-11)."""
        data = _load_catalog()
        cells = _catalog_cells(data)
        ev = next((c for c in cells if c.get("id") == "llm.evaluation"), None)
        assert ev is not None, "llm.evaluation not found in catalog"

        owners = ev.get("state_owners") or []
        assert any("llm_test_index" in p for p in owners), (
            f"llm.evaluation must own an llm_test_index path; got state_owners={owners}"
        )


class TestLlmControlPlaneOwnedPaths:
    """llm.control_plane-specific owned_paths boundary (P0-10 regression)."""

    def test_llm_control_plane_owned_paths_no_kernelone(self) -> None:
        """llm.control_plane must not own polaris/kernelone/** or infrastructure/**."""
        data = _load_catalog()
        cells = _catalog_cells(data)
        cp = next((c for c in cells if c.get("id") == "llm.control_plane"), None)
        assert cp is not None, "llm.control_plane not found in catalog"

        bad = [p for p in (cp.get("owned_paths") or []) if _is_infra_path(p)]
        assert not bad, (
            "llm.control_plane must not own kernelone/infrastructure paths "
            "(ACGA 2.0 §2.3, P0-10); found: "
            + ", ".join(bad)
        )


class TestCellYamlCatalogConsistency:
    """Individual cell.yaml files must be consistent with the catalog."""

    _KEY_FIELDS = ("state_owners", "effects_allowed", "owned_paths")

    @pytest.mark.parametrize("cell_id", ["llm.control_plane", "llm.evaluation"])
    def test_cell_yaml_matches_catalog(self, cell_id: str) -> None:
        """catalog and cell.yaml must agree on state_owners/effects_allowed/owned_paths."""
        data = _load_catalog()
        cells = _catalog_cells(data)
        catalog_entry = next(
            (c for c in cells if c.get("id") == cell_id), None
        )
        assert catalog_entry is not None, f"{cell_id} not found in catalog"

        cell_yaml = _load_cell_yaml(cell_id)
        if cell_yaml is None:
            pytest.skip(
                f"cell.yaml not present for {cell_id} — skipping consistency check"
            )

        for field in self._KEY_FIELDS:
            catalog_val = sorted(catalog_entry.get(field) or [])
            cell_val = sorted(cell_yaml.get(field) or [])
            assert catalog_val == cell_val, (
                f"{cell_id}: field '{field}' differs between catalog and cell.yaml.\n"
                f"  catalog  : {catalog_val}\n"
                f"  cell.yaml: {cell_val}"
            )


# ---------------------------------------------------------------------------
# Catalog-wide structural invariants (allowlist-gated — must not grow)
# ---------------------------------------------------------------------------


class TestCatalogWideInvariantsAllowlisted:
    """Catalog-wide invariants with a recorded-violation allowlist.

    These tests detect NEW violations only. Pre-existing violations in other
    cells are frozen in the allowlists below and must be resolved in future
    tasks; they must NOT grow.
    """

    # Cells already in violation of the "no kernelone/infra owned_paths" rule.
    # This set must not grow. Remove entries as violations are fixed.
    _EXISTING_INFRA_PATH_VIOLATORS: frozenset[str] = frozenset(
        {
            "chief_engineer.blueprint",
            "director.execution",
            "policy.permission",
            "finops.budget_guard",
            "events.fact_stream",
            "orchestration.workflow_runtime",
            "storage.layout",
        }
    )

    def test_no_new_cell_owns_kernelone_or_infra_paths(self) -> None:
        """No cell outside the known violators may newly claim kernelone/infra paths."""
        data = _load_catalog()
        cells = _catalog_cells(data)

        new_violations: list[str] = []

        for cell in cells:
            cell_id: str = cell.get("id", "<unknown>")
            # audit.evidence is allowlisted by design; known violators are frozen
            if (
                cell_id == "audit.evidence"
                or cell_id in self._EXISTING_INFRA_PATH_VIOLATORS
            ):
                continue
            for path in cell.get("owned_paths") or []:
                if _is_infra_path(path):
                    new_violations.append(
                        f"Cell '{cell_id}' owns infra/kernelone path: '{path}'"
                    )

        assert not new_violations, (
            "NEW cells are claiming kernelone/infrastructure owned_paths "
            "(ACGA 2.0 §2.3). Fix these before the allowlist can shrink:\n"
            + "\n".join(f"  - {v}" for v in new_violations)
        )
