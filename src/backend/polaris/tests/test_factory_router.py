"""Tests for Factory router contract and runtime transport behavior."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

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
from polaris.delivery.http.app_factory import create_app
from polaris.delivery.http.routers import factory as factory_router_module
from polaris.kernelone.storage import resolve_logical_path


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


def test_start_from_director_alias_builds_full_pm_chief_engineer_director_chain(
    client: TestClient,
    service: FactoryRunService,
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
