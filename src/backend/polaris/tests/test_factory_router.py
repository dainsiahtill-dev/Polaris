"""Tests for Factory router contract and SSE behavior."""

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


def _collect_sse_events(lines) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    current_event = ""
    current_data: list[str] = []

    for line in lines:
        if line == "":
            if current_event:
                events.append((current_event, "\n".join(current_data)))
                if current_event == "done":
                    break
            current_event = ""
            current_data = []
            continue

        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: "):
            current_data.append(line[6:])

    return events


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


def test_stream_emits_status_and_done_events(
    client: TestClient,
    service: FactoryRunService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DisconnectedConsumer:
        is_connected = False

        async def connect(self) -> bool:
            return False

    monkeypatch.setattr(
        factory_router_module,
        "create_sse_jetstream_consumer",
        lambda **_: DisconnectedConsumer(),
    )

    run = asyncio.run(service.create_run(FactoryConfig(name="stream-run", stages=["pm_planning"])))
    asyncio.run(service.start_run(run.id))
    asyncio.run(service.complete_run(run.id, success=True))

    with client.stream("GET", f"/v2/factory/runs/{run.id}/stream") as stream_response:
        assert stream_response.status_code == 200
        events = _collect_sse_events(stream_response.iter_lines())

    event_names = [event_name for event_name, _ in events]
    assert "status" in event_names
    assert "event" in event_names
    assert "done" in event_names


def test_start_from_director_builds_director_to_qa_chain(
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
    assert list(run.config.stages) == ["director_dispatch", "quality_gate"]


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


def test_factory_readiness_roles_skip_local_chief_engineer_stage() -> None:
    roles = factory_router_module._required_ready_roles_for_stages(
        ["pm_planning", "chief_engineer_review", "director_dispatch", "quality_gate"],
        qa_enabled=True,
    )

    assert roles == ["pm", "director", "qa"]


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
