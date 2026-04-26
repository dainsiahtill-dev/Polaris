from __future__ import annotations

from pathlib import Path

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = BACKEND_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"
SUBGRAPHS_DIR = BACKEND_ROOT / "docs" / "graph" / "subgraphs"
FINAL_SPEC_PATH = BACKEND_ROOT / "docs" / "FINAL_SPEC.md"

EXPECTED_PUBLIC_CELLS = {
    "context.catalog",
    "delivery.api_gateway",
    "policy.workspace_guard",
    "runtime.state_owner",
    "runtime.projection",
    "audit.evidence",
    "archive.run_archive",
    "archive.task_snapshot_archive",
    "archive.factory_archive",
    "context.engine",
}

EXPECTED_MODULES = {
    "context.catalog": "polaris.cells.context.catalog.public.contracts",
    "delivery.api_gateway": "polaris.cells.delivery.api_gateway.public.contracts",
    "policy.workspace_guard": "polaris.cells.policy.workspace_guard.public.contracts",
    "runtime.state_owner": "polaris.cells.runtime.state_owner.public.contracts",
    "runtime.projection": "polaris.cells.runtime.projection.public.contracts",
    "audit.evidence": "polaris.cells.audit.evidence.public.contracts",
    "archive.run_archive": "polaris.cells.archive.run_archive.public.contracts",
    "archive.task_snapshot_archive": ("polaris.cells.archive.task_snapshot_archive.public.contracts"),
    "archive.factory_archive": "polaris.cells.archive.factory_archive.public.contracts",
    "context.engine": "polaris.cells.context.engine.public.contracts",
}

UNDECLARED_DRAFT_CELLS = {
    "llm.control_plane",
    "roles.runtime",
    "docs.court_workflow",
    "factory.pipeline",
    "orchestration.pm_planning",
    "orchestration.pm_dispatch",
    "chief_engineer.blueprint",
    "director.execution",
    "qa.audit_verdict",
    "policy.permission",
    "finops.budget_guard",
    "events.fact_stream",
    "orchestration.workflow_runtime",
    "storage.layout",
}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_yaml(path: Path) -> dict[str, object]:
    payload = yaml.safe_load(_read_text(path))
    assert isinstance(payload, dict)
    return payload


def _cell_root(cell_id: str) -> Path:
    return BACKEND_ROOT / "polaris" / "cells" / Path(*cell_id.split("."))


def test_catalog_matches_current_phase1_public_cells() -> None:
    payload = _load_yaml(CATALOG_PATH)

    assert payload["migration_status"] == "phase1_public_phase2_composite_phase3_business_cells_declared"

    cells = payload.get("cells")
    assert isinstance(cells, list)

    catalog_cells = {str(item.get("id")): item for item in cells if isinstance(item, dict) and item.get("id")}
    assert EXPECTED_PUBLIC_CELLS.issubset(set(catalog_cells))

    declared_subgraphs = set()

    for cell_id in EXPECTED_PUBLIC_CELLS:
        catalog_item = catalog_cells[cell_id]
        cell_root = _cell_root(cell_id)
        manifest_path = cell_root / "cell.yaml"
        contracts_path = cell_root / "public" / "contracts.py"
        context_pack_path = cell_root / "generated" / "context.pack.json"

        assert manifest_path.is_file()
        assert (cell_root / "README.agent.md").is_file()
        assert contracts_path.is_file()
        assert context_pack_path.is_file()

        manifest = _load_yaml(manifest_path)
        assert manifest["id"] == cell_id
        assert manifest["public_contracts"]["modules"] == [EXPECTED_MODULES[cell_id]]
        assert catalog_item["public_contracts"]["modules"] == [EXPECTED_MODULES[cell_id]]
        assert manifest.get("generated_artifacts") == ["generated/context.pack.json"]

        declared_subgraphs.update(str(value) for value in catalog_item.get("subgraphs", []))

    assert "storage_archive_pipeline" in declared_subgraphs

    subgraph_path = SUBGRAPHS_DIR / "storage_archive_pipeline.yaml"
    assert subgraph_path.is_file()

    subgraph = _load_yaml(subgraph_path)
    assert subgraph["id"] == "storage_archive_pipeline"
    assert set(subgraph["cells"]).issubset(EXPECTED_PUBLIC_CELLS)
    assert set(subgraph["entry_cells"]).issubset(EXPECTED_PUBLIC_CELLS)
    assert set(subgraph["exit_cells"]).issubset(EXPECTED_PUBLIC_CELLS)


def test_final_spec_describes_graph_as_current_phase1_state() -> None:
    text = _read_text(FINAL_SPEC_PATH)

    assert "phase1_public_phase2_composite_phase3_business_cells_declared" in text
    assert "当前 graph catalog 已声明的第一批公共 Cell 包括：" in text
    assert "当前 `docs/graph/subgraphs/` 中已恢复并纳入当前事实的子图资产包括：" in text
    assert "`storage_archive_pipeline`" in text

    for cell_id in EXPECTED_PUBLIC_CELLS:
        assert f"`{cell_id}`" in text

    assert "phase1_public_declared" not in text
