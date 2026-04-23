"""Tests for Factory router contract and SSE behavior."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.bootstrap.config import Settings
from fastapi.testclient import TestClient
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryConfig,
    FactoryRunService,
    FactoryRunStatus,
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


class LoopingStageExecutor:
    """Executor that emits changing PM plans to validate factory loop convergence."""

    def __init__(self, workspace: Path, signatures: list[str], complete_cycle: int = 3) -> None:
        self.workspace = workspace
        self.signatures = signatures
        self.complete_cycle = complete_cycle
        self.pm_calls = 0
        self.qa_calls = 0

    def _write_json(self, relative_path: str, payload: dict) -> None:
        target = Path(resolve_logical_path(str(self.workspace), relative_path))
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


def test_stream_emits_status_and_done_events(client: TestClient, temp_workspace: Path) -> None:
    response = client.post(
        "/v2/factory/runs",
        json={
            "workspace": str(temp_workspace),
            "start_from": "architect",
            "directive": "Run the full factory pipeline",
            "run_director": True,
        },
    )
    run_id = response.json()["run_id"]

    with client.stream("GET", f"/v2/factory/runs/{run_id}/stream") as stream_response:
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

