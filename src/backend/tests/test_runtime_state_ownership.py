"""
Tests: ACGA 2.0 single-state-owner invariant for runtime.state_owner and
runtime.artifact_store cells.

Principle: One source-of-truth state namespace must have exactly one writing
Cell declared in state_owners. runtime/contracts/*, runtime/state/*,
runtime/runs/* belong exclusively to runtime.state_owner.
runtime/artifacts/* belongs exclusively to runtime.artifact_store.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BACKEND_ROOT = Path(__file__).parent.parent
CELLS_ROOT = BACKEND_ROOT / "polaris" / "cells"
CATALOG_PATH = BACKEND_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"


def _load_cell_yaml(cell_id: str) -> dict:
    """Load a cell.yaml by dot-separated cell id (e.g. 'runtime.state_owner')."""
    parts = cell_id.split(".")
    path = CELLS_ROOT.joinpath(*parts) / "cell.yaml"
    assert path.exists(), f"cell.yaml not found at {path}"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), f"cell.yaml at {path} did not parse as a mapping"
    return data


def _catalog_entry(cell_id: str) -> dict:
    """Return the catalog entry for a given cell id."""
    assert CATALOG_PATH.exists(), f"cells.yaml not found at {CATALOG_PATH}"
    with CATALOG_PATH.open(encoding="utf-8") as f:
        catalog = yaml.safe_load(f)
    cells = catalog if isinstance(catalog, list) else catalog.get("cells", [])
    for entry in cells:
        if isinstance(entry, dict) and entry.get("id") == cell_id:
            return entry
    pytest.fail(f"Cell id '{cell_id}' not found in catalog at {CATALOG_PATH}")


def _state_owners(cell_data: dict) -> list[str]:
    return list(cell_data.get("state_owners") or [])


def _effects_write(cell_data: dict) -> list[str]:
    effects = list(cell_data.get("effects_allowed") or [])
    return [e for e in effects if e.startswith("fs.write:")]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def state_owner_cell() -> dict:
    return _load_cell_yaml("runtime.state_owner")


@pytest.fixture(scope="module")
def artifact_store_cell() -> dict:
    return _load_cell_yaml("runtime.artifact_store")


@pytest.fixture(scope="module")
def state_owner_catalog() -> dict:
    return _catalog_entry("runtime.state_owner")


@pytest.fixture(scope="module")
def artifact_store_catalog() -> dict:
    return _catalog_entry("runtime.artifact_store")


# ---------------------------------------------------------------------------
# Tests: runtime.state_owner
# ---------------------------------------------------------------------------

class TestStateOwnerCell:
    """runtime.state_owner must claim and only claim its three namespaces."""

    def test_cell_yaml_id(self, state_owner_cell):
        assert state_owner_cell["id"] == "runtime.state_owner"

    def test_owns_contracts_namespace(self, state_owner_cell):
        owners = _state_owners(state_owner_cell)
        assert "runtime/contracts/*" in owners, (
            "runtime.state_owner must own runtime/contracts/*"
        )

    def test_owns_state_namespace(self, state_owner_cell):
        owners = _state_owners(state_owner_cell)
        assert "runtime/state/*" in owners, (
            "runtime.state_owner must own runtime/state/*"
        )

    def test_owns_runs_namespace(self, state_owner_cell):
        owners = _state_owners(state_owner_cell)
        assert "runtime/runs/*" in owners, (
            "runtime.state_owner must own runtime/runs/*"
        )

    def test_does_not_own_artifacts_namespace(self, state_owner_cell):
        owners = _state_owners(state_owner_cell)
        for o in owners:
            assert not o.startswith("runtime/artifacts"), (
                f"runtime.state_owner must NOT claim runtime/artifacts — found: {o}"
            )

    def test_effects_write_only_owned_namespaces(self, state_owner_cell):
        """Every fs.write effect must target a namespace this cell owns."""
        owned = _state_owners(state_owner_cell)
        # Extract the base prefixes from state_owners (strip glob suffix)
        owned_prefixes = [re.sub(r"/\*+$", "/", s) for s in owned]
        for effect in _effects_write(state_owner_cell):
            target = effect.removeprefix("fs.write:")
            assert any(target.startswith(prefix) or target == prefix.rstrip("/")
                       for prefix in owned_prefixes), (
                f"runtime.state_owner has write effect on unowned path: {effect}"
            )


# ---------------------------------------------------------------------------
# Tests: runtime.artifact_store
# ---------------------------------------------------------------------------

class TestArtifactStoreCell:
    """runtime.artifact_store must NOT claim namespaces owned by runtime.state_owner."""

    STATE_OWNER_NAMESPACES = {
        "runtime/contracts",
        "runtime/state",
        "runtime/runs",
    }

    def test_cell_yaml_id(self, artifact_store_cell):
        assert artifact_store_cell["id"] == "runtime.artifact_store"

    def test_owns_artifacts_namespace(self, artifact_store_cell):
        owners = _state_owners(artifact_store_cell)
        assert "runtime/artifacts/*" in owners, (
            "runtime.artifact_store must own runtime/artifacts/*"
        )

    def test_no_overlap_with_state_owner_namespaces(self, artifact_store_cell):
        """artifact_store must not declare state_owners in namespaces that belong
        to runtime.state_owner."""
        owners = _state_owners(artifact_store_cell)
        for owned in owners:
            namespace = re.sub(r"/\*+$", "", owned).split("/")[0:2]
            namespace_str = "/".join(namespace)
            assert namespace_str not in self.STATE_OWNER_NAMESPACES, (
                f"runtime.artifact_store illegally claims state in "
                f"'{namespace_str}' — that namespace belongs to runtime.state_owner. "
                f"Offending entry: {owned}"
            )

    def test_no_write_effects_on_state_owner_namespaces(self, artifact_store_cell):
        """artifact_store must not have fs.write effects on state_owner namespaces."""
        for effect in _effects_write(artifact_store_cell):
            target = effect.removeprefix("fs.write:")
            for forbidden in self.STATE_OWNER_NAMESPACES:
                assert not target.startswith(forbidden), (
                    f"runtime.artifact_store has write effect on state_owner namespace: "
                    f"{effect}  (forbidden prefix: {forbidden})"
                )

    def test_declares_dependency_on_state_owner(self, artifact_store_cell):
        """artifact_store reads from state_owner namespaces, so it must declare
        runtime.state_owner in depends_on to make the read-dependency explicit."""
        depends = list(artifact_store_cell.get("depends_on") or [])
        assert "runtime.state_owner" in depends, (
            "runtime.artifact_store reads from runtime.state_owner namespaces and must "
            "declare it in depends_on"
        )


# ---------------------------------------------------------------------------
# Tests: Cross-cell uniqueness invariant
# ---------------------------------------------------------------------------

class TestStateOwnershipUniqueness:
    """No two cells may declare ownership of the same state namespace."""

    def test_no_duplicate_state_owners_across_all_cells(self):
        """Parse every cell.yaml under polaris/cells and verify that each
        state_owner glob pattern is claimed by at most one cell.

        Pre-existing violations in other cells (outside this task's scope) are
        captured in KNOWN_PREEXISTING_VIOLATIONS. The test fails only on NEW
        violations not in that set.  Any entry in that set that disappears
        (i.e. the other cell is fixed) is also flagged so the set stays current.
        """
        # Pre-existing violations discovered by this scan but outside the scope
        # of this task (runtime.state_owner / runtime.artifact_store).
        # These must be addressed in a dedicated task for each owning team.
        KNOWN_PREEXISTING_VIOLATIONS: set[str] = set()

        seen: dict[str, str] = {}  # pattern -> cell_id
        all_duplicates: list[str] = []

        for cell_yaml_path in sorted(CELLS_ROOT.rglob("cell.yaml")):
            with cell_yaml_path.open(encoding="utf-8") as f:
                try:
                    data = yaml.safe_load(f)
                except yaml.YAMLError:
                    continue
            if not isinstance(data, dict):
                continue
            cell_id = data.get("id", str(cell_yaml_path))
            for pattern in (data.get("state_owners") or []):
                pattern_str = str(pattern).strip()
                if pattern_str in seen:
                    all_duplicates.append(
                        f"'{pattern_str}' claimed by both '{seen[pattern_str]}' and '{cell_id}'"
                    )
                else:
                    seen[pattern_str] = cell_id

        new_violations = [d for d in all_duplicates if d not in KNOWN_PREEXISTING_VIOLATIONS]
        stale_entries = [k for k in KNOWN_PREEXISTING_VIOLATIONS if k not in all_duplicates]

        # Surface pre-existing violations as warnings in the output for visibility
        if all_duplicates:
            import warnings
            preexisting = [d for d in all_duplicates if d in KNOWN_PREEXISTING_VIOLATIONS]
            if preexisting:
                warnings.warn(
                    "Pre-existing ACGA 2.0 state_owner violations (not in scope here):\n"
                    + "\n".join(f"  - {d}" for d in preexisting),
                    UserWarning,
                    stacklevel=2,
                )

        assert not stale_entries, (
            "KNOWN_PREEXISTING_VIOLATIONS has entries that no longer appear in the scan "
            "(the violation was fixed). Remove these from the set:\n"
            + "\n".join(f"  - {e}" for e in stale_entries)
        )

        assert not new_violations, (
            "NEW ACGA 2.0 single-state-owner violation detected — fix before merging:\n"
            + "\n".join(f"  - {d}" for d in new_violations)
        )

    def test_catalog_runtime_artifact_store_owns_only_artifacts(
        self, artifact_store_catalog
    ):
        """Verify the catalog entry for runtime.artifact_store is consistent."""
        owners = list(artifact_store_catalog.get("state_owners") or [])
        assert owners == ["runtime/artifacts/*"], (
            f"Catalog runtime.artifact_store state_owners must be exactly "
            f"['runtime/artifacts/*'], got: {owners}"
        )

    def test_catalog_runtime_state_owner_owns_contracts_state_runs(
        self, state_owner_catalog
    ):
        """Verify the catalog entry for runtime.state_owner is consistent."""
        owners = set(state_owner_catalog.get("state_owners") or [])
        expected = {"runtime/contracts/*", "runtime/state/*", "runtime/runs/*"}
        assert owners == expected, (
            f"Catalog runtime.state_owner state_owners must be {expected}, got: {owners}"
        )

    def test_cell_yaml_and_catalog_consistent_for_artifact_store(
        self, artifact_store_cell, artifact_store_catalog
    ):
        """cell.yaml and catalog must agree on state_owners for artifact_store."""
        cell_owners = set(_state_owners(artifact_store_cell))
        catalog_owners = set(artifact_store_catalog.get("state_owners") or [])
        assert cell_owners == catalog_owners, (
            f"runtime.artifact_store cell.yaml state_owners {cell_owners} "
            f"do not match catalog {catalog_owners}"
        )

    def test_cell_yaml_and_catalog_consistent_for_state_owner(
        self, state_owner_cell, state_owner_catalog
    ):
        """cell.yaml and catalog must agree on state_owners for runtime.state_owner."""
        cell_owners = set(_state_owners(state_owner_cell))
        catalog_owners = set(state_owner_catalog.get("state_owners") or [])
        assert cell_owners == catalog_owners, (
            f"runtime.state_owner cell.yaml state_owners {cell_owners} "
            f"do not match catalog {catalog_owners}"
        )
