"""tests/test_roles_state_ownership.py

Verifies that roles.runtime and roles.session Cell manifests have
no overlapping state_owners and that each Cell owns only the paths
appropriate to its semantic boundary.

Boundary contract:
  roles.runtime  -> owns: runtime/roles/*
  roles.session  -> owns: runtime/role_sessions/*, runtime/conversations/*
  (no path belongs to both)
"""

from __future__ import annotations

import pathlib

import yaml

BACKEND_ROOT = pathlib.Path(__file__).parent.parent
CELLS_YAML = BACKEND_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"
RUNTIME_CELL_YAML = BACKEND_ROOT / "polaris" / "cells" / "roles" / "runtime" / "cell.yaml"
SESSION_CELL_YAML = BACKEND_ROOT / "polaris" / "cells" / "roles" / "session" / "cell.yaml"


def _load_yaml(path: pathlib.Path) -> object:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _find_cell(catalog: list, cell_id: str) -> dict | None:
    """Return the catalog entry for a given cell id, or None."""
    for cell in catalog:
        if isinstance(cell, dict) and cell.get("id") == cell_id:
            return cell
    return None


def _load_catalog_cells() -> list[dict]:
    raw = _load_yaml(CELLS_YAML)
    assert isinstance(raw, dict), "catalog YAML root must be a mapping"
    cells = raw.get("cells") or []
    assert isinstance(cells, list), "catalog.cells must be a list"
    return cells


# ---------------------------------------------------------------------------
# Tests against individual cell.yaml files
# ---------------------------------------------------------------------------


class TestRuntimeCellYaml:
    def setup_method(self):
        self.data = _load_yaml(RUNTIME_CELL_YAML)

    def test_id_is_roles_runtime(self):
        assert self.data.get("id") == "roles.runtime"

    def test_state_owners_contains_only_runtime_roles(self):
        owners: list = self.data.get("state_owners") or []
        assert "runtime/roles/*" in owners, "roles.runtime must own runtime/roles/*"

    def test_state_owners_does_not_contain_role_sessions(self):
        owners: list = self.data.get("state_owners") or []
        assert "runtime/role_sessions/*" not in owners, (
            "roles.runtime must NOT own runtime/role_sessions/* (that belongs to roles.session)"
        )

    def test_state_owners_does_not_contain_conversations(self):
        owners: list = self.data.get("state_owners") or []
        assert "runtime/conversations/*" not in owners, (
            "roles.runtime must NOT own runtime/conversations/* (that belongs to roles.session)"
        )

    def test_effects_allowed_contains_write_roles(self):
        effects: list = self.data.get("effects_allowed") or []
        assert "fs.write:runtime/roles/*" in effects, "roles.runtime must have fs.write:runtime/roles/* effect"

    def test_effects_allowed_does_not_write_role_sessions(self):
        effects: list = self.data.get("effects_allowed") or []
        assert "fs.write:runtime/role_sessions/*" not in effects, (
            "roles.runtime must NOT write runtime/role_sessions/* (that is session Cell's responsibility)"
        )


class TestSessionCellYaml:
    def setup_method(self):
        self.data = _load_yaml(SESSION_CELL_YAML)

    def test_id_is_roles_session(self):
        assert self.data.get("id") == "roles.session"

    def test_state_owners_contains_role_sessions(self):
        owners: list = self.data.get("state_owners") or []
        assert "runtime/role_sessions/*" in owners, "roles.session must own runtime/role_sessions/*"

    def test_state_owners_contains_conversations(self):
        owners: list = self.data.get("state_owners") or []
        assert "runtime/conversations/*" in owners, "roles.session must own runtime/conversations/*"

    def test_state_owners_does_not_contain_runtime_roles(self):
        owners: list = self.data.get("state_owners") or []
        assert "runtime/roles/*" not in owners, (
            "roles.session must NOT own runtime/roles/* (that belongs to roles.runtime)"
        )

    def test_effects_allowed_does_not_write_runtime_roles(self):
        effects: list = self.data.get("effects_allowed") or []
        assert "fs.write:runtime/roles/*" not in effects, (
            "roles.session must NOT write runtime/roles/* (that is runtime Cell's responsibility)"
        )


# ---------------------------------------------------------------------------
# Tests against the catalog (cells.yaml) — catalog must stay in sync
# ---------------------------------------------------------------------------


class TestCatalogConsistency:
    def setup_method(self):
        self.cells = _load_catalog_cells()
        self.runtime_entry = _find_cell(self.cells, "roles.runtime")
        self.session_entry = _find_cell(self.cells, "roles.session")

    def test_runtime_entry_exists_in_catalog(self):
        assert self.runtime_entry is not None, "roles.runtime must be declared in cells.yaml catalog"

    def test_session_entry_exists_in_catalog(self):
        assert self.session_entry is not None, "roles.session must be declared in cells.yaml catalog"

    def test_catalog_runtime_state_owners_no_role_sessions(self):
        owners: list = (self.runtime_entry or {}).get("state_owners") or []
        assert "runtime/role_sessions/*" not in owners, "catalog: roles.runtime must NOT own runtime/role_sessions/*"

    def test_catalog_session_state_owners_contains_role_sessions(self):
        owners: list = (self.session_entry or {}).get("state_owners") or []
        assert "runtime/role_sessions/*" in owners, "catalog: roles.session must own runtime/role_sessions/*"

    def test_catalog_session_state_owners_does_not_contain_runtime_roles(self):
        owners: list = (self.session_entry or {}).get("state_owners") or []
        assert "runtime/roles/*" not in owners, "catalog: roles.session must NOT own runtime/roles/*"

    def test_no_overlap_between_runtime_and_session_state_owners(self):
        runtime_owners: set = set((self.runtime_entry or {}).get("state_owners") or [])
        session_owners: set = set((self.session_entry or {}).get("state_owners") or [])
        overlap = runtime_owners & session_owners
        assert not overlap, f"state_owners overlap between roles.runtime and roles.session: {overlap}"

    def test_catalog_and_cell_yaml_runtime_state_owners_agree(self):
        catalog_owners: set = set((self.runtime_entry or {}).get("state_owners") or [])
        cell_data = _load_yaml(RUNTIME_CELL_YAML)
        cell_owners: set = set(cell_data.get("state_owners") or [])
        assert catalog_owners == cell_owners, (
            f"roles.runtime state_owners mismatch between catalog and cell.yaml: "
            f"catalog={sorted(catalog_owners)}, cell={sorted(cell_owners)}"
        )

    def test_catalog_and_cell_yaml_session_state_owners_agree(self):
        catalog_owners: set = set((self.session_entry or {}).get("state_owners") or [])
        cell_data = _load_yaml(SESSION_CELL_YAML)
        cell_owners: set = set(cell_data.get("state_owners") or [])
        assert catalog_owners == cell_owners, (
            f"roles.session state_owners mismatch between catalog and cell.yaml: "
            f"catalog={sorted(catalog_owners)}, cell={sorted(cell_owners)}"
        )
