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

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = BACKEND_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"
CELLS_ROOT = BACKEND_ROOT / "polaris" / "cells"
SUBGRAPHS_ROOT = BACKEND_ROOT / "docs" / "graph" / "subgraphs"
ROLES_KERNEL_ROOT = CELLS_ROOT / "roles" / "kernel"

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


def _load_subgraph(subgraph_id: str) -> dict[str, Any]:
    candidate = SUBGRAPHS_ROOT / f"{subgraph_id}.yaml"
    assert candidate.is_file(), f"subgraph not found: {candidate}"
    with candidate.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _load_json(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"JSON governance asset not found: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _is_infra_path(path: str) -> bool:
    """Return True if *path* targets kernelone/** or infrastructure/**."""
    return bool(re.match(r"polaris/kernelone/", path) or re.match(r"polaris/infrastructure/", path))


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
                    duplicates.append(f"'{path}' claimed by both '{seen[path]}' and '{cell_id}'")
                else:
                    seen[path] = cell_id

        assert not duplicates, "state_owner conflict(s) detected in cells.yaml:\n" + "\n".join(
            f"  - {d}" for d in duplicates
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
            "(ACGA 2.0 §2.3, P0-10); found: " + ", ".join(bad)
        )


class TestCellYamlCatalogConsistency:
    """Individual cell.yaml files must be consistent with the catalog."""

    _KEY_FIELDS = ("state_owners", "effects_allowed", "owned_paths")

    @pytest.mark.parametrize("cell_id", ["llm.control_plane", "llm.evaluation"])
    def test_cell_yaml_matches_catalog(self, cell_id: str) -> None:
        """catalog and cell.yaml must agree on state_owners/effects_allowed/owned_paths."""
        data = _load_catalog()
        cells = _catalog_cells(data)
        catalog_entry = next((c for c in cells if c.get("id") == cell_id), None)
        assert catalog_entry is not None, f"{cell_id} not found in catalog"

        cell_yaml = _load_cell_yaml(cell_id)
        if cell_yaml is None:
            pytest.skip(f"cell.yaml not present for {cell_id} — skipping consistency check")

        for field in self._KEY_FIELDS:
            catalog_val = sorted(catalog_entry.get(field) or [])
            cell_val = sorted(cell_yaml.get(field) or [])
            assert catalog_val == cell_val, (
                f"{cell_id}: field '{field}' differs between catalog and cell.yaml.\n"
                f"  catalog  : {catalog_val}\n"
                f"  cell.yaml: {cell_val}"
            )


class TestRolesKernelFinalRequestEvidenceMetadata:
    """B3.2 final-request cutoff metadata must have one generated truth."""

    def test_b32_metadata_surfaces_stay_in_lockstep(self) -> None:
        """Catalog, manifest, and generated context must expose the exact B3.2 slice."""
        expected_module = "polaris.cells.roles.kernel.public.final_request_evidence_cutoff"
        expected_query = "FactoryRoleEvidenceCutoffPort.resolve_cutoff_proof"
        expected_commands = {"FactoryRoleEvidenceCutoffRequestV1"}
        expected_results = {
            "FactoryRoleEvidenceCutoffAckV1",
            "FactoryRoleEvidenceCutoffSourceHeadV1",
            "FactoryRoleEvidenceCutoffProofV1",
            "FactoryRoleSemanticRequestIdentityV1",
            "FactoryRoleSemanticCandidateV1",
            "FactoryRoleFrozenSemanticRequestV1",
        }
        expected_tests = {
            "polaris/cells/roles/kernel/tests/test_final_request_evidence_cutoff.py",
            "polaris/cells/factory/pipeline/tests/test_factory_role_evidence_authority.py",
            "polaris/cells/roles/kernel/tests/test_factory_role_evidence_binding.py",
            "polaris/cells/roles/kernel/tests/test_role_turn_request_fact_projection.py",
            "polaris/cells/roles/kernel/tests/test_llm_caller_components.py",
        }
        generated_context_ref = "generated/context.pack.json"

        catalog_entry = next(cell for cell in _catalog_cells(_load_catalog()) if cell.get("id") == "roles.kernel")
        manifest = _load_cell_yaml("roles.kernel")
        assert manifest is not None
        generated_path = ROLES_KERNEL_ROOT / generated_context_ref
        generated = _load_json(generated_path) if generated_path.is_file() else {}

        violations: list[str] = []
        for surface_name, surface in (("catalog", catalog_entry), ("cell.yaml", manifest)):
            if expected_module not in (surface.get("current_modules") or []):
                violations.append(f"{surface_name}: missing current module {expected_module}")
            contracts = surface.get("public_contracts") or {}
            if expected_module not in (contracts.get("modules") or []):
                violations.append(f"{surface_name}: missing public module {expected_module}")
            missing_commands = sorted(expected_commands.difference(contracts.get("commands") or []))
            if missing_commands:
                violations.append(f"{surface_name}: missing commands {missing_commands}")
            if expected_query not in (contracts.get("queries") or []):
                violations.append(f"{surface_name}: missing query {expected_query}")
            missing_results = sorted(expected_results.difference(contracts.get("results") or []))
            if missing_results:
                violations.append(f"{surface_name}: missing results {missing_results}")
            misclassified_results = sorted(expected_commands.intersection(contracts.get("results") or []))
            if misclassified_results:
                violations.append(f"{surface_name}: request DTOs misclassified as results {misclassified_results}")
            missing_tests = sorted(expected_tests.difference((surface.get("verification") or {}).get("tests") or []))
            if missing_tests:
                violations.append(f"{surface_name}: missing tests {missing_tests}")
            if generated_context_ref not in (surface.get("generated_artifacts") or []):
                violations.append(f"{surface_name}: missing artifact {generated_context_ref}")

        if not generated:
            violations.append(f"generated context missing: {generated_path}")
        else:
            contracts = generated.get("public_contracts") or {}
            missing_commands = sorted(expected_commands.difference(contracts.get("commands") or []))
            if missing_commands:
                violations.append(f"generated context: missing commands {missing_commands}")
            if expected_query not in (contracts.get("queries") or []):
                violations.append(f"generated context: missing query {expected_query}")
            missing_results = sorted(expected_results.difference(contracts.get("results") or []))
            if missing_results:
                violations.append(f"generated context: missing results {missing_results}")
            misclassified_results = sorted(expected_commands.intersection(contracts.get("results") or []))
            if misclassified_results:
                violations.append(f"generated context: request DTOs misclassified as results {misclassified_results}")
            missing_tests = sorted(expected_tests.difference(generated.get("test_targets") or []))
            if missing_tests:
                violations.append(f"generated context: missing tests {missing_tests}")

        legacy_root = ROLES_KERNEL_ROOT / "context.pack.json"
        if legacy_root.exists():
            violations.append(
                "roles.kernel/context.pack.json must be retired after generated/context.pack.json becomes canonical"
            )

        assert not violations, "roles.kernel B3.2 metadata drift:\n" + "\n".join(f"  - {item}" for item in violations)


class TestChiefEngineerBlueprintGovernance:
    """Chief Engineer desktop route ownership and runtime blueprint state invariants."""

    def test_desktop_route_is_owned_by_chief_engineer_blueprint_cell(self) -> None:
        """The Chief Engineer v2 desktop route must be graph-owned by the blueprint cell."""
        data = _load_catalog()
        cells = _catalog_cells(data)
        catalog_entry = next((c for c in cells if c.get("id") == "chief_engineer.blueprint"), None)
        assert catalog_entry is not None, "chief_engineer.blueprint not found in catalog"

        route_module = "polaris.delivery.http.v2.chief_engineer"
        route_path = "polaris/delivery/http/v2/chief_engineer.py"
        assert route_module in (catalog_entry.get("current_modules") or [])
        assert route_path in (catalog_entry.get("owned_paths") or [])

        cell_yaml = _load_cell_yaml("chief_engineer.blueprint")
        assert cell_yaml is not None, "chief_engineer.blueprint cell.yaml not found"
        assert route_module in (cell_yaml.get("current_modules") or [])
        assert route_path in (cell_yaml.get("owned_paths") or [])

    def test_runtime_blueprints_are_declared_state_and_effects(self) -> None:
        """The blueprint cell must declare the runtime/blueprints persistence it writes."""
        cell_yaml = _load_cell_yaml("chief_engineer.blueprint")
        assert cell_yaml is not None, "chief_engineer.blueprint cell.yaml not found"

        assert "runtime/blueprints/*" in (cell_yaml.get("state_owners") or [])
        assert "fs.write:runtime/blueprints/*" in (cell_yaml.get("effects_allowed") or [])


class TestDirectorExecutionGovernance:
    """Director execution graph dependency invariants."""

    def test_declares_llm_dialogue_dependency_in_catalog_and_manifest(self) -> None:
        """Director execution imports llm.dialogue public service and must declare it."""
        data = _load_catalog()
        cells = _catalog_cells(data)
        catalog_entry = next((c for c in cells if c.get("id") == "director.execution"), None)
        assert catalog_entry is not None, "director.execution not found in catalog"
        assert "llm.dialogue" in (catalog_entry.get("depends_on") or [])

        cell_yaml = _load_cell_yaml("director.execution")
        assert cell_yaml is not None, "director.execution cell.yaml not found"
        assert "llm.dialogue" in (cell_yaml.get("depends_on") or [])

    def test_execution_governance_subgraph_tracks_llm_dialogue_edge(self) -> None:
        """The execution governance subgraph must expose Director role dialogue calls."""
        subgraph = _load_subgraph("execution_governance_pipeline")
        assert "llm.dialogue" in (subgraph.get("cells") or [])

        expected_relation = {
            "from": "director.execution",
            "to": "llm.dialogue",
            "type": "commands",
            "contract": "InvokeRoleDialogueCommandV1",
        }
        assert expected_relation in (subgraph.get("relations") or [])


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
            "code_intelligence.engine",
            "director.execution",
            "director.runtime",
            "factory.cognitive_runtime",
            "policy.permission",
            "finops.budget_guard",
            "events.fact_stream",
            "kernelone.core",
            "kernelone.traceability",
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
            if cell_id == "audit.evidence" or cell_id in self._EXISTING_INFRA_PATH_VIOLATORS:
                continue
            for path in cell.get("owned_paths") or []:
                if _is_infra_path(path):
                    new_violations.append(f"Cell '{cell_id}' owns infra/kernelone path: '{path}'")

        assert not new_violations, (
            "NEW cells are claiming kernelone/infrastructure owned_paths "
            "(ACGA 2.0 §2.3). Fix these before the allowlist can shrink:\n"
            + "\n".join(f"  - {v}" for v in new_violations)
        )
