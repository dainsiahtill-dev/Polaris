"""Foreign repair effects require a fresh original-owner claim and fresh plan."""

from __future__ import annotations

import asyncio
import threading
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint import public as ce
from polaris.cells.factory.pipeline.internal import (
    factory_materialization_impl as stage,
    factory_workspace_quality_impl as quality,
)
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.run_ledger import build_job_token_from_record
from polaris.cells.roles.adapters import public as adapters
from polaris.cells.runtime.task_runtime import public as runtime


def _candidate(path: str, *, epoch: str = "old") -> dict[str, Any]:
    return {
        "result": {
            "deferred_request": SimpleNamespace(
                plan=SimpleNamespace(effects=(SimpleNamespace(contingency_kind="forward", target_path=path),)),
                allowed_paths=(path,),
            ),
            "plan_hash": epoch,
        }
    }


@pytest.fixture
def continuation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    paths_by_owner = {"TASK-1": ["moon.ts", "humidity.ts"], "TASK-2": ["browser-app.ts"]}
    owners: dict[str, dict[str, Any]] = {}
    tokens: dict[str, dict[str, Any]] = {}
    for task_id, paths in paths_by_owner.items():
        token = build_job_token_from_record(
            {
                "target_files": paths,
                "allowed_paths": paths,
                "allowed_write_paths": paths,
                "allowed_read_paths": paths,
                "blueprint_id": "ce-" + task_id,
                "contract_goal": "repair original owned artifacts",
            },
            run_id="factory-1",
            project_id="project-1",
            stage="director_dispatch",
        ).to_dict()
        token["allowed_write_paths"] = list(paths)
        token["allowed_read_paths"] = list(paths)
        tokens[task_id] = token
        owners[task_id] = {
            "id": task_id,
            "task_id": task_id,
            "target_files": paths,
            "metadata": {
                "factory_run_id": "factory-1",
                "task_completion_projection": {
                    "task_id": task_id,
                    "run_id": "factory-1",
                    "owned_artifacts": [
                        {"path": path, "owner_task_id": task_id, "obligation_id": path} for path in paths
                    ],
                },
            },
        }
        for path in paths:
            (tmp_path / path).write_text("unfixed\n", encoding="utf-8")

    def handoff(_workspace: str, owner: dict[str, Any], *, require_strict: bool) -> dict[str, Any]:
        assert require_strict
        task_id = owner["task_id"]
        return {
            "allowed": True,
            "job_token": deepcopy(tokens[task_id]),
            "capability_token": deepcopy(tokens[task_id]),
            "task_completion_projection": deepcopy(owners[task_id]["metadata"]["task_completion_projection"]),
        }

    monkeypatch.setattr(ce, "validate_director_handoff_from_payload", handoff)
    order: list[tuple[str, str]] = []
    attempts: dict[str, SimpleNamespace] = {}
    original = deepcopy(owners)
    stale_foreign = _candidate("browser-app.ts")
    executor = SimpleNamespace(workspace=tmp_path)
    executor._director_stage_should_run_materialization_quality_settle = lambda **_k: True
    executor._workspace_quality_repair_diagnostic_target_files = lambda _d: ["moon.ts"]
    executor._workspace_quality_repair_target_files = lambda: ["moon.ts"]
    executor._workspace_quality_repair_blueprint_evidence = lambda **_k: ("", "")
    executor._load_pm_plan_tasks = lambda _path: list(owners.values())
    executor._task_id = lambda task, _index: task["id"]
    # The external CE boundary above is controlled; canonical lookup still
    # consumes its real strict projection, never candidate-proposed grants.
    monkeypatch.setattr(
        quality, "_workspace_quality_frozen_ce_owner_task", lambda _e, **k: deepcopy(k["canonical_task"])
    )

    def scan() -> list[str]:
        return [
            f"{path}: repair needed"
            for paths in paths_by_owner.values()
            for path in paths
            if (tmp_path / path).read_text(encoding="utf-8") != "fixed\n"
        ]

    executor._collect_director_stage_materialization_diagnostics = scan

    def claim(**kwargs: Any) -> tuple:
        task_id = "TASK-2" if "browser-app.ts" in kwargs["target_files"] else "TASK-1"
        order.append(("claim", task_id))
        attempt = SimpleNamespace(workspace=str(tmp_path), task_id=int(task_id[-1]), external_task_id=task_id)
        attempts[task_id] = attempt
        return task_id, attempt.task_id, attempt, deepcopy(owners[task_id])

    executor._claim_workspace_quality_repair_attempt = claim
    monkeypatch.setattr(quality, "_claim_workspace_quality_repair_attempt", lambda _e, **k: claim(**k))

    def plan(**kwargs: Any) -> tuple:
        task_id = kwargs["task_id"]
        order.append(("plan", task_id))
        assert kwargs["repair_task"] == original[task_id]
        if task_id == "TASK-1":
            return [_candidate("moon.ts"), stale_foreign, _candidate("humidity.ts")], {}
        assert (tmp_path / "moon.ts").read_text(encoding="utf-8") == "fixed\n"
        assert (tmp_path / "humidity.ts").read_text(encoding="utf-8") == "fixed\n"
        assert kwargs["artifact_quality_errors"] == ["browser-app.ts: repair needed"]
        return [_candidate("browser-app.ts", epoch="fresh")], {}

    executor._apply_workspace_quality_repairs = plan
    executor._director_stage_materialization_settle_commit_context = lambda **k: (
        stage._director_stage_materialization_settle_commit_context(executor, **k)
    )
    executor._director_stage_materialization_receipt_succeeded = lambda r: r.get("success") is True
    executor._workspace_quality_repair_result_has_mutation = lambda _r: False
    executor._ensure_director_stage_materialization_typescript_toolchain = lambda: None

    def close(**kwargs: Any) -> dict:
        task_id = kwargs["execution_attempt"].external_task_id
        order.append(("close:" + kwargs["stage_status"], task_id))
        return {"success": True}

    executor._settle_director_stage_materialization_attempt = close

    async def commit(**kwargs: Any) -> list:
        task_id = kwargs["execution_attempt"].external_task_id
        candidate = kwargs["tool_results"][0]
        path = stage._deferred_repair_forward_target_paths([candidate])[0]
        assert kwargs["context"]["job_token"] == tokens[task_id]
        assert kwargs["context"]["task_completion_projection"]["task_id"] == task_id
        if task_id == "TASK-2":
            assert candidate is not stale_foreign and candidate["result"]["plan_hash"] == "fresh"
        order.append(("effect", path))
        (tmp_path / path).write_text("fixed\n", encoding="utf-8")
        return [{"success": True, "result": {"file": path}}]

    monkeypatch.setattr(adapters, "commit_materialization_deferred_repairs", commit)

    def record(pending: dict) -> tuple:
        task_id = pending["task_id"]
        assert pending["execution_attempt"] is attempts[task_id]
        order.append(("assets", task_id))
        return tuple({"path": path} for path in paths_by_owner[task_id])

    monkeypatch.setattr(quality, "_record_workspace_quality_repair_artifact_receipts", record)
    monkeypatch.setattr(quality, "_workspace_quality_causal_repair_target_files", lambda *_a, **_k: ["moon.ts"])
    monkeypatch.setattr(
        runtime,
        "create_task_runtime_execution_attempt_authority",
        lambda _a: SimpleNamespace(heartbeat=lambda **_k: SimpleNamespace(success=True)),
    )
    return SimpleNamespace(
        executor=executor,
        order=order,
        owners=owners,
        original=original,
        tokens=tokens,
        commit=commit,
        record=record,
        run=SimpleNamespace(id="factory-1", config=SimpleNamespace(name="project-1")),
    )


@pytest.mark.asyncio
async def test_owned_foreign_owned_finishes_owned_effects_then_reclaims_and_replans(
    continuation: SimpleNamespace,
) -> None:
    result = await stage._run_director_stage_materialization_quality_settle(
        continuation.executor, run=continuation.run, stage_status="failed", error_code="director.dispatch_failed"
    )
    assert result["ok"] is True, result
    assert continuation.order[:6] == [
        ("claim", "TASK-1"),
        ("plan", "TASK-1"),
        ("effect", "moon.ts"),
        ("assets", "TASK-1"),
        ("effect", "humidity.ts"),
        ("assets", "TASK-1"),
    ]
    assert continuation.order.index(("close:failed", "TASK-1")) < continuation.order.index(("claim", "TASK-2"))
    assert ("plan", "TASK-2") in continuation.order
    assert result["committed_receipt_count"] == 3
    assert result["project_artifact_receipt_count"] == 3
    assert continuation.owners == continuation.original
    assert continuation.order.count(("claim", "TASK-1")) == 2
    assert continuation.order[-2:] == [("assets", "TASK-1"), ("close:success", "TASK-1")]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unknown", "ambiguous", "unsafe"])
async def test_unresolvable_candidate_preserves_prior_effect_and_assets(
    continuation: SimpleNamespace, failure: str
) -> None:
    if failure == "ambiguous":
        duplicate = deepcopy(continuation.owners["TASK-2"])
        duplicate["id"] = duplicate["task_id"] = "TASK-3"
        projection = duplicate["metadata"]["task_completion_projection"]
        projection["task_id"] = "TASK-3"
        projection["owned_artifacts"][0]["owner_task_id"] = "TASK-3"
        continuation.owners["TASK-3"] = duplicate
        continuation.tokens["TASK-3"] = deepcopy(continuation.tokens["TASK-2"])
    path = {"unknown": "unowned.ts", "ambiguous": "browser-app.ts", "unsafe": "../escape.ts"}[failure]
    continuation.executor._apply_workspace_quality_repairs = lambda **_k: (
        [_candidate("moon.ts"), _candidate(path), _candidate("humidity.ts")],
        {},
    )
    result = await stage._run_director_stage_materialization_quality_settle(
        continuation.executor, run=continuation.run, stage_status="failed", error_code="director.dispatch_failed"
    )
    assert result["ok"] is False
    assert result["committed_receipt_count"] == 1
    assert result["project_artifact_receipt_count"] == 2
    assert ("effect", "humidity.ts") not in continuation.order
    assert continuation.order[-1] == ("close:failed", "TASK-1")
    assert {
        "unknown": "repair_candidate_owner_unknown",
        "ambiguous": "repair_candidate_owner_ambiguous",
        "unsafe": "repair_candidate_path_invalid",
    }[failure] in result["detail"]


@pytest.mark.asyncio
async def test_rejected_foreign_owner_claim_keeps_owned_progress(
    continuation: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def rejected(_executor: object, **_kwargs: Any) -> tuple:
        raise RuntimeError("workspace_quality_repair_claim_failed:dependencies_not_completed")

    monkeypatch.setattr(quality, "_claim_workspace_quality_repair_attempt", rejected)
    result = await stage._run_director_stage_materialization_quality_settle(
        continuation.executor, run=continuation.run, stage_status="failed", error_code="director.dispatch_failed"
    )
    assert result["ok"] is False and "dependencies_not_completed" in result["detail"]
    assert result["committed_receipt_count"] == 2
    assert result["project_artifact_receipt_count"] == 2
    assert continuation.order[-1] == ("close:failed", "TASK-1")
    assert ("effect", "browser-app.ts") not in continuation.order


@pytest.mark.asyncio
@pytest.mark.parametrize("reject_at", ["foreign_registration", "prior_owner_revalidation", "prior_owner_close"])
async def test_clean_workspace_cannot_erase_required_owner_rejection(
    continuation: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, reject_at: str
) -> None:
    def record(pending: dict) -> tuple:
        if (reject_at == "foreign_registration" and pending["task_id"] == "TASK-2") or (
            reject_at == "prior_owner_revalidation" and continuation.order.count(("claim", "TASK-1")) == 2
        ):
            raise RuntimeError("artifact_owner_revalidation_rejected")
        return continuation.record(pending)

    monkeypatch.setattr(quality, "_record_workspace_quality_repair_artifact_receipts", record)
    close = continuation.executor._settle_director_stage_materialization_attempt

    def rejected_close(**kwargs: Any) -> dict:
        if (
            reject_at == "prior_owner_close"
            and kwargs["stage_status"] == "success"
            and kwargs["execution_attempt"].external_task_id == "TASK-1"
        ):
            return {"success": False, "reason": "task_boundary_rejected"}
        return close(**kwargs)

    continuation.executor._settle_director_stage_materialization_attempt = rejected_close
    result = await stage._run_director_stage_materialization_quality_settle(
        continuation.executor, run=continuation.run, stage_status="failed", error_code="director.dispatch_failed"
    )
    assert result["ok"] is False
    assert result["committed_receipt_count"] == 3
    assert result["project_artifact_receipt_count"] == (2 if reject_at == "foreign_registration" else 3)


@pytest.mark.asyncio
async def test_failed_effect_receipt_remains_red_after_later_clean_verifier(
    continuation: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def commit(**kwargs: Any) -> list:
        receipts = await continuation.commit(**kwargs)
        if kwargs["execution_attempt"].external_task_id == "TASK-2":
            receipts.append({"success": False, "error": "physical_effect_rejected"})
        return receipts

    monkeypatch.setattr(adapters, "commit_materialization_deferred_repairs", commit)
    result = await stage._run_director_stage_materialization_quality_settle(
        continuation.executor, run=continuation.run, stage_status="failed", error_code="director.dispatch_failed"
    )
    assert result["ok"] is False
    assert result["committed_receipt_count"] == 3 and result["failed_receipt_count"] == 1
    assert result["project_artifact_receipt_count"] == 3
    assert ("close:success", "TASK-2") not in continuation.order


@pytest.mark.asyncio
async def test_owner_signature_cycle_stops_without_moving_plans(continuation: SimpleNamespace) -> None:
    continuation.executor._apply_workspace_quality_repairs = lambda **k: (
        [_candidate("browser-app.ts" if k["task_id"] == "TASK-1" else "moon.ts")],
        {},
    )
    result = await stage._run_director_stage_materialization_quality_settle(
        continuation.executor, run=continuation.run, stage_status="failed", error_code="director.dispatch_failed"
    )
    assert result["ok"] is False
    assert result["repair_round_count"] == 2
    assert result["committed_receipt_count"] == 0
    assert [item for item in continuation.order if item[0] == "claim"] == [("claim", "TASK-1"), ("claim", "TASK-2")]


@pytest.mark.asyncio
async def test_foreign_owner_heartbeat_rejection_keeps_prior_receipts(
    continuation: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = continuation.executor._apply_workspace_quality_repairs

    def slow_foreign_plan(**kwargs: Any) -> tuple:
        if kwargs["task_id"] == "TASK-2":
            time.sleep(0.03)
        return plan(**kwargs)

    continuation.executor._apply_workspace_quality_repairs = slow_foreign_plan
    monkeypatch.setattr(quality, "_WORKSPACE_QUALITY_REPAIR_HEARTBEAT_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(
        runtime,
        "create_task_runtime_execution_attempt_authority",
        lambda attempt: SimpleNamespace(
            heartbeat=lambda **_k: SimpleNamespace(success=attempt.external_task_id != "TASK-2", code="lease_lost")
        ),
    )
    result = await stage._run_director_stage_materialization_quality_settle(
        continuation.executor, run=continuation.run, stage_status="failed", error_code="director.dispatch_failed"
    )
    assert result["ok"] is False and "repair_owner_heartbeat_failed:lease_lost" in result["detail"]
    assert result["committed_receipt_count"] == 2 and result["project_artifact_receipt_count"] == 2
    assert ("effect", "browser-app.ts") not in continuation.order
    assert continuation.order[-1] == ("close:failed", "TASK-2")


@pytest.mark.asyncio
async def test_cancellation_stops_owner_renewal_without_terminal_receipt(
    continuation: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    planner_started = threading.Event()
    planner_release = threading.Event()
    renewal_stopped = asyncio.Event()

    def blocked_plan(**_kwargs: Any) -> tuple:
        planner_started.set()
        assert planner_release.wait(5)
        return [], {}

    async def heartbeat(_authority: object, *, stop: asyncio.Event, **_kwargs: Any) -> None:
        await stop.wait()
        renewal_stopped.set()

    continuation.executor._apply_workspace_quality_repairs = blocked_plan
    monkeypatch.setattr(quality, "_run_workspace_quality_repair_heartbeat", heartbeat)
    pending = asyncio.create_task(
        stage._run_director_stage_materialization_quality_settle(
            continuation.executor, run=continuation.run, stage_status="failed", error_code="director.dispatch_failed"
        )
    )
    try:
        assert await asyncio.to_thread(planner_started.wait, 3)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert renewal_stopped.is_set()
        assert not any(item[0].startswith("close:") or item[0] == "effect" for item in continuation.order)
    finally:
        planner_release.set()


@pytest.mark.parametrize("dependency_blocked", [False, True])
def test_exact_original_owner_claim_uses_real_taskruntime_dependency_authority(
    tmp_path: Path, dependency_blocked: bool
) -> None:
    from polaris.cells.events.fact_stream.public import (
        BootstrapFactStreamWorkspaceCommandV1,
        bootstrap_fact_stream_workspace,
        fact_stream_bootstrap_streams,
    )
    from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
    from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService

    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path), streams=fact_stream_bootstrap_streams(), maintenance_reason="original-owner-test"
        )
    )
    service = TaskRuntimeService(str(tmp_path))
    executor = OrchestrationStageExecutor(tmp_path)
    rows: list[dict[str, Any]] = []
    for task_id in ("TASK-1", "TASK-2"):
        rows.append(
            service.create_task_row(
                subject="original CE owner " + task_id,
                blocked_by=[int(rows[0]["id"])] if task_id == "TASK-2" and dependency_blocked else [],
                metadata={
                    "external_task_id": task_id,
                    "factory_run_id": "factory-1",
                    "target_files": ["shared.ts"],
                    "control_plane_job_token": {"run_id": "factory-1", "allowed_write_paths": ["shared.ts"]},
                    "task_completion_projection": {
                        "task_id": task_id,
                        "run_id": "factory-1",
                        "owned_artifacts": [{"path": "shared.ts", "owner_task_id": task_id, "obligation_id": task_id}],
                    },
                },
            )
        )
    run = FactoryRun(
        id="factory-1",
        config=FactoryConfig(name="project-1"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-03T00:00:00+00:00",
    )
    if dependency_blocked:
        with pytest.raises(RuntimeError, match="workspace_quality_repair_claim_failed"):
            quality._claim_workspace_quality_repair_attempt(
                executor, run=run, repair_attempt=2, target_files=["shared.ts"], original_owner_task_id="TASK-2"
            )
        current = next(
            row
            for row in TaskRuntimeService(str(tmp_path)).list_task_rows(include_terminal=True)
            if row["id"] == rows[1]["id"]
        )
        assert current["blocked_by"] == [int(rows[0]["id"])]
        assert current["status"] == "blocked"
        return
    owner_id, row_id, attempt, owner = quality._claim_workspace_quality_repair_attempt(
        executor, run=run, repair_attempt=2, target_files=["shared.ts"], original_owner_task_id="TASK-2"
    )
    try:
        assert owner_id == "TASK-2" and row_id == int(rows[1]["id"])
        assert attempt.external_task_id == "TASK-2" and attempt.run_id == "factory-1"
        assert owner["metadata"]["control_plane_job_token"] == {
            "run_id": "factory-1",
            "allowed_write_paths": ["shared.ts"],
        }
        assert owner["metadata"]["task_completion_projection"]["task_id"] == "TASK-2"
        first = next(
            row
            for row in TaskRuntimeService(str(tmp_path)).list_task_rows(include_terminal=True)
            if row["id"] == rows[0]["id"]
        )
        assert first["status"] in {"ready", "pending"}
    finally:
        executor._settle_director_stage_materialization_attempt(
            task_row_id=row_id, execution_attempt=attempt, stage_status="failed", summary="test cleanup"
        )


def test_real_ce_projection_routes_real_typed_plan_without_grant_or_hash_transplant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.chief_engineer.blueprint.public import (
        BuildChiefEngineerBlueprintPortfolioCommandV1,
        ChiefEngineerPortfolioTaskV1,
        GenerateTaskBlueprintCommandV1,
        build_chief_engineer_blueprint_portfolio,
        generate_task_blueprint,
        project_chief_engineer_task_blueprint,
    )
    from polaris.cells.chief_engineer.blueprint.public.tests.test_public_contractsa import (
        _library_completion_requirements,
        _portfolio_command_authority,
    )
    from polaris.cells.director.runtime.public import PlanDirectorRepairCommandV1, plan_director_repair
    from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor

    tasks = (
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-1", objective="Implement owned model", target_files=("src/model.ts",)
        ),
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-2", objective="Implement browser and tests", target_files=("src/web.ts", "tests/test_web.py")
        ),
    )
    portfolio = build_chief_engineer_blueprint_portfolio(
        BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id="factory-1",
            tasks=tasks,
            **_portfolio_command_authority(tasks=tasks, project_kind="library", workspace=tmp_path, run_id="factory-1"),
            llm_blueprint={
                "construction_plan": {"project_interface_contract": {}},
                "project_completion_contract": _library_completion_requirements(
                    "src/model.ts",
                    "src/web.ts",
                    owner_task_ids=("TASK-1", "TASK-2"),
                    test_path="tests/test_web.py",
                    test_owner_task_id="TASK-2",
                ),
                "risk_flags": [],
            },
        )
    )
    owners: dict[str, dict[str, Any]] = {}
    review: dict[str, Any] = {"blueprints": []}
    for task in tasks:
        context = {
            "task_title": task.objective,
            "target_files": list(task.target_files),
            "scope_paths": list(task.target_files),
            "acceptance_criteria": ["Original owned module is usable"],
            "execution_checklist": ["Implement declared files"],
            "task": task.to_dict(),
            **portfolio.to_task_blueprint_context(),
        }
        result = generate_task_blueprint(
            GenerateTaskBlueprintCommandV1(
                task_id=task.task_id,
                workspace=str(tmp_path),
                objective=task.objective,
                run_id="factory-1",
                context=context,
                llm_blueprint=project_chief_engineer_task_blueprint(portfolio, task.task_id),
            )
        )
        assert result.blueprint_id and result.blueprint_path
        owner = {
            "id": task.task_id,
            "task_id": task.task_id,
            "subject": task.objective,
            "target_files": list(task.target_files),
            "blueprint_id": result.blueprint_id,
            "metadata": {"factory_run_id": "factory-1"},
        }
        owners[task.task_id] = owner
        review["blueprints"].append(
            {
                "task_id": task.task_id,
                "blueprint_id": result.blueprint_id,
                "blueprint_path": result.blueprint_path,
                "status": "generated",
                "handoff_ready": True,
            }
        )
    executor = OrchestrationStageExecutor(tmp_path)
    monkeypatch.setattr(executor, "_load_pm_plan_tasks", lambda _path: list(owners.values()))
    monkeypatch.setattr(executor, "_load_chief_engineer_review_payload", lambda **_k: review)
    run = FactoryRun(
        id="factory-1",
        config=FactoryConfig(name="project-1"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-03T00:00:00+00:00",
    )
    context_before = executor._director_stage_materialization_settle_commit_context(
        run=run, run_id="factory-1", diagnostics=[], repair_task=owners["TASK-1"]
    )
    base_files = {"src/web.ts": "export function f(state: number): number {\n  return newState;\n}\n"}
    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_unresolved_identifier_repair",
            base_files=base_files,
            artifact_quality_errors=("src/web.ts(2,10): error TS2304: Cannot find name 'newState'.",),
            mode="shadow",
        )
    )
    assert planning.ok is True and planning.planned is True
    plan = planning.effect_plan
    assert plan is not None
    candidate = {"result": {"deferred_request": SimpleNamespace(plan=plan), "plan_hash": plan.plan_hash}}
    candidate_before = deepcopy(candidate)
    assert stage._director_stage_deferred_repair_owner_targets(
        executor,
        run=run,
        run_id="factory-1",
        repair_task=owners["TASK-1"],
        candidate=candidate,
        owner_context=context_before,
    ) == {"TASK-2": ["src/web.ts"]}
    assert candidate == candidate_before
    assert (
        executor._director_stage_materialization_settle_commit_context(
            run=run, run_id="factory-1", diagnostics=[], repair_task=owners["TASK-1"]
        )["job_token"]
        == context_before["job_token"]
    )
    with pytest.raises(ValueError, match="repair_candidate_outside_owner_scope"):
        executor._director_stage_materialization_settle_commit_context(
            run=run, run_id="factory-1", diagnostics=[], repair_task=owners["TASK-1"], deferred_tool_results=[candidate]
        )
