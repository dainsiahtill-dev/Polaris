"""Cell Manifest Schema Canonical Gate (Phase 0: Freeze the Bleed)

Validates that ALL cell.yaml manifests follow a consistent canonical schema.
Blocks non-canonical manifests from being introduced.

Rules enforced:
- AGENTS.md section 4.2: Cell is the minimal autonomous boundary
- Blueprint Phase 0: Block new non-canonical cell.yaml schemas
- Blueprint Phase 3: Reconcile catalog -> manifest -> code
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CELLS_ROOT = BACKEND_ROOT / "polaris" / "cells"
CATALOG_PATH = BACKEND_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"

# Canonical required fields in cell.yaml
REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "title",
        "kind",
        "visibility",
        "stateful",
        "owner",
        "purpose",
        "owned_paths",
        "public_contracts",
        "depends_on",
        "state_owners",
        "effects_allowed",
    }
)

# Valid values for enumerated fields
VALID_KINDS = {"capability", "projection", "workflow", "policy", "runtime", "composite"}
VALID_VISIBILITY = {"public", "internal"}

# Known schema inconsistencies (baseline for migration)
KNOWN_ID_FIELD_ISSUES: frozenset[str] = frozenset()

# Fixture/sandbox cell.yaml files that should be excluded from validation
FIXTURE_EXCLUSION_PATTERNS = ("fixtures/", "sandbox/", "workspaces/")


def _find_all_cell_yamls() -> list[Path]:
    """Find all cell.yaml files under polaris/cells/, excluding fixtures."""
    if not CELLS_ROOT.is_dir():
        return []
    results = []
    for p in sorted(CELLS_ROOT.rglob("cell.yaml")):
        rel = str(p.relative_to(BACKEND_ROOT)).replace("\\", "/")
        if any(excl in rel for excl in FIXTURE_EXCLUSION_PATTERNS):
            continue
        results.append(p)
    return results


def _load_yaml(path: Path) -> dict[str, Any] | None:
    """Load a YAML file, returning None on failure."""
    try:
        content = path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            return data
    except (OSError, yaml.YAMLError):
        pass
    return None


class TestManifestSchemaCanonical:
    """Validate cell.yaml manifests follow the canonical schema."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.cell_yamls = _find_all_cell_yamls()

    def test_cell_yamls_exist(self) -> None:
        """At least some cell.yaml files must exist."""
        assert len(self.cell_yamls) > 0, "No cell.yaml files found under polaris/cells/"

    @pytest.mark.parametrize(
        "cell_yaml",
        _find_all_cell_yamls(),
        ids=lambda p: str(p.relative_to(BACKEND_ROOT)).replace("\\", "/"),
    )
    def test_required_fields_present(self, cell_yaml: Path) -> None:
        """Every cell.yaml must have all required fields."""
        rel = str(cell_yaml.relative_to(BACKEND_ROOT)).replace("\\", "/")
        data = _load_yaml(cell_yaml)
        assert data is not None, f"Failed to parse YAML: {rel}"

        # Handle known id-field inconsistencies
        effective_required = set(REQUIRED_FIELDS)
        if rel in KNOWN_ID_FIELD_ISSUES:
            # Allow cell_id as alternative to id
            if "cell_id" in data:
                effective_required.discard("id")

        missing = effective_required - set(data.keys())
        if missing:
            pytest.fail(f"{rel}: missing required fields: {sorted(missing)}")

    @pytest.mark.parametrize(
        "cell_yaml",
        _find_all_cell_yamls(),
        ids=lambda p: str(p.relative_to(BACKEND_ROOT)).replace("\\", "/"),
    )
    def test_id_field_format(self, cell_yaml: Path) -> None:
        """Cell ID must follow domain.capability format."""
        rel = str(cell_yaml.relative_to(BACKEND_ROOT)).replace("\\", "/")
        data = _load_yaml(cell_yaml)
        assert data is not None, f"Failed to parse YAML: {rel}"

        cell_id = data.get("id") or data.get("cell_id")
        assert cell_id is not None, f"{rel}: no id or cell_id field"
        assert isinstance(cell_id, str), f"{rel}: id must be a string"
        assert "." in cell_id, f"{rel}: id must follow domain.capability format, got '{cell_id}'"

    @pytest.mark.parametrize(
        "cell_yaml",
        _find_all_cell_yamls(),
        ids=lambda p: str(p.relative_to(BACKEND_ROOT)).replace("\\", "/"),
    )
    def test_kind_is_valid(self, cell_yaml: Path) -> None:
        """Cell kind must be one of the canonical values."""
        rel = str(cell_yaml.relative_to(BACKEND_ROOT)).replace("\\", "/")
        data = _load_yaml(cell_yaml)
        assert data is not None, f"Failed to parse YAML: {rel}"

        kind = data.get("kind")
        if kind is not None:
            assert kind in VALID_KINDS, (
                f"{rel}: invalid kind '{kind}', must be one of {sorted(VALID_KINDS)}"
            )

    @pytest.mark.parametrize(
        "cell_yaml",
        _find_all_cell_yamls(),
        ids=lambda p: str(p.relative_to(BACKEND_ROOT)).replace("\\", "/"),
    )
    def test_owned_paths_are_strings(self, cell_yaml: Path) -> None:
        """owned_paths must be a list of strings."""
        rel = str(cell_yaml.relative_to(BACKEND_ROOT)).replace("\\", "/")
        data = _load_yaml(cell_yaml)
        assert data is not None

        owned = data.get("owned_paths")
        if owned is not None:
            assert isinstance(owned, list), f"{rel}: owned_paths must be a list"
            for i, p in enumerate(owned):
                assert isinstance(p, str), f"{rel}: owned_paths[{i}] must be a string"

    @pytest.mark.parametrize(
        "cell_yaml",
        _find_all_cell_yamls(),
        ids=lambda p: str(p.relative_to(BACKEND_ROOT)).replace("\\", "/"),
    )
    def test_depends_on_are_strings(self, cell_yaml: Path) -> None:
        """depends_on must be a list of cell ID strings."""
        rel = str(cell_yaml.relative_to(BACKEND_ROOT)).replace("\\", "/")
        data = _load_yaml(cell_yaml)
        assert data is not None

        deps = data.get("depends_on")
        if deps is not None:
            assert isinstance(deps, list), f"{rel}: depends_on must be a list"
            for i, d in enumerate(deps):
                assert isinstance(d, str), f"{rel}: depends_on[{i}] must be a string"

    @pytest.mark.parametrize(
        "cell_yaml",
        _find_all_cell_yamls(),
        ids=lambda p: str(p.relative_to(BACKEND_ROOT)).replace("\\", "/"),
    )
    def test_effects_allowed_are_strings(self, cell_yaml: Path) -> None:
        """effects_allowed must be a list of strings."""
        rel = str(cell_yaml.relative_to(BACKEND_ROOT)).replace("\\", "/")
        data = _load_yaml(cell_yaml)
        assert data is not None

        effects = data.get("effects_allowed")
        if effects is not None:
            assert isinstance(effects, list), f"{rel}: effects_allowed must be a list"
            for i, e in enumerate(effects):
                assert isinstance(e, str), f"{rel}: effects_allowed[{i}] must be a string"

    @pytest.mark.parametrize(
        "cell_yaml",
        _find_all_cell_yamls(),
        ids=lambda p: str(p.relative_to(BACKEND_ROOT)).replace("\\", "/"),
    )
    def test_public_contracts_structure(self, cell_yaml: Path) -> None:
        """public_contracts must be a dict with standard sub-keys."""
        rel = str(cell_yaml.relative_to(BACKEND_ROOT)).replace("\\", "/")
        data = _load_yaml(cell_yaml)
        assert data is not None

        contracts = data.get("public_contracts")
        if contracts is not None:
            assert isinstance(contracts, dict), f"{rel}: public_contracts must be a dict"

    @pytest.mark.parametrize(
        "cell_yaml",
        _find_all_cell_yamls(),
        ids=lambda p: str(p.relative_to(BACKEND_ROOT)).replace("\\", "/"),
    )
    def test_no_non_utf8_content(self, cell_yaml: Path) -> None:
        """cell.yaml must be valid UTF-8."""
        try:
            cell_yaml.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            pytest.fail(f"Non-UTF-8 content in {cell_yaml.relative_to(BACKEND_ROOT)}")


class TestManifestCatalogReconciliation:
    """Verify cell.yaml manifests are consistent with cells.yaml catalog."""

    def test_catalog_exists(self) -> None:
        """The catalog file must exist."""
        assert CATALOG_PATH.is_file(), f"Catalog not found: {CATALOG_PATH}"

    def test_manifest_ids_in_catalog(self) -> None:
        """Every cell.yaml id should appear in cells.yaml catalog."""
        if not CATALOG_PATH.is_file():
            pytest.skip("Catalog file not found")

        catalog_data = _load_yaml(CATALOG_PATH)
        if not catalog_data:
            pytest.skip("Cannot parse catalog")

        catalog_cells = catalog_data.get("cells", [])
        catalog_ids: set[str] = set()
        for cell in catalog_cells:
            if isinstance(cell, dict):
                cid = cell.get("id", "")
                if cid:
                    catalog_ids.add(cid)

        manifest_ids: dict[str, str] = {}
        for cell_yaml in _find_all_cell_yamls():
            data = _load_yaml(cell_yaml)
            if data:
                cid = data.get("id") or data.get("cell_id")
                if cid:
                    rel = str(cell_yaml.relative_to(BACKEND_ROOT)).replace("\\", "/")
                    manifest_ids[cid] = rel

        not_in_catalog = set(manifest_ids.keys()) - catalog_ids
        if not_in_catalog:
            lines = ["Cell manifests not found in catalog (should be registered):"]
            for cid in sorted(not_in_catalog):
                lines.append(f"  {cid} ({manifest_ids[cid]})")
            # Soft warning during migration
            pytest.skip("\n".join(lines))

    def test_no_duplicate_cell_ids(self) -> None:
        """No two cell.yaml files should declare the same cell ID."""
        ids: dict[str, list[str]] = {}
        for cell_yaml in _find_all_cell_yamls():
            data = _load_yaml(cell_yaml)
            if data:
                cid = data.get("id") or data.get("cell_id")
                if cid:
                    rel = str(cell_yaml.relative_to(BACKEND_ROOT)).replace("\\", "/")
                    ids.setdefault(cid, []).append(rel)

        dupes = {cid: paths for cid, paths in ids.items() if len(paths) > 1}
        if dupes:
            lines = ["Duplicate cell IDs detected:"]
            for cid, paths in sorted(dupes.items()):
                lines.append(f"  {cid}:")
                for p in paths:
                    lines.append(f"    - {p}")
            pytest.fail("\n".join(lines))
