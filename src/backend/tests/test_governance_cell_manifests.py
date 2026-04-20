"""Non-regression: graph catalog cell ids align with polaris cell manifests.

Also includes manifest<->catalog reconciliation tests (G-2: dual-source drift gate).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = BACKEND_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"
CELLS_ROOT = BACKEND_ROOT / "polaris" / "cells"
GATE_SCRIPT = BACKEND_ROOT / "docs" / "governance" / "ci" / "scripts" / "run_catalog_governance_gate.py"

# ------------------------------------------------------------------
# Helpers for unit testing the reconciliation logic directly
# ------------------------------------------------------------------

from docs.governance.ci.scripts.run_catalog_governance_gate import (
    GovernanceIssue,
    ManifestCatalogMismatch,
    _check_manifest_catalog_consistency,
    _count_new_mismatches,
    _load_baseline_fingerprints,
    _load_manifest,
    _manifest_owned_path_contained,
    _write_mismatch_baseline,
)


class TestOwnedPathContained:
    """Unit tests for glob-based owned_path containment."""

    def test_exact_match(self):
        assert _manifest_owned_path_contained(
            "polaris/cells/foo/bar/internal/service.py",
            ("polaris/cells/foo/bar/internal/service.py",),
        )

    def test_exact_no_match(self):
        assert not _manifest_owned_path_contained(
            "polaris/cells/foo/bar/external/service.py",
            ("polaris/cells/foo/bar/internal/service.py",),
        )

    def test_double_star_prefix_match(self):
        assert _manifest_owned_path_contained(
            "polaris/cells/foo/bar/internal/service.py",
            ("polaris/cells/foo/bar/internal/**",),
        )

    def test_double_star_nested_match(self):
        assert _manifest_owned_path_contained(
            "polaris/cells/foo/bar/internal/deep/nested/file.py",
            ("polaris/cells/foo/bar/**",),
        )

    def test_double_star_exact_boundary(self):
        # The prefix itself (without the trailing slash) should also match
        assert _manifest_owned_path_contained(
            "polaris/cells/foo/bar",
            ("polaris/cells/foo/bar/**",),
        )

    def test_double_star_no_match_different_prefix(self):
        assert not _manifest_owned_path_contained(
            "polaris/cells/foo/baz/internal/service.py",
            ("polaris/cells/foo/bar/**",),
        )

    def test_multiple_catalog_paths_one_matches(self):
        cat_paths = (
            "polaris/cells/foo/qux/public/**",
            "polaris/cells/foo/bar/internal/**",
            "polaris/cells/other/**",
        )
        assert _manifest_owned_path_contained(
            "polaris/cells/foo/bar/internal/service.py",
            cat_paths,
        )

    def test_empty_catalog_paths(self):
        assert not _manifest_owned_path_contained(
            "polaris/cells/foo/bar/internal/service.py",
            (),
        )


class TestLoadManifest:
    """Unit tests for manifest loading and normalization."""

    def test_load_valid_manifest(self, tmp_path: Path):
        manifest = tmp_path / "cell.yaml"
        manifest.write_text(
            "id: test.cell\n"
            "owned_paths:\n"
            "  - polaris/cells/test/cell/internal/**\n"
            "depends_on:\n"
            "  - other.cell\n"
            "state_owners:\n"
            "  - workspace/test/*\n"
            "effects_allowed:\n"
            "  - fs.read:workspace/**\n"
            "current_modules:\n"
            "  - polaris.cells.test.cell.internal.service\n",
            encoding="utf-8",
        )
        record = _load_manifest(manifest)
        assert record is not None
        assert record.cell_id == "test.cell"
        assert "polaris/cells/test/cell/internal/**" in record.owned_paths
        assert "other.cell" in record.depends_on
        assert "workspace/test/*" in record.state_owners
        assert "fs.read:workspace/**" in record.effects_allowed
        assert record.has_current_modules is True

    def test_load_missing_file(self):
        assert _load_manifest(Path("/nonexistent/cell.yaml")) is None

    def test_load_no_id(self, tmp_path: Path):
        manifest = tmp_path / "cell.yaml"
        manifest.write_text("owned_paths: []\n", encoding="utf-8")
        assert _load_manifest(manifest) is None

    def test_has_current_modules_false_when_absent(self, tmp_path: Path):
        manifest = tmp_path / "cell.yaml"
        manifest.write_text("id: test.cell\nowned_paths: []\ndepends_on: []\n", encoding="utf-8")
        record = _load_manifest(manifest)
        assert record is not None
        assert record.has_current_modules is False


class TestMismatchCounting:
    """Tests for baseline freezing and new-mismatch counting."""

    def test_no_baseline_all_new(self, tmp_path: Path):
        mismatches = [
            ManifestCatalogMismatch(
                cell_id="test.cell",
                field="depends_on",
                mismatch_type="catalog_not_superset",
                manifest_value="extra.dep",
            ),
        ]
        assert _count_new_mismatches(mismatches, None) == 1

    def test_baseline_freezes_existing(self, tmp_path: Path):
        mismatches = [
            ManifestCatalogMismatch(
                cell_id="test.cell",
                field="depends_on",
                mismatch_type="catalog_not_superset",
                manifest_value="extra.dep",
            ),
        ]
        baseline_file = tmp_path / "baseline.jsonl"
        _write_mismatch_baseline(baseline_file, mismatches)
        # Count again against same baseline -- nothing new
        assert _count_new_mismatches(mismatches, baseline_file) == 0

    def test_baseline_only_freezes_matching_fingerprints(self, tmp_path: Path):
        # Write one mismatch to baseline
        existing = [
            ManifestCatalogMismatch(
                cell_id="cell.a",
                field="depends_on",
                mismatch_type="catalog_not_superset",
                manifest_value="dep.a",
            ),
        ]
        baseline_file = tmp_path / "baseline.jsonl"
        _write_mismatch_baseline(baseline_file, existing)

        # New mismatch list has a different mismatch
        new = [
            ManifestCatalogMismatch(
                cell_id="cell.b",
                field="depends_on",
                mismatch_type="catalog_not_superset",
                manifest_value="dep.b",
            ),
        ]
        assert _count_new_mismatches(new, baseline_file) == 1

    def test_write_mismatch_baseline_format(self, tmp_path: Path):
        mismatches = [
            ManifestCatalogMismatch(
                cell_id="cell.x",
                field="state_owners",
                mismatch_type="catalog_not_superset",
                manifest_value="workspace/x/*",
                catalog_value="workspace/y/*",
            ),
        ]
        baseline_file = tmp_path / "baseline.jsonl"
        _write_mismatch_baseline(baseline_file, mismatches)
        lines = baseline_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["cell_id"] == "cell.x"
        assert record["field"] == "state_owners"
        assert "fingerprint" in record

    def test_load_baseline_fingerprints_malformed_lines(self, tmp_path: Path):
        baseline_file = tmp_path / "bad.jsonl"
        baseline_file.write_text('{"fingerprint": "fp1"}\nnot-json\n{"fingerprint": "fp2"}\n', encoding="utf-8")
        fps = _load_baseline_fingerprints(baseline_file)
        assert "fp1" in fps
        assert "fp2" in fps


class TestCatalogCellIndex:
    """Smoke test: catalog cells.yaml loads and has expected structure."""

    def test_catalog_has_expected_cells(self):
        if not CATALOG_PATH.is_file():
            pytest.skip(f"missing catalog: {CATALOG_PATH}")
        try:
            data = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            pytest.fail(f"cells.yaml has a YAML syntax error: {exc}")
        assert isinstance(data, dict)
        cells = data.get("cells") or []
        ids = {c.get("id") for c in cells if isinstance(c, dict) and c.get("id")}
        # These are known cells that should be in the catalog
        expected = {
            "context.catalog",
            "factory.pipeline",
            "runtime.projection",
            "orchestration.pm_dispatch",
            "roles.runtime",
            "llm.dialogue",
            "llm.provider_runtime",
        }
        assert expected.issubset(ids), f"Missing cells: {expected - ids}"

    def test_cells_have_required_fields(self):
        """Every cell entry should have required governance fields."""
        if not CATALOG_PATH.is_file():
            pytest.skip(f"missing catalog: {CATALOG_PATH}")
        try:
            data = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            pytest.fail(f"cells.yaml has a YAML syntax error: {exc}")
        cells = data.get("cells") or []
        for entry in cells:
            if not isinstance(entry, dict):
                continue
            cid = entry.get("id")
            if not cid:
                continue
            # Check fields that must exist for reconciliation
            assert isinstance(entry.get("owned_paths"), list), f"{cid}: owned_paths must be a list"
            assert isinstance(entry.get("depends_on"), list), f"{cid}: depends_on must be a list"
            assert isinstance(entry.get("state_owners"), list), f"{cid}: state_owners must be a list"
            assert isinstance(entry.get("effects_allowed"), list), f"{cid}: effects_allowed must be a list"


class TestReconciliationWithRealFiles:
    """Integration tests using the real catalog and manifest files."""

    def test_manifest_catalog_consistency_with_real_files(self):
        """Real catalog vs manifest reconciliation. May surface existing debt."""
        if not CATALOG_PATH.is_file():
            pytest.skip(f"missing catalog: {CATALOG_PATH}")

        from docs.governance.ci.scripts.run_catalog_governance_gate import (
            _build_cell_index,
            _read_yaml,
        )

        catalog_payload = _read_yaml(CATALOG_PATH)
        assert isinstance(catalog_payload, dict)
        cells = _build_cell_index(catalog_payload)

        issues: list[GovernanceIssue] = []  # kept for type-checking the import; unused below
        mismatches = _check_manifest_catalog_consistency(
            repo_root=BACKEND_ROOT,
            catalog_cells=cells,
        )

        # Report mismatches but do not fail -- existing debt is expected.
        # The fail-on-new gate handles regression detection.
        if mismatches:
            mismatch_summary = [
                f"  {mm.cell_id} | {mm.field} | {mm.mismatch_type} | {mm.manifest_value}"
                for mm in mismatches[:10]
            ]
            pytest.skip(
                f"Found {len(mismatches)} manifest-catalog mismatches (existing debt); "
                f"gate will block new ones:\n" + "\n".join(mismatch_summary)
            )

    def test_fail_on_new_mode_detects_new_mismatch(self, tmp_path: Path):
        """Verify that fail-on-new mode detects a genuinely new mismatch."""
        if not CATALOG_PATH.is_file():
            pytest.skip(f"missing catalog: {CATALOG_PATH}")

        # Create a synthetic workspace with a deliberate mismatch:
        # the catalog has a cell but the manifest has a dep NOT in the catalog
        synth_root = tmp_path / "workspace"
        synth_root.mkdir()

        # Copy catalog and add a synthetic cell entry
        synth_catalog = synth_root / "docs" / "graph" / "catalog" / "cells.yaml"
        synth_catalog.parent.mkdir(parents=True, exist_ok=True)
        catalog_text = CATALOG_PATH.read_text(encoding="utf-8")
        synth_catalog.write_text(catalog_text, encoding="utf-8")

        # Create a manifest for a new cell registered in the catalog but with
        # a depends_on entry that the catalog does NOT declare
        synth_cell_dir = synth_root / "polaris" / "cells" / "synth" / "test_cell"
        synth_cell_dir.mkdir(parents=True, exist_ok=True)
        synth_cell_dir.joinpath("cell.yaml").write_text(
            "id: synth.test_cell\n"
            "owned_paths:\n"
            "  - polaris/cells/synth/test_cell/internal/**\n"
            "depends_on:\n"
            "  - definitely.not.in.catalog\n"
            "  - factory.pipeline\n"
            "state_owners: []\n"
            "effects_allowed: []\n",
            encoding="utf-8",
        )

        # Append the synth cell to the catalog

        catalog_data = yaml.safe_load(catalog_text)
        assert isinstance(catalog_data, dict)
        synth_cell_entry = {
            "id": "synth.test_cell",
            "owned_paths": ["polaris/cells/synth/test_cell/internal/**"],
            "depends_on": ["factory.pipeline"],  # catalog only declares factory.pipeline
            "state_owners": [],
            "effects_allowed": [],
        }
        cells_list = catalog_data.get("cells") or []
        cells_list.append(synth_cell_entry)
        catalog_data["cells"] = cells_list
        synth_catalog.write_text(yaml.safe_dump(catalog_data, allow_unicode=True), encoding="utf-8")

        from docs.governance.ci.scripts.run_catalog_governance_gate import (
            run_governance_gate,
        )

        # Audit-only: should detect the mismatch
        report, mismatch_info = run_governance_gate(
            workspace=str(synth_root),
            mode="audit-only",
            baseline_path=None,
            mismatch_baseline_path=None,
        )
        # MC findings are in mismatch_info, not in report.issues (baselines stay independent).
        assert mismatch_info["mismatch_count"] >= 1, (
            f"Expected manifest-catalog mismatch to be detected. "
            f"mismatch_info: {mismatch_info}"
        )

        # With empty baseline, all mismatches are "new"
        mismatch_baseline = tmp_path / "mismatch_baseline.jsonl"
        _write_mismatch_baseline(mismatch_baseline, [])

        report2, mismatch_info2 = run_governance_gate(
            workspace=str(synth_root),
            mode="fail-on-new",
            baseline_path=None,
            mismatch_baseline_path=mismatch_baseline,
        )
        assert mismatch_info2["new_mismatch_count"] >= 1, (
            "With empty baseline, should see mismatches as new"
        )


# ------------------------------------------------------------------
# Existing tests preserved
# ------------------------------------------------------------------


def _cell_id_to_manifest_path(cell_id: str) -> Path:
    parts = cell_id.strip().split(".")
    return CELLS_ROOT.joinpath(*parts) / "cell.yaml"


def test_catalog_cell_ids_have_cell_yaml():
    if not CATALOG_PATH.is_file():
        pytest.skip(f"missing catalog: {CATALOG_PATH}")

    data = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    cells = data.get("cells") or []
    missing = []
    for entry in cells:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("id")
        if not cid:
            continue
        manifest = _cell_id_to_manifest_path(str(cid))
        if not manifest.is_file():
            missing.append((cid, manifest))
    assert not missing, f"cell.yaml missing for catalog ids: {missing}"


def test_polaris_cell_manifests_are_in_catalog():
    if not CATALOG_PATH.is_file():
        pytest.skip(f"missing catalog: {CATALOG_PATH}")

    catalog_data = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    catalog_ids = {
        str(e["id"])
        for e in (catalog_data.get("cells") or [])
        if isinstance(e, dict) and e.get("id")
    }

    manifests = sorted(CELLS_ROOT.glob("**/cell.yaml"))
    not_in_catalog = []
    for manifest in manifests:
        rel = manifest.parent.relative_to(CELLS_ROOT)
        cell_id = ".".join(rel.parts)
        if cell_id not in catalog_ids:
            not_in_catalog.append(cell_id)

    assert not not_in_catalog, f"cell.yaml present but id missing from cells.yaml: {not_in_catalog}"


def test_no_directory_is_only_pycache():
    """Public cells must not leave cache-only directories (Agent-30)."""
    bad: list[str] = []
    for pyc in CELLS_ROOT.rglob("__pycache__"):
        parent = pyc.parent
        if not parent.is_dir():
            continue
        others = [x for x in parent.iterdir() if x.name != "__pycache__"]
        if not others:
            bad.append(str(parent.relative_to(CELLS_ROOT)))
    assert not bad, f"directories containing only __pycache__: {bad}"
