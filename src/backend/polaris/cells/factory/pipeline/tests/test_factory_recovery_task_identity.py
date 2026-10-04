"""Factory retry keeps the CE-bound prerequisite identity instead of cloning it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerPortfolioTaskV1,
    GenerateTaskBlueprintCommandV1,
    ProjectCompletionContractV1,
    build_chief_engineer_blueprint_portfolio,
    generate_task_blueprint,
    project_chief_engineer_task_blueprint,
    validate_director_handoff_from_payload,
)
from polaris.cells.chief_engineer.blueprint.public.tests.test_public_contractsa import (
    _library_completion_requirements,
    _portfolio_command_authority,
)
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.factory_stage_executor._executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.internal.factory_workspace_quality_impl import (
    _claim_workspace_quality_repair_attempt,
)
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import authored as authored
from polaris.cells.runtime.task_runtime.public.contracts import BindRuntimeTaskToFactoryRunCommandV1
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.cells.runtime.task_runtime.tests.test_service_sub2 import (
    _create_bootstrapped_task_runtime_service,
    _settle_claimed_execution_attempt,
)


def _failed_ce_owner(tmp_path: Path) -> tuple[TaskRuntimeService, dict[str, Any], dict[str, Any]]:
    service = _create_bootstrapped_task_runtime_service(tmp_path)
    task = ChiefEngineerPortfolioTaskV1(
        task_id="WORK-A",
        objective="Implement the generic library",
        target_files=("src/shared.py", "src/a.py", "tests/test_a.py"),
    )
    portfolio = build_chief_engineer_blueprint_portfolio(
        BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id="factory-one",
            tasks=(task,),
            **_portfolio_command_authority(
                tasks=(task,), project_kind="library", workspace=tmp_path, run_id="factory-one"
            ),
            llm_blueprint={
                "construction_plan": {"project_interface_contract": {}},
                "project_completion_contract": _library_completion_requirements(
                    "src/shared.py",
                    "src/a.py",
                    owner_task_ids=("WORK-A", "WORK-A"),
                    test_path="tests/test_a.py",
                    test_owner_task_id="WORK-A",
                ),
                "risk_flags": [],
            },
        )
    )
    result = generate_task_blueprint(
        GenerateTaskBlueprintCommandV1(
            task_id=task.task_id,
            workspace=str(tmp_path),
            objective=task.objective,
            run_id="factory-one",
            context={
                "task_title": task.objective,
                "target_files": list(task.target_files),
                "scope_paths": list(task.target_files),
                "acceptance_criteria": ["Library behavior is verified"],
                "execution_checklist": ["Implement and test the library"],
                "delivery_plan_document": {
                    "schema_version": "polaris.delivery_plan_document.v1",
                    "title": "Library",
                    "user_journey": ["Import library", "Run tests"],
                },
                "delivery_depth_contract": {
                    "schema_version": "polaris.delivery_depth_contract.v1",
                    "behavior_contract": {"rule_matrix": ["Library exports deterministic behavior"]},
                },
                "task": task.to_dict(),
                **portfolio.to_task_blueprint_context(),
            },
            llm_blueprint=project_chief_engineer_task_blueprint(portfolio, task.task_id),
        )
    )
    handoff = validate_director_handoff_from_payload(
        str(tmp_path), {"task_id": task.task_id, "blueprint_id": result.blueprint_id}, require_strict=True
    )
    assert handoff["allowed"] is True, handoff.get("reason")
    plan_task = {"id": task.task_id, "subject": task.objective, "target_files": list(task.target_files)}
    row = service.ensure_task_row(
        external_task_id=task.task_id,
        subject=task.objective,
        metadata={
            "factory_run_id": "factory-one",
            "external_task_id": task.task_id,
            "blueprint_id": result.blueprint_id,
            "task_completion_projection": handoff["task_completion_projection"],
        },
    )
    assert service.bind_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(tmp_path), task_id=str(row["id"]), factory_run_id="factory-one"
        )
    ).ok
    claim = service.claim_execution(
        row["id"], worker_id="director", role_id="director", run_id="director-before", external_task_id=task.task_id
    )
    assert claim["success"]
    assert _settle_claimed_execution_attempt(service, claim, outcome="failed", summary="verifier failure")["success"]
    return service, row, plan_task


def test_real_factory_retry_reopens_exact_strict_ce_owner(tmp_path: Path) -> None:
    service, old, plan_task = _failed_ce_owner(tmp_path)
    result = OrchestrationStageExecutor(tmp_path)._materialize_pm_plan_taskboard(
        [plan_task],
        run_id="factory-one",
        source_stage="director_dispatch",
        run_metadata={"retry_from_status": "failed", "retry_execution_stage": "director_dispatch"},
    )
    assert result["binding_failures"] == []
    rows = service.list_observable_task_rows()
    assert len(rows) == 1, "sanctioned same-run recovery cloned its logical owner"
    assert rows[0]["id"] == old["id"]
    assert rows[0]["status"] == "pending"


def test_factory_retry_refuses_changed_ce_binding(tmp_path: Path) -> None:
    service, old, plan_task = _failed_ce_owner(tmp_path)
    projection = dict(old["metadata"]["task_completion_projection"])
    projection["project_contract_hash"] = "f" * 64
    service.update_task_row(old["id"], metadata={"task_completion_projection": projection})
    before = service.list_observable_task_rows()
    with pytest.raises(RuntimeError):
        OrchestrationStageExecutor(tmp_path)._materialize_pm_plan_taskboard(
            [plan_task],
            run_id="factory-one",
            source_stage="director_dispatch",
            run_metadata={"retry_from_status": "failed", "retry_execution_stage": "director_dispatch"},
        )
    assert service.list_observable_task_rows() == before


def test_quality_repair_refuses_competing_rows_for_one_logical_owner(tmp_path: Path) -> None:
    service, old, _plan_task = _failed_ce_owner(tmp_path)
    service.create_task_row(subject="Legacy competing row", metadata=dict(old["metadata"]))
    before = service.list_observable_task_rows()
    with pytest.raises(RuntimeError, match="factory_task_binding_ambiguous"):
        _claim_workspace_quality_repair_attempt(
            OrchestrationStageExecutor(tmp_path),
            run=FactoryRun(
                id="factory-one",
                config=FactoryConfig(name="generic"),
                status=FactoryRunStatus.FAILED,
                created_at="2026-10-04T00:00:00+00:00",
            ),
            repair_attempt=1,
            target_files=["src/a.py"],
        )
    assert service.list_observable_task_rows() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["explicit", "body_error", "legacy", "kwargs_only", "missing_owner", "non_node"])
async def test_qa_preparation_bridge_requires_explicit_contract_without_retry(
    tmp_path: Path, authored: tuple[Any, Any, Any], monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    from polaris.cells.factory.pipeline.internal.factory_node_dependencies import NODE_PREPARATION_COMMAND

    portfolio, contract, _ = authored
    executor = OrchestrationStageExecutor(tmp_path)
    run = FactoryRun(
        id=contract.run_id,
        config=FactoryConfig(name="preparation-bridge"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-04T00:00:00+00:00",
    )
    if mode != "missing_owner":
        run.metadata["stage_results"] = {
            "chief_engineer_review": {"status": "success", "artifacts": [portfolio.portfolio_path]}
        }
    monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda _run, _context: None)
    monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda _context: None)
    command = ["generic-preparation"] if mode == "non_node" else list(NODE_PREPARATION_COMMAND)
    monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda _commands, _context: [command])
    calls: list[object] = []

    def explicit(
        _command: list[str], _timeout: float, *, preparation_contract: ProjectCompletionContractV1
    ) -> dict[str, Any]:
        calls.append(preparation_contract)
        assert preparation_contract.contract_hash == contract.contract_hash
        if mode == "body_error":
            raise TypeError("fixture callback body error")
        return {"command": _command, "passed": False, "exit_code": 1, "error": "fixture stop after handoff"}

    def legacy(_command: list[str], _timeout: float) -> dict[str, Any]:
        calls.append(None)
        return {"command": _command, "passed": False, "exit_code": 1, "error": "fixture legacy stop"}

    def kwargs_only(_command: list[str], _timeout: float, **_kwargs: Any) -> dict[str, Any]:
        calls.append(None)
        return {"command": _command, "passed": False, "exit_code": 1}

    callback = legacy if mode in {"legacy", "non_node"} else kwargs_only if mode == "kwargs_only" else explicit
    monkeypatch.setattr(executor, "_run_workspace_quality_command", callback)
    context = {"project_id": contract.project_id, "quality_commands": [["/bin/true"]]}
    if mode == "body_error":
        with pytest.raises(TypeError, match="fixture callback body error"):
            await executor._run_workspace_quality_checks(run, context)
        assert len(calls) == 1
        return
    passed, artifact = await executor._run_workspace_quality_checks(run, context)
    assert passed is False
    expected_calls = 1 if mode in {"explicit", "non_node"} else 0
    assert len(calls) == expected_calls
    import json

    payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
    if expected_calls == 0:
        assert "preparation_authority_or_execution_invalid" in payload["commands"][0]["error"]
