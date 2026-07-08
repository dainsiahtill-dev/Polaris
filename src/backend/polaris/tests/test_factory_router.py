"""Tests for Factory router contract and runtime transport behavior."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from polaris.bootstrap.config import Settings
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryConfig,
    FactoryRunService,
    FactoryRunStatus,
    OrchestrationStageExecutor,
    StageResult,
)
from polaris.cells.factory.pipeline.public.types import FactoryStartRequest
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.delivery.http.app_factory import create_app
from polaris.delivery.http.routers import factory as factory_router_module
from polaris.kernelone.quality import (
    matching_owner_handoff_request,
    owner_handoff_identifier_tokens,
    task_record_identifier_tokens,
)
from polaris.kernelone.storage import resolve_logical_path


def _complete_task_row(
    task_runtime: TaskRuntimeService,
    task_id: Any,
    *,
    worker_id: str = "test",
    role_id: str = "director",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    claimed = task_runtime.claim_execution(
        task_id,
        worker_id=worker_id,
        role_id=role_id,
        selection_source="factory_router_test",
    )
    assert claimed["success"] is True
    completed = task_runtime.complete_execution(
        task_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="test completed",
        metadata=metadata,
    )
    assert completed["success"] is True
    return completed


def _fail_task_row(
    task_runtime: TaskRuntimeService,
    task_id: Any,
    *,
    worker_id: str = "test",
    role_id: str = "director",
    error: str = "test failed",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    claimed = task_runtime.claim_execution(
        task_id,
        worker_id=worker_id,
        role_id=role_id,
        selection_source="factory_router_test",
    )
    assert claimed["success"] is True
    failed = task_runtime.fail_execution(
        task_id,
        session_id=str(claimed["session"]["session_id"]),
        error=error,
        metadata=metadata,
    )
    assert failed["success"] is True
    return failed


class FakeStageExecutor:
    """Fast deterministic executor for router tests."""

    async def execute(self, stage, run, context):
        return StageResult(
            stage=stage,
            status="success",
            output=f"{stage} completed",
            artifacts=[f"artifacts/{stage}.json"],
        )


class QaLlmUnavailableStageExecutor(FakeStageExecutor):
    """Executor that simulates a deterministic QA pass with missing LLM judgement."""

    async def execute(self, stage, run, context):
        del run, context
        if stage == "quality_gate":
            return StageResult(
                stage=stage,
                status="failed",
                output=(
                    "Quality gate completed: Run status: completed; qa_passed=True; qa_score=92; "
                    "qa_critical=0; workspace_checks_passed=True; qa_llm_required=True; "
                    "qa_llm_judgement_ready=False; qa_gate_blocker=qa_llm_judgement_unavailable"
                ),
                artifacts=["runtime/qa/report.json"],
            )
        return StageResult(
            stage=stage,
            status="success",
            output=f"{stage} completed",
            artifacts=[f"artifacts/{stage}.json"],
        )


class QualityReworkStageExecutor(FakeStageExecutor):
    """Executor that simulates QA reopening a Director task for one rework round."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.director_calls = 0
        self.qa_calls = 0

    def _request_taskboard_rework(self) -> None:
        task_board = TaskRuntimeService(str(self.workspace))
        row = task_board.ensure_task_row(
            external_task_id="TASK-1",
            subject="Implement behavior with tests",
            metadata={"external_task_id": "TASK-1", "adapter_result": {"qa_required_for_final_verdict": True}},
            priority=1,
        )
        task_board.update_task_row(
            row["id"],
            metadata={
                "qa_rework_requested": True,
                "qa_rework_exhausted": False,
                "qa_rework_retry_count": 1,
                "qa_rework_max_retries": 3,
                "qa_rework_reason": "qa_score_below_threshold",
            },
        )

    async def execute(self, stage, run, context):
        del run, context
        if stage == "director_dispatch":
            self.director_calls += 1
        if stage == "quality_gate":
            self.qa_calls += 1
            if self.qa_calls == 1:
                self._request_taskboard_rework()
                return StageResult(
                    stage=stage,
                    status="failed",
                    output=(
                        "Quality gate completed: Run status: completed; qa_passed=False; "
                        "qa_score=32; qa_critical=0; qa_gate_blocker=qa_score_below_threshold"
                    ),
                    artifacts=["runtime/qa/report.json"],
                )
        return StageResult(
            stage=stage,
            status="success",
            output=f"{stage} completed",
            artifacts=[f"artifacts/{stage}.json"],
        )


class TaskBoundaryQualityReworkStageExecutor(FakeStageExecutor):
    """Executor that emits runtime plan-probe evidence requiring task-boundary rework."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.director_calls = 0
        self.qa_calls = 0

    def _write_workspace_validation(self) -> None:
        target = Path(resolve_logical_path(str(self.workspace), "workspace/qa/latest.workspace-validation.json"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "passed": False,
                    "warnings": ["task_boundary_interface_discrepancy_required"],
                    "errors": [
                        {
                            "path": "src/main.ts",
                            "line": 69,
                            "message": "Property 'displayName' does not exist on type 'string'.",
                            "code": "TS2339",
                        }
                    ],
                    "repair": {
                        "task_boundary_triage_required": True,
                        "success_reason": "task_boundary_interface_discrepancy_required",
                        "plan_probe_preaudit": {
                            "status": "coverage_matched_but_unplannable",
                            "plannable_source_tools": [],
                        },
                        "interface_discrepancy_evidence": {
                            "reason": "coverage_matched_but_unplannable",
                            "plan_probe_status": "coverage_matched_but_unplannable",
                        },
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def _materialize_failed_director_task(self) -> None:
        task_board = TaskRuntimeService(str(self.workspace))
        row = task_board.ensure_task_row(
            external_task_id="TASK-1",
            subject="Implement cross-file contract",
            metadata={
                "external_task_id": "TASK-1",
                "adapter_result": {
                    "quality_repair": {
                        "stage": "runtime_plan_probe_unplannable",
                        "success_reason": "task_boundary_interface_discrepancy_required",
                        "plan_probe_preaudit": {"status": "coverage_matched_but_unplannable"},
                        "interface_discrepancy_evidence": {
                            "reason": "coverage_matched_but_unplannable",
                            "plan_probe_status": "coverage_matched_but_unplannable",
                        },
                    }
                },
                "last_execution_error": "director_materialization_quality_failed",
            },
            priority=1,
        )
        _fail_task_row(
            task_board,
            row["id"],
            worker_id="director",
            metadata={"last_execution_error": "director_materialization_quality_failed"},
        )

    async def execute(self, stage, run, context):
        del run, context
        if stage == "director_dispatch":
            self.director_calls += 1
        if stage == "quality_gate":
            self.qa_calls += 1
            if self.qa_calls == 1:
                self._materialize_failed_director_task()
                self._write_workspace_validation()
                return StageResult(
                    stage=stage,
                    status="failed",
                    output=(
                        "Quality gate completed: Run status: failed; qa_passed=False; "
                        "qa_gate_blocker=task_boundary_interface_discrepancy_required"
                    ),
                    artifacts=["runtime/qa/workspace-validation.json"],
                )
        return StageResult(
            stage=stage,
            status="success",
            output=f"{stage} completed",
            artifacts=[f"artifacts/{stage}.json"],
        )


def test_orchestration_stage_executor_maps_docs_artifacts_to_workspace_prefix(tmp_path: Path) -> None:
    executor = OrchestrationStageExecutor(tmp_path)

    resolved = executor._artifact_path("docs/plan.md")
    expected = Path(resolve_logical_path(str(tmp_path), "workspace/docs/plan.md")).resolve()

    assert resolved == expected


def test_factory_router_resolves_docs_stage_artifacts_from_workspace_layer(tmp_path: Path) -> None:
    resolved = factory_router_module._resolve_runtime_path(str(tmp_path), "docs/plan.md")
    expected = Path(resolve_logical_path(str(tmp_path), "workspace/docs/plan.md")).resolve()

    assert resolved == expected


class LoopingStageExecutor:
    """Executor that emits changing PM plans to validate factory loop convergence."""

    def __init__(self, workspace: Path, signatures: list[str], complete_cycle: int = 3) -> None:
        self.workspace = workspace
        self.signatures = signatures
        self.complete_cycle = complete_cycle
        self.pm_calls = 0
        self.qa_calls = 0

    def _write_json(self, relative_path: str, payload: dict) -> None:
        rel = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
        if rel.startswith(("tasks/", "dispatch/")):
            rel = f"runtime/{rel}"
        target = Path(resolve_logical_path(str(self.workspace), rel))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    async def execute(self, stage, run, context):
        del run, context
        if stage == "pm_planning":
            self.pm_calls += 1
            index = min(self.pm_calls - 1, len(self.signatures) - 1)
            signature = self.signatures[index]
            self._write_json(
                "tasks/plan.json",
                {
                    "tasks": [
                        {
                            "id": f"TASK-{signature}",
                            "title": f"task-{signature}",
                            "goal": f"goal-{signature}",
                            "scope": "src",
                            "steps": [f"step-{signature}"],
                            "acceptance": [f"accept-{signature}"],
                        }
                    ]
                },
            )
            self._write_json(
                "runtime/contracts/architect.docs_pipeline.json",
                {
                    "schema_version": 1,
                    "stages": [
                        {"id": "DOC-STAGE-01", "doc_path": "docs/a.md"},
                        {"id": "DOC-STAGE-02", "doc_path": "docs/b.md"},
                    ],
                },
            )
            pipeline_complete = self.pm_calls >= self.complete_cycle
            self._write_json(
                "runtime/state/pm.docs_progress.json",
                {
                    "schema_version": 1,
                    "active_stage_index": 1 if pipeline_complete else 0,
                    "active_stage_id": "DOC-STAGE-02" if pipeline_complete else "DOC-STAGE-01",
                    "advance_reason": "pipeline_complete" if pipeline_complete else "waiting_for_new_contract",
                },
            )
        elif stage == "quality_gate":
            self.qa_calls += 1

        return StageResult(
            stage=stage,
            status="success",
            output=f"{stage} completed",
            artifacts=[f"artifacts/{stage}.json"],
        )


@pytest.fixture
def temp_workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(autouse=True)
def _disable_factory_jetstream_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop_publish_to_jetstream(**_kwargs):
        return True

    monkeypatch.setattr(
        "polaris.delivery.http.routers.jetstream_utils.publish_to_jetstream",
        _noop_publish_to_jetstream,
    )
    monkeypatch.setattr(factory_router_module, "publish_to_jetstream", _noop_publish_to_jetstream)


@pytest.fixture
def service(temp_workspace: Path) -> FactoryRunService:
    return FactoryRunService(temp_workspace, executor=FakeStageExecutor())


@pytest.fixture
def client(temp_workspace: Path, service: FactoryRunService, monkeypatch: pytest.MonkeyPatch):
    # Set a test token before creating the app (IRONWALL-1: auth is now enforced)
    test_token = "test-factory-router-token-2024"
    monkeypatch.setenv("KERNELONE_TOKEN", test_token)
    app = create_app(Settings(workspace=temp_workspace))
    monkeypatch.setattr(factory_router_module, "_get_service", lambda workspace: service)
    monkeypatch.setattr(factory_router_module, "ensure_required_roles_ready", lambda *args, **kwargs: None)

    with TestClient(app, headers={"Authorization": f"Bearer {test_token}"}) as test_client:
        yield test_client


def test_start_and_get_factory_run_without_workspace(client: TestClient, temp_workspace: Path) -> None:
    response = client.post(
        "/v2/factory/runs",
        json={
            "workspace": str(temp_workspace),
            "start_from": "architect",
            "directive": "Build a complete workflow",
            "run_director": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "running"
    assert "phase" in payload
    assert "current_stage" in payload

    status_response = client.get(f"/v2/factory/runs/{payload['run_id']}")
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert "status" in status_payload
    assert "current_stage" in status_payload
    assert "last_successful_stage" in status_payload


def test_cancel_factory_run_without_workspace(
    client: TestClient,
    service: FactoryRunService,
) -> None:
    run = asyncio.run(service.create_run(FactoryConfig(name="manual-run", stages=["pm_planning"])))
    asyncio.run(service.start_run(run.id))

    response = client.post(
        f"/v2/factory/runs/{run.id}/control",
        json={"action": "cancel", "reason": "operator stop"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "cancelled"
    assert payload["phase"] == "cancelled"


def test_stream_route_is_not_registered(
    client: TestClient,
    service: FactoryRunService,
) -> None:
    run = asyncio.run(service.create_run(FactoryConfig(name="stream-run", stages=["pm_planning"])))
    asyncio.run(service.start_run(run.id))
    asyncio.run(service.complete_run(run.id, success=True))

    response = client.get(f"/v2/factory/runs/{run.id}/stream")

    assert response.status_code == 404
    assert "text/event-stream" not in response.headers.get("content-type", "")


def test_start_from_director_is_rejected_as_removed_legacy_alias(
    client: TestClient,
    temp_workspace: Path,
) -> None:
    response = client.post(
        "/v2/factory/runs",
        json={
            "workspace": str(temp_workspace),
            "start_from": "director",
            "directive": "Retry code implementation only",
            "run_director": True,
        },
    )

    assert response.status_code == 422


def test_start_from_pm_builds_pm_chief_engineer_director_chain(
    client: TestClient,
    service: FactoryRunService,
    temp_workspace: Path,
) -> None:
    response = client.post(
        "/v2/factory/runs",
        json={
            "workspace": str(temp_workspace),
            "start_from": "pm",
            "directive": "Plan and execute implementation",
            "run_director": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    run_id = str(payload.get("run_id") or "")
    assert run_id

    run = asyncio.run(service.get_run(run_id))
    assert run is not None
    assert list(run.config.stages) == [
        "pm_planning",
        "chief_engineer_review",
        "director_dispatch",
        "quality_gate",
    ]


def test_factory_director_context_defaults_to_auto_rounds() -> None:
    payload = FactoryStartRequest(
        workspace="C:/tmp/workspace",
        metadata={"factory_bench_session_id": "bench-1"},
    )
    state = SimpleNamespace(
        settings=SimpleNamespace(
            director_execution_mode="parallel",
            director_max_parallel_tasks=2,
        )
    )

    context = factory_router_module._build_stage_context("director_dispatch", payload, state, run_id="factory-1")

    assert "director_max_rounds" not in context
    assert context["factory_run_id"] == "factory-1"
    assert context["metadata"]["factory_bench_session_id"] == "bench-1"
    assert context["timeout"] == 1800
    assert context["director_dispatch_timeout_seconds"] == 1800
    assert context["llm_call_timeout_seconds"] == 1800
    assert context["director_llm_timeout_seconds"] == 1800


def test_factory_director_context_uses_director_timeout_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS", "2100")
    payload = FactoryStartRequest(workspace="C:/tmp/workspace")
    state = SimpleNamespace(
        settings=SimpleNamespace(
            director_execution_mode="parallel",
            director_max_parallel_tasks=2,
        )
    )

    context = factory_router_module._build_stage_context("director_dispatch", payload, state)

    assert context["timeout"] == 2100
    assert context["director_dispatch_timeout_seconds"] == 2100
    assert context["llm_call_timeout_seconds"] == 2100
    assert context["director_llm_timeout_seconds"] == 2100


def test_factory_director_context_honors_explicit_round_cap() -> None:
    payload = FactoryStartRequest(workspace="C:/tmp/workspace", director_iterations=4)
    state = SimpleNamespace(
        settings=SimpleNamespace(
            director_execution_mode="parallel",
            director_max_parallel_tasks=2,
        )
    )

    context = factory_router_module._build_stage_context("director_dispatch", payload, state)

    assert context["director_max_rounds"] == 4


def test_factory_director_context_honors_payload_execution_mode() -> None:
    payload = FactoryStartRequest(
        workspace="C:/tmp/workspace",
        director_workflow_execution_mode="parallel",
    )
    state = SimpleNamespace(
        settings=SimpleNamespace(
            director_execution_mode="serial",
            director_max_parallel_tasks=2,
        )
    )

    context = factory_router_module._build_stage_context("director_dispatch", payload, state)

    assert context["execution_mode"] == "parallel"
    assert context["director_dispatch_driver"] == "task-market"
    assert context["dispatch_mode"] == "mainline-full"


def test_factory_readiness_roles_skip_local_chief_engineer_stage() -> None:
    roles = factory_router_module._required_ready_roles_for_stages(
        ["pm_planning", "chief_engineer_review", "director_dispatch", "quality_gate"],
        qa_enabled=True,
    )

    assert roles == ["pm", "director", "qa"]


def test_factory_readiness_roles_include_architect_for_docs_generation() -> None:
    roles = factory_router_module._required_ready_roles_for_stages(
        ["docs_generation", "pm_planning", "chief_engineer_review", "director_dispatch", "quality_gate"],
        qa_enabled=True,
    )

    assert roles == ["architect", "pm", "director", "qa"]


def test_factory_role_projection_marks_chief_engineer_stage_running() -> None:
    run = factory_router_module.FactoryRun(
        id="factory_ce_role",
        config=factory_router_module.FactoryConfig(name="test", stages=["chief_engineer_review"]),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-05-23T00:00:00Z",
        metadata={"current_stage": "chief_engineer_review"},
    )

    status = factory_router_module._map_service_run_to_contract(run)

    assert status.phase == factory_router_module.RunPhase.PLANNING
    assert status.roles["chief_engineer"].status == "running"
    assert status.roles["chief_engineer"].current_task == "chief_engineer_review"


def test_artifact_endpoint_includes_existing_stage_result_artifacts(
    client: TestClient,
    service: FactoryRunService,
    temp_workspace: Path,
) -> None:
    run = asyncio.run(service.create_run(FactoryConfig(name="artifact-stage-run", stages=["chief_engineer_review"])))
    blueprint_path = Path(resolve_logical_path(str(temp_workspace), "runtime/blueprints/ce-test.json"))
    blueprint_path.parent.mkdir(parents=True, exist_ok=True)
    blueprint_path.write_text('{"blueprint_id":"ce-test","task_id":"TASK-CE-1"}\n', encoding="utf-8")
    asyncio.run(
        service._append_event(
            run.id,
            {
                "type": "stage_completed",
                "stage": "chief_engineer_review",
                "message": "Chief Engineer review completed",
                "result": {
                    "stage": "chief_engineer_review",
                    "status": "success",
                    "artifacts": ["runtime/blueprints/ce-test.json"],
                },
            },
        )
    )

    response = client.get(f"/v2/factory/runs/{run.id}/artifacts")

    assert response.status_code == 200
    artifacts = response.json()["artifacts"]
    assert any(item["path"] == "runtime/blueprints/ce-test.json" for item in artifacts)
    assert any(
        item["path"] == "runtime/blueprints/ce-test.json" and item.get("task_id") == "TASK-CE-1" for item in artifacts
    )


def test_delivery_loop_replans_until_pipeline_complete_and_stable(temp_workspace: Path) -> None:
    executor = LoopingStageExecutor(temp_workspace, signatures=["A", "B", "B"], complete_cycle=3)
    service = FactoryRunService(temp_workspace, executor=executor)
    run = asyncio.run(service.create_run(FactoryConfig(name="loop-run", stages=["pm_planning", "quality_gate"])))
    asyncio.run(service.start_run(run.id))

    payload = FactoryStartRequest(
        workspace=str(temp_workspace),
        start_from="pm",
        directive="Loop until architect docs are fully implemented",
        run_director=False,
        loop=True,
    )
    state = SimpleNamespace(settings=Settings(workspace=str(temp_workspace)))

    asyncio.run(factory_router_module._execute_run_with_service(service, run.id, payload, state))
    updated = asyncio.run(service.get_run(run.id))
    assert updated is not None
    assert updated.status == FactoryRunStatus.COMPLETED
    assert int(updated.metadata.get("loop_cycles_executed") or 0) == 3
    history = updated.metadata.get("loop_history")
    assert isinstance(history, list) and len(history) == 3
    assert str(updated.metadata.get("loop_stop_reason") or "") == "plan_signature_stable"
    assert executor.pm_calls == 3
    assert executor.qa_calls == 1
    summary_json = updated.metadata.get("summary_json")
    assert isinstance(summary_json, dict)
    assert summary_json.get("status") == "PASS"


def test_delivery_loop_fails_when_docs_pipeline_stalled_without_new_plan(temp_workspace: Path) -> None:
    decision = factory_router_module._decide_delivery_loop_action(
        plan_signature="same-signature",
        previous_plan_signature="same-signature",
        unchanged_cycles=2,
        docs_state={"enabled": True, "completed": False},
        max_stalled_cycles=2,
    )
    assert decision["action"] == "fail"
    assert decision["reason"] == "docs_pipeline_stalled"


def test_quality_gate_rework_summary_reads_taskboard_requests(temp_workspace: Path) -> None:
    task_board = TaskRuntimeService(str(temp_workspace))
    row = task_board.ensure_task_row(
        external_task_id="TASK-1",
        subject="Implement behavior with tests",
        metadata={"external_task_id": "TASK-1"},
        priority=1,
    )
    task_board.update_task_row(
        row["id"],
        metadata={
            "qa_rework_requested": True,
            "qa_rework_exhausted": False,
            "qa_rework_retry_count": 1,
            "qa_rework_max_retries": 3,
            "qa_rework_reason": "qa_score_below_threshold",
        },
    )

    summary = factory_router_module._read_quality_gate_rework_summary(str(temp_workspace))

    assert summary["requested"] is True
    assert summary["requested_count"] == 1
    assert summary["ready_count"] == 1
    assert summary["tasks"][0]["external_task_id"] == "TASK-1"
    assert summary["tasks"][0]["reason"] == "qa_score_below_threshold"


def test_quality_gate_rework_summary_uses_task_row_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    class _TaskRowService:
        def __init__(self, workspace: str) -> None:
            self.workspace = workspace

        def list_observable_task_rows(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": 7,
                    "status": "pending",
                    "metadata": {
                        "external_task_id": "TASK-7",
                        "qa_rework_requested": True,
                        "qa_rework_reason": "qa_score_below_threshold",
                    },
                }
            ]

        def list_task_rows(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            raise AssertionError("quality-gate rework summary must read observable task rows")

        def list_all(self) -> list[object]:
            raise AssertionError("quality-gate rework summary must not read raw TaskBoard entities")

    monkeypatch.setattr(factory_router_module, "TaskRuntimeService", _TaskRowService)

    summary = factory_router_module._read_quality_gate_rework_summary("/tmp/workspace")

    assert summary["requested"] is True
    assert summary["requested_count"] == 1
    assert summary["ready_count"] == 1
    assert summary["tasks"] == [
        {
            "task_id": "7",
            "external_task_id": "TASK-7",
            "status": "pending",
            "reason": "qa_score_below_threshold",
            "retry_count": None,
            "max_retries": None,
            "exhausted": False,
        }
    ]


def test_quality_gate_rework_summary_uses_source_task_id_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    class _TaskRowService:
        def __init__(self, workspace: str) -> None:
            self.workspace = workspace

        def list_observable_task_rows(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": 17,
                    "status": "pending",
                    "metadata": {
                        "source_task_id": "TASK-17",
                        "qa_rework_requested": True,
                        "qa_rework_reason": "owner_handoff_retry",
                    },
                }
            ]

    monkeypatch.setattr(factory_router_module, "TaskRuntimeService", _TaskRowService)

    summary = factory_router_module._read_quality_gate_rework_summary("/tmp/workspace")

    assert summary["tasks"][0]["external_task_id"] == "TASK-17"


def test_factory_task_identifier_extraction_uses_scope_authority_aliases() -> None:
    payload = {
        "raw": {"source_task_id": "TASK-42"},
        "metadata": {"pm_task_id": "TASK-41"},
    }

    assert factory_router_module._extract_task_id_from_payload(payload) == "TASK-42"
    assert factory_router_module._extract_task_id_from_payload({"metadata": {"source_task_id": "TASK-43"}}) == "TASK-43"


def test_per_binding_status_extracts_source_task_id_alias() -> None:
    statuses = factory_router_module._extract_per_binding_task_status(
        [
            {
                "type": "task_claimed",
                "result": {"source_task_id": "TASK-99"},
            },
            {
                "type": "task_completed",
                "source_task_id": "TASK-99",
            },
        ]
    )

    assert statuses == [{"task_id": "TASK-99", "status": "completed", "events": ["task_claimed", "task_completed"]}]


def test_quality_gate_task_boundary_rework_uses_task_row_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _TaskRowService:
        def __init__(self, workspace: str) -> None:
            self.workspace = workspace

        def list_observable_task_rows(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": 8,
                    "status": "completed",
                    "metadata": {"external_task_id": "TASK-8"},
                }
            ]

        def list_task_rows(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            raise AssertionError("quality-gate task-boundary rework must read observable task rows")

        def list_all(self) -> list[object]:
            raise AssertionError("quality-gate task-boundary rework must not read raw TaskBoard entities")

        def update(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("test fixture should not request update")

        def reopen(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("test fixture should not request reopen")

    monkeypatch.setattr(factory_router_module, "TaskRuntimeService", _TaskRowService)
    monkeypatch.setattr(
        factory_router_module,
        "_read_task_boundary_workspace_validation",
        lambda _workspace: ({"passed": False, "repair": {}}, "workspace/qa/latest.workspace-validation.json"),
    )

    summary = factory_router_module._apply_quality_gate_task_boundary_rework_requests("/tmp/workspace")

    assert summary["requested"] is False
    assert summary["evaluated_count"] == 0
    assert summary["reopened_count"] == 0
    assert summary["tasks"] == []


def test_quality_gate_task_boundary_rework_routes_reopen_through_task_runtime_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances: list[Any] = []

    class _TaskRowService:
        def __init__(self, workspace: str) -> None:
            self.workspace = workspace
            self.reopen_calls: list[dict[str, Any]] = []
            instances.append(self)

        def list_observable_task_rows(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": 8,
                    "status": "failed",
                    "metadata": {
                        "external_task_id": "TASK-8",
                        "adapter_result": {
                            "success_reason": factory_router_module._TASK_BOUNDARY_REWORK_REASON,
                        },
                    },
                }
            ]

        def list_task_rows(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            raise AssertionError("quality-gate task-boundary rework must read observable task rows")

        def update_task_row(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("non-exhausted rework must not update task rows directly")

        def update(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("non-exhausted rework must not call legacy update")

        def reopen(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("non-exhausted rework must not call legacy reopen")

        def reopen_task_row(
            self,
            task_id: Any,
            *,
            reason: str,
            metadata: dict[str, Any],
        ) -> dict[str, Any]:
            self.reopen_calls.append({"task_id": task_id, "reason": reason, "metadata": dict(metadata)})
            return {"id": task_id, "status": "pending", "metadata": dict(metadata), "execution_event": {"ok": True}}

    monkeypatch.setattr(factory_router_module, "TaskRuntimeService", _TaskRowService)
    monkeypatch.setattr(factory_router_module, "_resolve_quality_rework_max_cycles", lambda: 3)
    monkeypatch.setattr(
        factory_router_module,
        "_read_task_boundary_workspace_validation",
        lambda _workspace: (
            {
                "passed": False,
                "repair": {"success_reason": factory_router_module._TASK_BOUNDARY_REWORK_REASON},
            },
            "workspace/qa/latest.workspace-validation.json",
        ),
    )

    summary = factory_router_module._apply_quality_gate_task_boundary_rework_requests("/tmp/workspace")

    assert summary["requested"] is True
    assert summary["evaluated_count"] == 1
    assert summary["reopened_count"] == 1
    assert summary["skipped_count"] == 0
    assert summary["tasks"][0]["external_task_id"] == "TASK-8"
    assert len(instances) == 1
    reopen_call = instances[0].reopen_calls[0]
    assert reopen_call["task_id"] == 8
    assert reopen_call["reason"] == factory_router_module._TASK_BOUNDARY_REWORK_REASON
    metadata = reopen_call["metadata"]
    assert "status" not in metadata
    assert metadata["task_boundary_rework_requested"] is True
    assert metadata["qa_rework_requested"] is True
    assert metadata["qa_rework_exhausted"] is False
    assert metadata["qa_rework_retry_count"] == 1
    assert metadata["qa_rework_max_retries"] == 3
    assert metadata["qa_last_verdict"] == "FAIL"
    assert str(metadata["qa_last_reviewed_at"]).strip()
    evidence = metadata["task_boundary_rework_evidence"]
    assert evidence["artifact"] == "workspace/qa/latest.workspace-validation.json"
    assert evidence["reason"] == factory_router_module._TASK_BOUNDARY_REWORK_REASON
    assert evidence["success_reason"] == factory_router_module._TASK_BOUNDARY_REWORK_REASON
    adapter_result = metadata["adapter_result"]
    assert adapter_result["task_boundary_rework_requested"] is True
    assert adapter_result["qa_rework_reason"] == factory_router_module._TASK_BOUNDARY_REWORK_REASON


def test_quality_gate_task_boundary_rework_blocks_reopen_without_execution_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _TaskRowService:
        def __init__(self, workspace: str) -> None:
            self.workspace = workspace

        def list_observable_task_rows(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": 8,
                    "status": "failed",
                    "metadata": {
                        "external_task_id": "TASK-8",
                        "adapter_result": {
                            "success_reason": factory_router_module._TASK_BOUNDARY_REWORK_REASON,
                        },
                    },
                }
            ]

        def reopen_task_row(self, *_args: object, **_kwargs: object) -> dict[str, Any]:
            return {
                "id": 8,
                "execution_event": {
                    "ok": False,
                    "event_type": "task_reopened",
                    "error": "append failed",
                },
            }

        def update_task_row(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("non-exhausted rework must not update task row")

    monkeypatch.setattr(factory_router_module, "TaskRuntimeService", _TaskRowService)
    monkeypatch.setattr(factory_router_module, "_resolve_quality_rework_max_cycles", lambda: 3)
    monkeypatch.setattr(
        factory_router_module,
        "_read_task_boundary_workspace_validation",
        lambda _workspace: (
            {
                "passed": False,
                "repair": {"success_reason": factory_router_module._TASK_BOUNDARY_REWORK_REASON},
            },
            "workspace/qa/latest.workspace-validation.json",
        ),
    )

    summary = factory_router_module._apply_quality_gate_task_boundary_rework_requests("/tmp/workspace")

    assert summary["requested"] is False
    assert summary["evaluated_count"] == 1
    assert summary["reopened_count"] == 0
    assert summary["skipped_count"] == 1
    assert summary["tasks"] == []
    assert summary["task_runtime_transition_failures"] == [
        {
            "success": False,
            "task_id": 8,
            "action": "reopen_for_rework",
            "reason": "task_runtime_execution_event_append_failed",
            "transition_result": {
                "ok": False,
                "event_type": "task_reopened",
                "error": "append failed",
            },
        }
    ]


def test_quality_gate_task_boundary_rework_blocks_exhausted_update_without_execution_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _TaskRowService:
        def __init__(self, workspace: str) -> None:
            self.workspace = workspace

        def list_observable_task_rows(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": 9,
                    "status": "failed",
                    "metadata": {
                        "external_task_id": "TASK-9",
                        "adapter_result": {
                            "success_reason": factory_router_module._TASK_BOUNDARY_REWORK_REASON,
                        },
                    },
                }
            ]

        def update_task_row(self, *_args: object, **_kwargs: object) -> dict[str, Any]:
            return {
                "id": 9,
                "execution_event": {
                    "ok": False,
                    "event_type": "task_rework_exhausted",
                    "error": "append failed",
                },
            }

        def reopen_task_row(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("exhausted rework must not reopen task row")

    monkeypatch.setattr(factory_router_module, "TaskRuntimeService", _TaskRowService)
    monkeypatch.setattr(factory_router_module, "_resolve_quality_rework_max_cycles", lambda: 1)
    monkeypatch.setattr(
        factory_router_module,
        "_read_task_boundary_workspace_validation",
        lambda _workspace: (
            {
                "passed": False,
                "repair": {"success_reason": factory_router_module._TASK_BOUNDARY_REWORK_REASON},
            },
            "workspace/qa/latest.workspace-validation.json",
        ),
    )

    summary = factory_router_module._apply_quality_gate_task_boundary_rework_requests("/tmp/workspace")

    assert summary["requested"] is False
    assert summary["evaluated_count"] == 1
    assert summary["exhausted_count"] == 0
    assert summary["skipped_count"] == 1
    assert summary["tasks"] == []
    assert summary["task_runtime_transition_failures"] == [
        {
            "success": False,
            "task_id": 9,
            "action": "mark_rework_exhausted",
            "reason": "task_runtime_execution_event_append_failed",
            "transition_result": {
                "ok": False,
                "event_type": "task_rework_exhausted",
                "error": "append failed",
            },
        }
    ]


def test_quality_gate_rework_summary_keeps_exhausted_requests(temp_workspace: Path) -> None:
    task_board = TaskRuntimeService(str(temp_workspace))
    row = task_board.ensure_task_row(
        external_task_id="TASK-1",
        subject="Implement behavior with tests",
        metadata={"external_task_id": "TASK-1"},
        priority=1,
    )
    _fail_task_row(
        task_board,
        row["id"],
        metadata={
            "qa_rework_requested": False,
            "qa_rework_exhausted": True,
            "qa_rework_retry_count": 3,
            "qa_rework_max_retries": 3,
            "qa_rework_reason": "task_boundary_interface_discrepancy_required",
        },
    )

    summary = factory_router_module._read_quality_gate_rework_summary(str(temp_workspace))

    assert summary["requested"] is False
    assert summary["requested_count"] == 0
    assert summary["exhausted_count"] == 1
    assert summary["tasks"][0]["external_task_id"] == "TASK-1"
    assert summary["tasks"][0]["exhausted"] is True


def test_quality_gate_task_boundary_validation_reopens_failed_director_task(temp_workspace: Path) -> None:
    executor = TaskBoundaryQualityReworkStageExecutor(temp_workspace)
    executor._materialize_failed_director_task()
    executor._write_workspace_validation()

    bridge_summary = factory_router_module._apply_quality_gate_task_boundary_rework_requests(str(temp_workspace))
    rework_summary = factory_router_module._read_quality_gate_rework_summary(str(temp_workspace))

    assert bridge_summary["requested"] is True
    assert bridge_summary["reopened_count"] == 1
    assert bridge_summary["exhausted_count"] == 0
    assert rework_summary["requested"] is True
    assert rework_summary["requested_count"] == 1
    assert rework_summary["ready_count"] == 1
    assert rework_summary["tasks"][0]["reason"] == "task_boundary_interface_discrepancy_required"

    rows = TaskRuntimeService(str(temp_workspace)).list_observable_task_rows()
    assert rows[0]["status"] == "pending"
    metadata = rows[0]["metadata"]
    assert metadata["qa_rework_requested"] is True
    assert metadata["task_boundary_rework_requested"] is True
    evidence = metadata["task_boundary_rework_evidence"]
    assert evidence["plan_probe_preaudit"]["status"] == "coverage_matched_but_unplannable"


def test_quality_gate_task_boundary_validation_routes_owner_handoff_to_owner_task(temp_workspace: Path) -> None:
    task_board = TaskRuntimeService(str(temp_workspace))
    owner_row = task_board.ensure_task_row(
        external_task_id="PM-0001-1-S4",
        subject="Owner creates src/index.js",
        metadata={"external_task_id": "PM-0001-1-S4", "source_task_id": "PM-0001-1-S4"},
        priority=1,
    )
    current_row = task_board.ensure_task_row(
        external_task_id="PM-0001-2-step-3",
        subject="Current task wants to repair tests",
        metadata={
            "external_task_id": "PM-0001-2-step-3",
            "last_execution_error": "director_materialization_quality_failed",
            "adapter_result": {
                "quality_repair": {
                    "stage": "task_boundary_repair_targets_deferred",
                    "success_reason": "repair_targets_outside_current_task_target_files",
                }
            },
        },
        priority=2,
    )
    _complete_task_row(task_board, owner_row["id"], worker_id="director")
    _fail_task_row(task_board, current_row["id"], worker_id="director")

    target = Path(resolve_logical_path(str(temp_workspace), "workspace/qa/latest.workspace-validation.json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "passed": False,
                "repair": {
                    "success_reason": "repair_targets_outside_current_task_target_files",
                    "task_boundary_scope_filter": {
                        "schema_version": "director.task_boundary.repair_scope_filter.v1",
                        "reason": "quality_repair_targets_outside_current_task_target_files",
                        "ownership_handoff_requests": [
                            {
                                "schema_version": "file-ownership-handoff-request/1",
                                "target_file": "src/index.js",
                                "requesting_task_id": "PM-0001-2-step-3",
                                "reason": "quality_repair_targets_outside_current_task_target_files",
                                "owner_step_id": "PM-0001-1-S4",
                                "owner_parent": "PM-0001-1",
                                "owner_found": True,
                                "recommended_route": "owner_task_retry",
                                "status": "owner_found",
                            }
                        ],
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    bridge_summary = factory_router_module._apply_quality_gate_task_boundary_rework_requests(str(temp_workspace))
    rework_summary = factory_router_module._read_quality_gate_rework_summary(str(temp_workspace))

    assert bridge_summary["requested"] is True
    assert bridge_summary["reopened_count"] == 1
    assert bridge_summary["skipped_count"] == 0
    assert bridge_summary["unmatched_owner_handoff_count"] == 0
    assert bridge_summary["unmatched_owner_handoff_requests"] == []
    assert bridge_summary["tasks"][0]["external_task_id"] == "PM-0001-1-S4"
    assert bridge_summary["tasks"][0]["reason"] == "task_boundary_owner_task_retry_required"
    assert rework_summary["requested_count"] == 1
    assert rework_summary["unmatched_owner_handoff_count"] == 0
    assert rework_summary["unmatched_owner_handoff_requests"] == []
    assert rework_summary["tasks"][0]["external_task_id"] == "PM-0001-1-S4"

    rows = {
        row["metadata"]["external_task_id"]: row
        for row in TaskRuntimeService(str(temp_workspace)).list_observable_task_rows()
    }
    assert rows["PM-0001-1-S4"]["status"] == "pending"
    assert rows["PM-0001-1-S4"]["metadata"]["task_boundary_rework_reason"] == (
        "task_boundary_owner_task_retry_required"
    )
    assert rows["PM-0001-2-step-3"]["status"] == "failed"


def test_quality_gate_task_boundary_validation_routes_scope_authority_nested_handoff(
    temp_workspace: Path,
) -> None:
    task_board = TaskRuntimeService(str(temp_workspace))
    owner_row = task_board.ensure_task_row(
        external_task_id="PM-0001-1-S4",
        subject="Owner creates src/index.js",
        metadata={"external_task_id": "PM-0001-1-S4"},
        priority=1,
    )
    _complete_task_row(task_board, owner_row["id"], worker_id="director")

    handoff_request = {
        "schema_version": "file-ownership-handoff-request/1",
        "target_file": "src/index.js",
        "requesting_task_id": "PM-0001-2-step-3",
        "reason": "quality_repair_targets_outside_current_task_target_files",
        "owner_step_id": "PM-0001-1-S4",
        "owner_parent": "PM-0001-1",
        "owner_found": True,
        "recommended_route": "owner_task_retry",
        "status": "owner_found",
    }
    target = Path(resolve_logical_path(str(temp_workspace), "workspace/qa/latest.workspace-validation.json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "passed": False,
                "repair": {
                    "success_reason": "repair_targets_outside_current_task_target_files",
                    "task_boundary_scope_filter": {
                        "schema_version": "director.task_boundary.repair_scope_filter.v1",
                        "reason": "quality_repair_targets_outside_current_task_target_files",
                        "scope_authority": {
                            "schema_version": "scope-authority-decision/1",
                            "owner_task_retry_handoff_requests": [handoff_request],
                        },
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    bridge_summary = factory_router_module._apply_quality_gate_task_boundary_rework_requests(str(temp_workspace))

    assert bridge_summary["requested"] is True
    assert bridge_summary["reopened_count"] == 1
    assert bridge_summary["skipped_count"] == 0
    assert bridge_summary["unmatched_owner_handoff_count"] == 0
    assert bridge_summary["unmatched_owner_handoff_requests"] == []
    assert bridge_summary["tasks"][0]["external_task_id"] == "PM-0001-1-S4"
    assert bridge_summary["tasks"][0]["ownership_handoff_target_file"] == "src/index.js"
    assert bridge_summary["tasks"][0]["ownership_handoff_request"] == handoff_request


def test_ownership_handoff_requests_accept_flat_scope_authority_payload() -> None:
    handoff_request = {
        "schema_version": "file-ownership-handoff-request/1",
        "target_file": "src/index.js",
        "owner_step_id": "PM-0001-1-S4",
        "owner_found": True,
        "recommended_route": "owner_task_retry",
    }

    from_scope_authority = factory_router_module._ownership_handoff_requests_from_repair_payload(
        {"scope_authority": {"ownership_handoff_requests": [handoff_request]}}
    )
    from_direct_requests = factory_router_module._ownership_handoff_requests_from_repair_payload(
        {"ownership_handoff_requests": [handoff_request]}
    )

    assert from_scope_authority == [handoff_request]
    assert from_direct_requests == [handoff_request]


def test_owner_handoff_matching_prefers_projected_identifier_tokens() -> None:
    owner_row = {
        "id": "row-1",
        "external_task_id": "TASK-4",
        "metadata": {"external_task_id": "TASK-4"},
    }
    request = {
        "schema_version": "file-ownership-handoff-request/1",
        "target_file": "src/index.js",
        "owner_step_id": "unmatched-owner-step",
        "owner_parent": "unmatched-parent",
        "owner_task_identifier_tokens": ["4", "TASK-04", "TASK-4"],
        "owner_found": True,
        "recommended_route": "owner_task_retry",
    }

    assert matching_owner_handoff_request(owner_row, [request]) == request
    assert set(owner_handoff_identifier_tokens(request)) == {"4", "TASK-04", "TASK-4"}


def test_quality_gate_task_boundary_validation_reports_unmatched_owner_handoff(temp_workspace: Path) -> None:
    task_board = TaskRuntimeService(str(temp_workspace))
    current_row = task_board.ensure_task_row(
        external_task_id="PM-0001-2-step-3",
        subject="Current task wants to repair tests",
        metadata={
            "external_task_id": "PM-0001-2-step-3",
            "last_execution_error": "director_materialization_quality_failed",
        },
        priority=2,
    )
    _fail_task_row(task_board, current_row["id"], worker_id="director")

    target = Path(resolve_logical_path(str(temp_workspace), "workspace/qa/latest.workspace-validation.json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "passed": False,
                "repair": {
                    "success_reason": "repair_targets_outside_current_task_target_files",
                    "task_boundary_scope_filter": {
                        "schema_version": "director.task_boundary.repair_scope_filter.v1",
                        "reason": "quality_repair_targets_outside_current_task_target_files",
                        "ownership_handoff_requests": [
                            {
                                "schema_version": "file-ownership-handoff-request/1",
                                "target_file": "src/index.js",
                                "requesting_task_id": "PM-0001-2-step-3",
                                "reason": "quality_repair_targets_outside_current_task_target_files",
                                "owner_step_id": "PM-0001-1-S4",
                                "owner_parent": "PM-0001-1",
                                "owner_found": True,
                                "recommended_route": "owner_task_retry",
                                "status": "owner_found",
                            }
                        ],
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    bridge_summary = factory_router_module._apply_quality_gate_task_boundary_rework_requests(str(temp_workspace))
    rework_summary = factory_router_module._read_quality_gate_rework_summary(str(temp_workspace))

    assert bridge_summary["requested"] is False
    assert bridge_summary["reopened_count"] == 0
    assert bridge_summary["skipped_count"] == 1
    assert bridge_summary["unmatched_owner_handoff_count"] == 1
    assert bridge_summary["unmatched_owner_handoff_requests"][0]["owner_step_id"] == "PM-0001-1-S4"
    assert rework_summary["ownership_handoff_count"] == 1
    assert rework_summary["unmatched_owner_handoff_count"] == 1
    assert rework_summary["unmatched_owner_handoff_requests"][0]["owner_step_id"] == "PM-0001-1-S4"

    rows = {
        row["metadata"]["external_task_id"]: row
        for row in TaskRuntimeService(str(temp_workspace)).list_observable_task_rows()
    }
    assert rows["PM-0001-2-step-3"]["status"] == "failed"


def test_task_record_identifier_tokens_include_top_level_projection_fields() -> None:
    tokens = set(
        task_record_identifier_tokens(
            {
                "id": 12,
                "external_task_id": "PM-0001-1-S4",
                "source_task_id": "source-step",
                "metadata": {"pm_task_id": "PM-0001"},
            }
        )
    )

    assert {"12", "PM-0001-1-S4", "source-step", "PM-0001"} <= tokens
    assert "TASK-12" in tokens


def test_matching_owner_handoff_accepts_task_prefix_numeric_alias() -> None:
    request = {
        "target_file": "src/index.js",
        "owner_found": True,
        "recommended_route": "owner_task_retry",
        "owner_step_id": "TASK-12",
    }

    matched = matching_owner_handoff_request(
        {"id": 12, "metadata": {}},
        [request],
    )

    assert matched == request


def test_matching_owner_handoff_aliases_request_owner_tokens() -> None:
    request = {
        "target_file": "src/index.js",
        "owner_found": True,
        "recommended_route": "owner_task_retry",
        "owner_step_id": "TASK-012",
    }

    matched = matching_owner_handoff_request(
        {"id": 12, "metadata": {}},
        [request],
    )

    assert matched == request


def test_quality_gate_owner_handoff_index_centralizes_matching() -> None:
    matched_request = {
        "target_file": "src/index.js",
        "owner_found": True,
        "recommended_route": "owner_task_retry",
        "owner_step_id": "TASK-12",
    }
    unmatched_request = {
        "target_file": "src/missing.js",
        "owner_found": True,
        "recommended_route": "owner_task_retry",
        "owner_step_id": "TASK-99",
    }
    unknown_request = {
        "target_file": "src/unknown.js",
        "owner_found": False,
        "recommended_route": "scope_authority_resolution",
    }

    index = factory_router_module._quality_gate_owner_handoff_index(
        {
            "task_boundary_scope_filter": {
                "ownership_handoff_requests": [
                    matched_request,
                    unmatched_request,
                    unknown_request,
                ]
            }
        },
        [{"id": 12, "metadata": {}}],
    )

    assert index.all_handoff_requests == (matched_request, unmatched_request, unknown_request)
    assert index.owner_handoff_requests == (matched_request, unmatched_request)
    assert index.unknown_owner_handoff_requests == (unknown_request,)
    assert index.matched_owner_handoff_by_task_key["12"] == matched_request
    assert index.unmatched_owner_handoff_requests == (unmatched_request,)


def test_quality_gate_task_boundary_validation_reports_unknown_owner_handoff(temp_workspace: Path) -> None:
    task_board = TaskRuntimeService(str(temp_workspace))
    current_row = task_board.ensure_task_row(
        external_task_id="PM-0001-2-step-3",
        subject="Current task wants to repair tests",
        metadata={
            "external_task_id": "PM-0001-2-step-3",
            "last_execution_error": "director_materialization_quality_failed",
            "adapter_result": {
                "quality_repair": {
                    "stage": "task_boundary_repair_targets_deferred",
                    "success_reason": "repair_targets_outside_current_task_target_files",
                }
            },
        },
        priority=2,
    )
    _fail_task_row(task_board, current_row["id"], worker_id="director")

    target = Path(resolve_logical_path(str(temp_workspace), "workspace/qa/latest.workspace-validation.json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "passed": False,
                "repair": {
                    "success_reason": "repair_targets_outside_current_task_target_files",
                    "task_boundary_scope_filter": {
                        "schema_version": "director.task_boundary.repair_scope_filter.v1",
                        "reason": "quality_repair_targets_outside_current_task_target_files",
                        "ownership_handoff_requests": [
                            {
                                "schema_version": "file-ownership-handoff-request/1",
                                "target_file": "src/index.js",
                                "requesting_task_id": "PM-0001-2-step-3",
                                "reason": "quality_repair_targets_outside_current_task_target_files",
                                "owner_step_id": "",
                                "owner_parent": "",
                                "owner_found": False,
                                "recommended_route": "scope_authority_resolution",
                                "status": "owner_unknown",
                            }
                        ],
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    bridge_summary = factory_router_module._apply_quality_gate_task_boundary_rework_requests(str(temp_workspace))
    rework_summary = factory_router_module._read_quality_gate_rework_summary(str(temp_workspace))

    assert bridge_summary["requested"] is False
    assert bridge_summary["reopened_count"] == 0
    assert bridge_summary["skipped_count"] == 1
    assert bridge_summary["unknown_owner_handoff_count"] == 1
    assert bridge_summary["unknown_owner_handoff_requests"][0]["target_file"] == "src/index.js"
    assert rework_summary["ownership_handoff_count"] == 1
    assert rework_summary["unknown_owner_handoff_count"] == 1
    assert rework_summary["unknown_owner_handoff_requests"][0]["target_file"] == "src/index.js"

    rows = {
        row["metadata"]["external_task_id"]: row
        for row in TaskRuntimeService(str(temp_workspace)).list_observable_task_rows()
    }
    assert rows["PM-0001-2-step-3"]["status"] == "failed"
    assert "qa_rework_requested" not in rows["PM-0001-2-step-3"]["metadata"]


def test_execute_run_reenters_director_when_quality_gate_requests_rework(temp_workspace: Path) -> None:
    executor = QualityReworkStageExecutor(temp_workspace)
    service = FactoryRunService(
        temp_workspace,
        executor=executor,
    )
    run = asyncio.run(
        service.create_run(FactoryConfig(name="qa-rework-run", stages=["director_dispatch", "quality_gate"]))
    )
    asyncio.run(service.start_run(run.id))
    payload = FactoryStartRequest(
        workspace=str(temp_workspace),
        start_from="director_resume",
        directive="Repair QA findings",
        run_director=True,
    )
    state = SimpleNamespace(settings=Settings(workspace=str(temp_workspace)))

    asyncio.run(factory_router_module._execute_run_with_service(service, run.id, payload, state))

    updated = asyncio.run(service.get_run(run.id))
    assert updated is not None
    assert updated.status == FactoryRunStatus.COMPLETED
    assert executor.director_calls == 2
    assert executor.qa_calls == 2
    history = updated.metadata.get("quality_rework_history")
    assert isinstance(history, list) and len(history) == 1
    assert history[0]["summary"]["requested_count"] == 1
    summary_json = updated.metadata.get("summary_json")
    assert isinstance(summary_json, dict)
    assert summary_json.get("status") == "PASS"


def test_execute_run_reenters_director_when_quality_gate_reports_task_boundary_triage(temp_workspace: Path) -> None:
    executor = TaskBoundaryQualityReworkStageExecutor(temp_workspace)
    service = FactoryRunService(
        temp_workspace,
        executor=executor,
    )
    run = asyncio.run(
        service.create_run(FactoryConfig(name="task-boundary-rework-run", stages=["director_dispatch", "quality_gate"]))
    )
    asyncio.run(service.start_run(run.id))
    payload = FactoryStartRequest(
        workspace=str(temp_workspace),
        start_from="director_resume",
        directive="Repair task boundary discrepancy",
        run_director=True,
    )
    state = SimpleNamespace(settings=Settings(workspace=str(temp_workspace)))

    asyncio.run(factory_router_module._execute_run_with_service(service, run.id, payload, state))

    updated = asyncio.run(service.get_run(run.id))
    assert updated is not None
    assert updated.status == FactoryRunStatus.COMPLETED
    assert executor.director_calls == 2
    assert executor.qa_calls == 2
    history = updated.metadata.get("quality_rework_history")
    assert isinstance(history, list) and len(history) == 1
    summary = history[0]["summary"]
    assert summary["requested_count"] == 1
    assert summary["task_boundary_rework_bridge"]["reopened_count"] == 1
    assert summary["tasks"][0]["reason"] == "task_boundary_interface_discrepancy_required"


def test_execute_run_preserves_qa_llm_unavailable_root_cause(temp_workspace: Path) -> None:
    service = FactoryRunService(temp_workspace, executor=QaLlmUnavailableStageExecutor())
    run = asyncio.run(service.create_run(FactoryConfig(name="qa-llm-run", stages=["quality_gate"])))
    asyncio.run(service.start_run(run.id))
    payload = FactoryStartRequest(
        workspace=str(temp_workspace),
        start_from="pm",
        directive="Verify generated project",
        run_director=False,
    )
    state = SimpleNamespace(settings=Settings(workspace=str(temp_workspace)))

    asyncio.run(factory_router_module._execute_run_with_service(service, run.id, payload, state))

    updated = asyncio.run(service.get_run(run.id))
    assert updated is not None
    assert updated.status == FactoryRunStatus.FAILED
    failure = updated.metadata.get("failure")
    assert isinstance(failure, dict)
    assert failure.get("stage") == "quality_gate"
    assert failure.get("code") == "QA_LLM_JUDGEMENT_UNAVAILABLE"
    assert "qa_llm_judgement_unavailable" in str(failure.get("detail") or "")
