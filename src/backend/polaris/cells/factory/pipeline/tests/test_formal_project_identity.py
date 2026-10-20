"""Formal admitted project identity survives real persistence and CE contracts.

Private PM computation/readiness/driver publication doubles; actual Factory
stage persistence, artifact proof, CE owner and token producers stay real.
No target programs, network provider, legacy catalog or product namespace.
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from polaris.cells.factory.pipeline.internal.factory_run_service import FactoryRunService, StageResult
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.tests.test_factory_role_evidence_provenance import _pm_document
from polaris.cells.orchestration.pm_planning.public.task_intent import normalize_pm_task_intent

PROJECT = "opaque-project.q7"
TASK = "opaque-unit.4f"


def _task() -> dict[str, Any]:
    return normalize_pm_task_intent(
        {
            "schema_version": "pm.task_intent.v1",
            "product_goal": "Deliver deterministic CLI output",
            "required_requirements": ["CLI accepts a name and prints a deterministic greeting"],
            "optional_requirements": [],
            "acceptance": ["CLI accepts a name and prints a deterministic greeting"],
            "explicit_constraints": ["Keep data inside the workspace"],
            "tasks": [
                {
                    "id": TASK,
                    "title": "Implement CLI",
                    "goal": "Deliver deterministic CLI output",
                    "acceptance": ["CLI accepts a name and prints a deterministic greeting"],
                    "depends_on": [],
                }
            ],
        }
    )[0]


async def _admitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, metadata: dict[str, Any]
) -> tuple[Any, OrchestrationStageExecutor, list[dict[str, Any]]]:
    from polaris.delivery.http.routers import factory as router

    tasks = [_task()]
    executor = OrchestrationStageExecutor(tmp_path)

    class SyntheticPM:
        async def execute(self, stage: str, run: Any, context: dict[str, Any]) -> StageResult:
            assert stage == "pm_planning"
            executor._write_json_artifact("tasks/plan.json", _pm_document(tasks))
            return StageResult(stage=stage, status="success", output="private PM fixture", artifacts=[])

    from polaris.cells.factory.pipeline.internal.factory_stage_artifact_bindings import build_pm_stage_artifact_bindings
    from polaris.kernelone.storage import resolve_storage_roots

    service: FactoryRunService = FactoryRunService(
        tmp_path,
        executor=SyntheticPM(),
        stage_artifact_binding_builder=lambda run_id, _result: build_pm_stage_artifact_bindings(
            factory_store=service.store,
            source_root=Path(resolve_storage_roots(str(tmp_path)).runtime_root),
            factory_run_id=run_id,
        ),
    )

    async def no_publish(_run_id: str, _event: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(service, "_publish_factory_event", no_publish)
    monkeypatch.setattr(router, "_get_service", lambda _workspace: service)
    monkeypatch.setattr(router, "_ensure_factory_runtime_ready", lambda _state, _stages: None)
    monkeypatch.setattr(router, "workspace_binding_conflict", lambda _workspace: None)
    monkeypatch.setattr(router, "_schedule_factory_run_task", lambda *_args: None)
    state = SimpleNamespace(settings=SimpleNamespace(workspace=tmp_path, qa_enabled=True))
    app = FastAPI()
    app.post("/v2/factory/runs")(router.start_factory_run_v2)
    app.dependency_overrides[router.get_state] = lambda: state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://private") as client:
        response = await client.post(
            "/v2/factory/runs",
            json={
                "workspace": str(tmp_path),
                "start_from": "pm",
                "directive": "Private exact original acceptance",
                "persist_workspace": False,
                "metadata": metadata,
            },
        )
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    result = await service.execute_stage(run_id, "pm_planning", {})
    assert result.status == "success", result.output
    cold = FactoryRunService(tmp_path)
    loaded = await cold.get_run(run_id)
    assert loaded is not None
    assert loaded.metadata["factory_start_request"]["metadata"] == metadata
    assert not (tmp_path / ".polaris" / "catalog_contract.json").exists()
    return loaded, OrchestrationStageExecutor(tmp_path), tasks


@pytest.mark.asyncio
async def test_http_persisted_project_reaches_real_ce_authority_without_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, executor, tasks = await _admitted(tmp_path, monkeypatch, metadata={"project_id": PROJECT})
    authority = await executor._load_chief_engineer_portfolio_authority(
        run=run, pm_tasks=tasks, portfolio_tasks=executor._chief_engineer_portfolio_tasks(tasks)
    )
    assert authority.project_id == PROJECT
    assert authority.pm_task_ids == (TASK,)
    from polaris.cells.chief_engineer.blueprint.public import (
        GenerateTaskBlueprintCommandV1,
        generate_task_blueprint,
        project_chief_engineer_task_blueprint,
    )
    from polaris.cells.chief_engineer.blueprint.public.service import BlueprintPersistence
    from polaris.cells.chief_engineer.blueprint.public.tests.test_task_intent_technical_handoff import _payload

    candidate = _payload()
    candidate["construction_plan"]["task_plans"][TASK] = candidate["construction_plan"]["task_plans"].pop("TASK-A")
    for rows in candidate["project_completion_contract"]["obligations"].values():
        for row in rows:
            row["owner_task_id"] = TASK
    portfolio = executor._build_chief_engineer_portfolio_from_candidate(
        run_id=run.id,
        tasks=executor._chief_engineer_portfolio_tasks(tasks),
        authority=authority,
        structured_output=candidate,
    )
    contract = portfolio.project_completion_contract
    assert contract is not None and contract.project_id == PROJECT and contract.run_id == run.id
    # A newer mutable context hint cannot rebind this frozen completion authority.
    result = generate_task_blueprint(
        GenerateTaskBlueprintCommandV1(
            task_id=TASK,
            workspace=str(tmp_path),
            run_id=run.id,
            objective=tasks[0]["goal"],
            context={
                **portfolio.to_task_blueprint_context(),
                "pm_task_contract": tasks[0],
                "project_id": "foreign-context",
            },
            llm_blueprint=project_chief_engineer_task_blueprint(portfolio, TASK),
        )
    )
    blueprint = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
    assert blueprint["job_token"]["project_id"] == PROJECT
    assert set(blueprint["job_token"]["allowed_write_paths"]) == {"src/main.py", "tests/test_main.py", "pyproject.toml"}
    assert len(blueprint["job_token"]["allowed_write_paths"]) == 3
    assert authority.project_id == PROJECT
    assert not (tmp_path / ".polaris" / "catalog_contract.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, "", " bad ", 42, [], "bad\nproject", "x" * 129])
async def test_explicit_invalid_project_does_not_fall_back_to_display_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: Any
) -> None:
    run, executor, tasks = await _admitted(tmp_path, monkeypatch, metadata={"project_id": invalid})
    with pytest.raises(RuntimeError, match=r"project_id_(invalid|missing)"):
        await executor._load_chief_engineer_portfolio_authority(
            run=run, pm_tasks=tasks, portfolio_tasks=executor._chief_engineer_portfolio_tasks(tasks)
        )


@pytest.mark.asyncio
async def test_conflicting_current_run_project_cannot_replace_admitted_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, executor, tasks = await _admitted(tmp_path, monkeypatch, metadata={"project_id": PROJECT})
    run.metadata["project_id"] = "foreign-current"
    with pytest.raises(RuntimeError, match=r"project_id_.*(conflict|mismatch)"):
        await executor._load_chief_engineer_portfolio_authority(
            run=run, pm_tasks=tasks, portfolio_tasks=executor._chief_engineer_portfolio_tasks(tasks)
        )


@pytest.mark.asyncio
async def test_admitted_project_is_bound_to_same_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run, executor, tasks = await _admitted(tmp_path, monkeypatch, metadata={"project_id": PROJECT})
    run.metadata["factory_start_request"]["workspace"] = str(tmp_path / "foreign")
    with pytest.raises(RuntimeError, match=r"project.*workspace.*mismatch"):
        await executor._load_chief_engineer_portfolio_authority(
            run=run, pm_tasks=tasks, portfolio_tasks=executor._chief_engineer_portfolio_tasks(tasks)
        )


@pytest.mark.asyncio
async def test_foreign_identity_bound_catalog_is_rejected_without_rebinding_formal_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from polaris.cells.factory.pipeline.internal.factory_stage_executor._helpers import (
        _ChiefEngineerPortfolioAuthorityError,
    )

    run, executor, tasks = await _admitted(tmp_path, monkeypatch, metadata={"project_id": PROJECT})
    catalog = tmp_path / ".polaris" / "catalog_contract.json"
    catalog.write_text(json.dumps({"project_id": "foreign-catalog"}), encoding="utf-8")
    original = executor._chief_engineer_project_kind_authority
    observed: list[str] = []

    def observe_real_kind_authority(**kwargs: Any) -> Any:
        observed.append(kwargs["project_id"])
        return original(**kwargs)

    monkeypatch.setattr(executor, "_chief_engineer_project_kind_authority", observe_real_kind_authority)
    with pytest.raises(_ChiefEngineerPortfolioAuthorityError, match="catalog snapshot project_id") as failure:
        await executor._load_chief_engineer_portfolio_authority(
            run=run, pm_tasks=tasks, portfolio_tasks=executor._chief_engineer_portfolio_tasks(tasks)
        )
    assert failure.value.code == "chief_engineer.project_completion_project_kind_authority_invalid"
    assert observed == [PROJECT]
    assert run.metadata["factory_start_request"]["metadata"]["project_id"] == PROJECT


@pytest.mark.asyncio
async def test_current_direct_owner_project_is_used_when_admitted_key_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, executor, tasks = await _admitted(tmp_path, monkeypatch, metadata={})
    run.metadata["project_id"] = PROJECT
    authority = await executor._load_chief_engineer_portfolio_authority(
        run=run, pm_tasks=tasks, portfolio_tasks=executor._chief_engineer_portfolio_tasks(tasks)
    )
    assert authority.project_id == PROJECT


@pytest.mark.asyncio
async def test_no_explicit_project_preserves_legacy_display_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, executor, tasks = await _admitted(tmp_path, monkeypatch, metadata={})
    authority = await executor._load_chief_engineer_portfolio_authority(
        run=run, pm_tasks=tasks, portfolio_tasks=executor._chief_engineer_portfolio_tasks(tasks)
    )
    assert authority.project_id == "Factory Run - pm"


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["foreign_run", "stale_pm"])
async def test_existing_pm_proof_cannot_be_borrowed_for_another_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    from dataclasses import replace

    run, executor, tasks = await _admitted(tmp_path, monkeypatch, metadata={"project_id": PROJECT})
    if defect == "foreign_run":
        run = replace(run, id="foreign-run")
        error = "current regular run snapshot"
    else:
        tasks = copy.deepcopy(tasks)
        tasks[0]["goal"] = "uncommitted changed goal"
        error = "pm_document_mismatch"
    with pytest.raises(RuntimeError, match=error):
        await executor._load_chief_engineer_portfolio_authority(
            run=run, pm_tasks=tasks, portfolio_tasks=executor._chief_engineer_portfolio_tasks(tasks)
        )
