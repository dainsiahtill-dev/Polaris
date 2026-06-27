"""Tests for runtime-owned repair environment preparation projections."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.director.runtime.internal.repair_kernel.environment import (
    environment_prep_catalog_summary,
    environment_prep_plans_from_requirements,
    environment_refresh_metadata_for_files,
    environment_refresh_requirements_from_receipts,
)
from polaris.cells.director.runtime.public import (
    QueryDirectorRepairEnvironmentPrepCatalogV1,
    QueryDirectorRepairEnvironmentRefreshRequirementsV1,
    RepairReceiptV1,
    query_director_repair_environment_prep_catalog,
    query_director_repair_environment_refresh_requirements,
)


def test_manifest_write_projects_environment_refresh_metadata() -> None:
    metadata = environment_refresh_metadata_for_files(
        files_changed=("package.json",),
        after_hashes={"package.json": "after-package-hash"},
    )

    assert metadata["environment_refresh_required"] is True
    requirement = metadata["environment_refresh_requirements"][0]
    assert requirement["schema_version"] == "director.environment_refresh_requirement.v1"
    assert requirement["ecosystem"] == "node"
    assert requirement["package_manager"] == "npm"
    assert requirement["manifest"] == "package.json"
    assert requirement["command"] == ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]
    assert requirement["authoritative_repair"] is False


def test_environment_prep_prefers_existing_node_lockfile(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"dependencies":{"left-pad":"1.3.0"}}\n', encoding="utf-8")
    (tmp_path / "package-lock.json").write_text('{"lockfileVersion":3}\n', encoding="utf-8")

    requirements = environment_refresh_requirements_from_receipts(
        (
            {
                "receipt_id": "receipt-node-dep",
                "files_changed": ["package.json"],
                "after_hashes": {"package.json": "after-package-hash"},
                "metadata": {},
            },
        ),
        workspace=tmp_path,
    )
    plans = environment_prep_plans_from_requirements(requirements, workspace=tmp_path)

    assert len(requirements) == 1
    assert requirements[0]["lockfile"] == "package-lock.json"
    assert requirements[0]["command"] == ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"]
    assert len(plans) == 1
    assert plans[0].command == ("npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund")
    assert plans[0].policy["command_source"] == "director.runtime.environment_prep_catalog"
    assert plans[0].policy["llm_generated_command_allowed"] is False


def test_public_environment_prep_catalog_and_requirement_query(tmp_path: Path) -> None:
    catalog = query_director_repair_environment_prep_catalog(
        QueryDirectorRepairEnvironmentPrepCatalogV1(include_items=True)
    )
    summary = environment_prep_catalog_summary()

    assert catalog.summary["entry_count"] == summary["entry_count"]
    assert "node" in catalog.summary["ecosystems"]
    assert catalog.summary["adapter_runner_binding_only"] is True
    assert catalog.summary["llm_generated_commands_allowed"] is False

    receipt = RepairReceiptV1(
        receipt_id="receipt-package",
        plan_id="plan-package",
        source_tool="deterministic_runtime_dependency_repair",
        status="applied",
        authoritative=False,
        files_changed=("package.json",),
        after_hashes={"package.json": "after-package-hash"},
    )
    result = query_director_repair_environment_refresh_requirements(
        QueryDirectorRepairEnvironmentRefreshRequirementsV1(
            receipts=(receipt,),
            workspace=str(tmp_path),
        )
    )

    assert len(result.items) == 1
    assert len(result.plans) == 1
    assert result.items[0].manifest == "package.json"
    assert result.plans[0].policy["command_source"] == "director.runtime.environment_prep_catalog"
    assert result.to_dict()["summary"]["plan_count"] == 1
