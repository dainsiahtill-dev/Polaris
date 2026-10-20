"""Current Factory input, not a legacy seed, owns quantitative depth validation.

Only synthetic private workspaces are inspected. No provider or target command
runs. The HTTP seam replaces admission scheduling while retaining the actual
request handler, FactoryStore, restored request, stage context and metric checker.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.factory_store import FactoryStore
from polaris.cells.factory.pipeline.internal.factory_workspace_quality import WorkspaceQualityRunner

L1_MINIMUMS = {
    "min_prod_files": 3,
    "min_prod_lines": 120,
    "min_behavior_symbols": 6,
    "min_branch_count": 3,
    "min_test_files": 1,
    "min_test_assertions": 2,
    "min_behavior_rules": 3,
    "min_primary_entities": 3,
    "min_edge_cases": 2,
}


def _contract(**extra: Any) -> dict[str, Any]:
    return {"schema_version": "polaris.delivery_depth_contract.v1", "level": 1, "minimums": dict(L1_MINIMUMS), **extra}


def _legacy(workspace: Path, minimums: dict[str, int]) -> None:
    path = workspace / ".polaris" / "catalog_contract.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"level": 1, "level_contract": {"level": 1, "minimums": minimums}}), encoding="utf-8")


def test_current_metadata_l1_contract_checks_empty_workspace_without_catalog(tmp_path: Path) -> None:
    result = WorkspaceQualityRunner(tmp_path).delivery_depth_contract_result(
        {"metadata": {"delivery_depth_contract": _contract()}}
    )
    assert result is not None, "current formal contract was silently skipped without catalog seed"
    assert result["passed"] is False
    assert result["exit_code"] == 1
    assert result["delivery_depth_contract"]["minimums"] == L1_MINIMUMS
    assert "production_source_lines=0 < 120" in result["stderr_tail"]
    assert not (tmp_path / ".polaris" / "catalog_contract.json").exists()


@pytest.mark.parametrize("location", ["context", "metadata"])
def test_current_contract_ignores_weaker_legacy_file(tmp_path: Path, location: str) -> None:
    _legacy(tmp_path, dict.fromkeys(L1_MINIMUMS, 0))
    context = (
        {"delivery_depth_contract": _contract()}
        if location == "context"
        else {"metadata": {"delivery_depth_contract": _contract()}}
    )
    result = WorkspaceQualityRunner(tmp_path).delivery_depth_contract_result(context)
    assert result is not None and result["passed"] is False
    assert result["delivery_depth_contract"]["minimums"]["min_prod_lines"] == 120


def test_current_contract_is_not_widened_by_stronger_legacy_file(tmp_path: Path) -> None:
    _legacy(tmp_path, dict(L1_MINIMUMS))
    current = _contract(minimums=dict.fromkeys(L1_MINIMUMS, 0))
    result = WorkspaceQualityRunner(tmp_path).delivery_depth_contract_result({"delivery_depth_contract": current})
    assert result is not None and result["passed"] is True
    assert result["delivery_depth_contract"]["minimums"]["min_prod_lines"] == 0


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        {},
        [],
        "contract",
        {"minimums": {}},
        {"minimums": []},
        {"minimums": {"min_prod_files": -1}},
        {"minimums": {"min_prod_files": True}},
        {"minimums": {"min_prod_files": 1.5}},
        {"minimums": {"min_prod_files": "3"}},
        {"minimums": {"min_typo": 3}},
        {"schema_version": "foreign.v9", "minimums": {"min_prod_files": 3}},
        {"level_contract": None},
    ],
)
def test_explicit_invalid_contract_fails_without_legacy_fallback(tmp_path: Path, invalid: Any) -> None:
    _legacy(tmp_path, dict.fromkeys(L1_MINIMUMS, 0))
    result = WorkspaceQualityRunner(tmp_path).delivery_depth_contract_result(
        {"metadata": {"delivery_depth_contract": invalid}}
    )
    assert result is not None and result["passed"] is False
    assert result["error_code"] == "delivery_depth_contract_invalid"


def test_conflicting_current_projections_fail_instead_of_selecting_weaker_minimums(tmp_path: Path) -> None:
    result = WorkspaceQualityRunner(tmp_path).delivery_depth_contract_result(
        {
            "delivery_depth_contract": _contract(minimums=dict.fromkeys(L1_MINIMUMS, 0)),
            "metadata": {"delivery_depth_contract": _contract()},
        }
    )
    assert result is not None and result["passed"] is False
    assert result["error_code"] == "delivery_depth_contract_invalid"
    assert "conflicting" in result["stderr_tail"]


@pytest.mark.parametrize(
    "changes",
    [
        {"level": True},
        {"level": 0},
        {"level": "1"},
        {"level_contract": []},
        {"level_contract": {"minimums": {}}},
        {"level_contract": {"level": 2, "minimums": dict(L1_MINIMUMS)}},
        {"level_contract": {"level": 1, "minimums": {"min_prod_files": 99}}},
    ],
)
def test_invalid_or_conflicting_nested_projection_cannot_rewrite_current_contract(
    tmp_path: Path, changes: dict[str, Any]
) -> None:
    result = WorkspaceQualityRunner(tmp_path).delivery_depth_contract_result(
        {"delivery_depth_contract": _contract(**changes)}
    )
    assert result is not None and result["passed"] is False
    assert result["error_code"] == "delivery_depth_contract_invalid"


def test_unhashable_nested_schema_returns_explicit_failed_result(tmp_path: Path) -> None:
    current = _contract(level_contract={"schema_version": [], "minimums": dict(L1_MINIMUMS)})
    result = WorkspaceQualityRunner(tmp_path).delivery_depth_contract_result({"delivery_depth_contract": current})
    assert result is not None and result["passed"] is False
    assert result["error_code"] == "delivery_depth_contract_invalid"


def test_nested_only_platform_projection_runs_the_same_depth_check(tmp_path: Path) -> None:
    current = {
        "schema_version": "polaris.delivery_depth_contract.v1",
        "level_contract": {"level": 1, "minimums": dict(L1_MINIMUMS)},
    }
    result = WorkspaceQualityRunner(tmp_path).delivery_depth_contract_result({"delivery_depth_contract": current})
    assert result is not None and result["passed"] is False
    assert result["delivery_depth_contract"]["minimums"] == L1_MINIMUMS


def test_descriptive_only_contract_never_falls_back_to_quantitative_file(tmp_path: Path) -> None:
    _legacy(tmp_path, dict(L1_MINIMUMS))
    result = WorkspaceQualityRunner(tmp_path).delivery_depth_contract_result(
        {
            "delivery_depth_contract": {
                "schema_version": "polaris.delivery_depth_contract.v1",
                "product_intent": {"subject": "private example"},
            }
        }
    )
    assert result is None


def test_no_formal_contract_preserves_ordinary_and_legacy_only_behavior(tmp_path: Path) -> None:
    runner = WorkspaceQualityRunner(tmp_path)
    assert runner.delivery_depth_contract_result({}) is None
    _legacy(tmp_path, dict(L1_MINIMUMS))
    result = runner.delivery_depth_contract_result({})
    assert result is not None and result["passed"] is False
    assert result["delivery_depth_contract"]["minimums"] == L1_MINIMUMS


@pytest.mark.parametrize(
    "field,wrong",
    [("workspace", "/private/foreign"), ("factory_run_id", "factory-old"), ("project_id", "foreign-project")],
)
def test_explicit_contract_identity_cannot_cross_current_owner(tmp_path: Path, field: str, wrong: str) -> None:
    current = _contract(**{field: wrong})
    result = WorkspaceQualityRunner(tmp_path).delivery_depth_contract_result(
        {"factory_run_id": "factory-current", "project_id": "private-project", "delivery_depth_contract": current}
    )
    assert result is not None and result["passed"] is False
    assert result["error_code"] == "delivery_depth_contract_invalid"
    assert "identity" in result["stderr_tail"]


def test_platform_project_identity_is_not_replaced_by_legacy_bench_observation(tmp_path: Path) -> None:
    context = {
        "metadata": {"project_id": "private-project", "factory_bench_project_id": "foreign-observation"},
        "delivery_depth_contract": _contract(project_id="private-project"),
    }
    result = WorkspaceQualityRunner(tmp_path).delivery_depth_contract_result(context)
    assert result is not None and result["passed"] is False
    assert result["error"].startswith("delivery_depth_contract_failed:")
    assert result["delivery_depth_contract"]["minimums"] == L1_MINIMUMS


def test_matching_identity_and_nested_level_projection_preserve_declared_minimums(tmp_path: Path) -> None:
    current = _contract(
        workspace=str(tmp_path),
        factory_run_id="factory-current",
        project_id="private-project",
        level_contract={"level": 1, "project_id": "private-project", "minimums": dict(L1_MINIMUMS)},
    )
    context = {"factory_run_id": "factory-current", "project_id": "private-project", "delivery_depth_contract": current}
    before = copy.deepcopy(context)
    result = WorkspaceQualityRunner(tmp_path).delivery_depth_contract_result(context)
    assert result is not None and result["passed"] is False
    assert result["delivery_depth_contract"]["minimums"] == L1_MINIMUMS
    result["delivery_depth_contract"]["minimums"]["min_prod_lines"] = 0
    assert context == before


@pytest.mark.parametrize("restored", [False, True])
def test_formal_http_input_persistence_and_stage_restore_reach_real_depth_checker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restored: bool
) -> None:
    from polaris.delivery.http.routers import factory as router
    from polaris.delivery.http.routers._factory_handlers import mapping
    from polaris.delivery.http.routers._factory_handlers.stage_ops.stage_context import _build_stage_context

    store = FactoryStore(tmp_path / ".polaris" / "runtime" / "factory")
    run = FactoryRun(
        id="factory-private-depth",
        config=FactoryConfig(name="private"),
        status=FactoryRunStatus.PENDING,
        created_at="2026-10-18T00:00:00Z",
    )

    class AdmissionOnlyService:
        def __init__(self) -> None:
            self.store = store

        async def create_run(self, config: FactoryConfig) -> FactoryRun:
            run.config = config
            return run

        async def start_run(self, run_id: str) -> FactoryRun:
            assert run_id == run.id
            run.status = FactoryRunStatus.RUNNING
            return run

    service = AdmissionOnlyService()
    settings = SimpleNamespace(workspace=tmp_path, qa_enabled=True)
    state = SimpleNamespace(settings=settings)
    scheduled: list[dict[str, Any]] = []
    monkeypatch.setattr(router, "_get_service", lambda _workspace: service)
    monkeypatch.setattr(router, "_ensure_factory_runtime_ready", lambda _state, _stages: None)
    monkeypatch.setattr(router, "workspace_binding_conflict", lambda _workspace: None)
    monkeypatch.setattr(
        router,
        "_schedule_factory_run_task",
        lambda _service, _run_id, request, _state: scheduled.append(request.model_dump(mode="json")),
    )
    app = FastAPI()
    app.post("/v2/factory/runs")(router.start_factory_run_v2)
    app.dependency_overrides[router.get_state] = lambda: state
    current = _contract(project_id="private-project")
    with TestClient(app) as client:
        response = client.post(
            "/v2/factory/runs",
            json={
                "workspace": str(tmp_path),
                "start_from": "pm",
                "directive": "Private unchanged original acceptance",
                "persist_workspace": False,
                "metadata": {
                    "project_id": "private-project",
                    "delivery_depth_contract": current,
                    "qa_semantic_review_required": True,
                    "qa_llm_required": True,
                },
            },
        )
    assert response.status_code == 200, response.text
    assert len(scheduled) == 1
    persisted = asyncio.run(store.get_run(run.id))
    assert persisted is not None
    assert persisted.metadata["factory_start_request"]["metadata"]["delivery_depth_contract"] == current
    request = (
        mapping._build_retry_start_request(persisted, str(tmp_path))
        if restored
        else router.FactoryStartRequest.model_validate(scheduled[0])
    )
    context = _build_stage_context("quality_gate", request, state, run_id=run.id)
    assert context["factory_run_id"] == persisted.id == run.id
    result = WorkspaceQualityRunner(tmp_path).delivery_depth_contract_result(context)
    assert result is not None, "formal HTTP contract disappeared at real quality consumer"
    assert result["passed"] is False
    assert result["delivery_depth_contract"]["minimums"] == L1_MINIMUMS
    assert request.metadata["qa_semantic_review_required"] is True
    assert request.metadata["qa_llm_required"] is True
    assert not (tmp_path / ".polaris" / "catalog_contract.json").exists()
