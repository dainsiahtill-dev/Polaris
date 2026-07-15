"""Tests for Factory audit bundle assembly."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryConfig,
    FactoryRun,
    FactoryRunService,
    FactoryRunStatus,
)
from polaris.delivery.http.routers import factory as factory_router_module


def _make_failed_run() -> FactoryRun:
    return FactoryRun(
        id="factory_test_001",
        config=FactoryConfig(name="audit-test", stages=["pm_planning", "quality_gate"]),
        status=FactoryRunStatus.FAILED,
        created_at="2026-05-06T00:00:00+00:00",
        updated_at="2026-05-06T00:02:00+00:00",
        started_at="2026-05-06T00:00:30+00:00",
        completed_at="2026-05-06T00:02:00+00:00",
        stages_completed=["pm_planning"],
        stages_failed=["quality_gate"],
        recovery_point="pm_planning",
        metadata={
            "current_stage": "quality_gate",
            "last_successful_stage": "pm_planning",
            "last_failed_stage": "quality_gate",
            "failure": {
                "stage": "quality_gate",
                "code": "FACTORY_STAGE_FAILED",
                "detail": "QA failed",
                "recoverable": False,
                "timestamp": "2026-05-06T00:02:00+00:00",
            },
            "summary_md": "# Summary\n",
            "summary_json": {"status": "FAIL"},
        },
    )


def test_build_factory_audit_bundle_includes_machine_readable_evidence() -> None:
    run = _make_failed_run()
    events = [
        {"type": "stage_started", "stage": "pm_planning"},
        {"type": "error", "stage": "quality_gate"},
    ]
    artifacts = [{"name": "qa.json", "path": ".polaris/factory/factory_test_001/artifacts/qa.json", "size": 17}]

    bundle = factory_router_module._build_factory_audit_bundle(
        run=run,
        events=events,
        artifacts=artifacts,
        events_tail_limit=1,
        generated_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
    )

    assert bundle["run_id"] == "factory_test_001"
    assert bundle["status"] == "failed"
    assert bundle["phase"] == "failed"
    assert bundle["progress"] == 50.0
    assert bundle["current_stage"] == "quality_gate"
    assert bundle["last_successful_stage"] == "pm_planning"
    assert bundle["events_tail"] == [events[-1]]
    assert bundle["artifacts"] == artifacts
    assert bundle["summary_md"] == "# Summary"
    assert bundle["summary_json"] == {"status": "FAIL"}
    assert bundle["generated_at"] == "2026-05-06T00:00:00+00:00"
    assert bundle["gates"][0]["gate_name"] == "quality_gate"
    assert bundle["failure"]["code"] == "FACTORY_STAGE_FAILED"
    assert bundle["evidence_counts"]["events_total"] == 2
    assert bundle["evidence_counts"]["events_tail"] == 1
    assert bundle["evidence_counts"]["artifacts"] == 1
    assert bundle["evidence_counts"]["failures"] == 1
    assert bundle["evidence_counts"]["event_types"] == {"stage_started": 1, "error": 1}


def test_get_factory_run_audit_bundle_reads_service_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _exercise() -> dict[str, Any]:
        service = FactoryRunService(tmp_path)
        run = await service.create_run(FactoryConfig(name="audit-run", stages=["pm_planning"]))
        run.metadata["summary_md"] = "# Summary\n"
        run.metadata["summary_json"] = {"status": "PENDING"}
        run.metadata["factory_start_request"] = {
            "workspace": str(tmp_path),
            "metadata": {
                "factory_bench_requested_project_id": "L2-08",
                "factory_bench_canonical_project_id": "L2-18",
                "instance_id": "bench-instance-1",
                "backend_port": 51001,
                "frontend_port": 52001,
            },
        }
        await service.store.save_run(run)
        artifact_path = service.store.get_run_dir(run.id) / "artifacts" / "evidence.json"
        artifact_path.write_text('{"ok": true}\n', encoding="utf-8")
        await service._append_event(run.id, {"type": "stage_started", "stage": "pm_planning"})
        await service._append_event(run.id, {"type": "stage_completed", "stage": "pm_planning"})

        monkeypatch.setattr(factory_router_module, "_get_service", lambda workspace: service)
        monkeypatch.setattr(
            factory_router_module,
            "read_run_ledger_projection",
            lambda query: SimpleNamespace(
                projection={
                    "schema_version": "control_plane.run_ledger_projection.v1",
                    "source": "run_ledger_projection",
                    "run_id": query.run_id,
                    "task_boundary": {"failed": False},
                    "tool_lifecycle": {"dropped_count": 0},
                }
            ),
        )
        task_runtime_projection = {
            "schema_version": "task_runtime.observable_task_rows_authority.v1",
            "source": "task_runtime.execution_fact",
            "authoritative": True,
            "degraded": False,
            "row_count": 1,
            "rows": [
                {
                    "task_id": "TASK-1",
                    "status": "completed",
                    "execution_state": "completed",
                    "fact_event_seq": 7,
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                }
            ],
            "readiness": {"ready": True, "blocking_reasons": []},
        }
        monkeypatch.setattr(
            factory_router_module,
            "TaskRuntimeService",
            lambda _workspace: SimpleNamespace(
                query_observable_task_rows_projection=lambda: SimpleNamespace(
                    to_authority_dict=lambda: dict(task_runtime_projection)
                )
            ),
        )
        state: Any = SimpleNamespace(settings=SimpleNamespace(workspace=tmp_path))
        response = await factory_router_module.get_factory_run_audit_bundle_v2(run.id, limit=1, state=state)
        return response.model_dump(mode="json")

    payload = asyncio.run(_exercise())

    assert payload["run_id"].startswith("factory_")
    assert payload["events_tail"][0]["type"] == "stage_completed"
    assert payload["artifacts"][0]["name"] == "evidence.json"
    assert payload["summary_md"] == "# Summary"
    assert payload["summary_json"] == {"status": "PENDING"}
    assert payload["evidence_counts"]["events_total"] == 2
    assert payload["evidence_counts"]["artifacts"] == 1
    assert payload["factory_run_id"] == payload["run_id"]
    assert payload["workspace"] == str(tmp_path)
    assert payload["control_plane_projection"]["source"] == "run_ledger_projection"
    assert payload["run_ledger_projection"]["tool_lifecycle"]["dropped_count"] == 0
    assert payload["task_runtime_projection"]["source"] == "task_runtime.execution_fact"
    assert payload["task_runtime_projection"]["authoritative"] is True
    assert payload["task_runtime_projection"]["rows"][0]["task_id"] == "TASK-1"
    assert payload["run_identity"]["requested_project_id"] == "L2-08"
    assert payload["run_identity"]["canonical_project_id"] == "L2-18"
    assert payload["run_identity"]["instance_id"] == "bench-instance-1"
    assert payload["run_identity"]["backend_port"] == 51001
    assert payload["run_identity"]["frontend_port"] == 52001


def test_get_factory_run_audit_bundle_missing_run_returns_404(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingRunService:
        async def get_run(self, run_id: str) -> None:
            return None

    monkeypatch.setattr(factory_router_module, "_get_service", lambda workspace: MissingRunService())
    state: Any = SimpleNamespace(settings=SimpleNamespace(workspace=tmp_path))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(factory_router_module.get_factory_run_audit_bundle_v2("missing", state=state))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == {
        "code": "RUN_NOT_FOUND",
        "message": "Run missing not found",
        "details": {},
    }


def test_get_factory_run_audit_bundle_partial_run_returns_quickly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit bundle must return quickly for partial runs with missing source dirs."""

    async def _exercise() -> dict[str, Any]:
        service = FactoryRunService(tmp_path)
        run = await service.create_run(
            FactoryConfig(name="partial-run", stages=["pm_planning", "chief_engineer_review", "director_dispatch"])
        )
        run.status = FactoryRunStatus.FAILED
        run.stages_completed = ["pm_planning", "chief_engineer_review"]
        run.stages_failed = ["director_dispatch"]
        run.metadata["current_stage"] = "director_dispatch"
        run.metadata["last_successful_stage"] = "chief_engineer_review"
        run.metadata["failure"] = {
            "stage": "director_dispatch",
            "code": "FACTORY_STAGE_FAILED",
            "detail": "Director timed out",
        }
        await service.store.save_run(run)

        await service._append_event(run.id, {"type": "stage_started", "stage": "pm_planning"})
        await service._append_event(run.id, {"type": "stage_completed", "stage": "pm_planning"})
        await service._append_event(run.id, {"type": "stage_started", "stage": "chief_engineer_review"})
        await service._append_event(run.id, {"type": "stage_completed", "stage": "chief_engineer_review"})
        await service._append_event(run.id, {"type": "stage_started", "stage": "director_dispatch"})
        await service._append_event(
            run.id,
            {
                "type": "stage_completed",
                "stage": "director_dispatch",
                "result": {"status": "failed", "artifacts": ["nonexistent/missing.json"]},
            },
        )

        monkeypatch.setattr(factory_router_module, "_get_service", lambda workspace: service)
        state: Any = SimpleNamespace(settings=SimpleNamespace(workspace=tmp_path))
        response = await factory_router_module.get_factory_run_audit_bundle_v2(run.id, limit=5, state=state)
        return response.model_dump(mode="json")

    payload = asyncio.run(_exercise())

    assert payload["run_id"].startswith("factory_")
    assert payload["status"] == "failed"
    assert payload["current_stage"] == "director_dispatch"
    assert payload["last_successful_stage"] == "chief_engineer_review"
    assert len(payload["events_tail"]) >= 1
    assert payload["summary_md"] is None


def test_get_factory_run_audit_bundle_partial_run_with_workspace_dispatch_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit bundle for a partial run should still include stage artifacts that resolve."""

    async def _exercise() -> dict[str, Any]:
        service = FactoryRunService(tmp_path)
        run = await service.create_run(
            FactoryConfig(
                name="partial-with-logs", stages=["pm_planning", "chief_engineer_review", "director_dispatch"]
            )
        )
        run.status = FactoryRunStatus.FAILED
        run.stages_completed = ["pm_planning", "chief_engineer_review"]
        run.stages_failed = ["director_dispatch"]
        await service.store.save_run(run)

        artifacts_dir = service.store.get_run_dir(run.id) / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / "dispatch.json").write_text(
            json.dumps({"dispatch": {"tasks": []}}),
            encoding="utf-8",
        )

        await service._append_event(run.id, {"type": "stage_started", "stage": "pm_planning"})
        await service._append_event(run.id, {"type": "stage_completed", "stage": "pm_planning"})

        monkeypatch.setattr(factory_router_module, "_get_service", lambda workspace: service)
        state: Any = SimpleNamespace(settings=SimpleNamespace(workspace=tmp_path))
        response = await factory_router_module.get_factory_run_audit_bundle_v2(run.id, limit=5, state=state)
        return response.model_dump(mode="json")

    payload = asyncio.run(_exercise())

    assert payload["status"] == "failed"
    assert len(payload["artifacts"]) >= 1
    assert payload["artifacts"][0]["name"] == "dispatch.json"


def test_director_partial_audit_bundle_includes_convergence_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """director_partial must expose blocking_phase, missing targets, taskboard state — not just qa_ran=False."""

    async def _exercise() -> dict[str, Any]:
        service = FactoryRunService(tmp_path)
        run = await service.create_run(
            FactoryConfig(
                name="director-partial",
                stages=["pm_planning", "chief_engineer_review", "director_dispatch", "quality_gate"],
            )
        )
        run.status = FactoryRunStatus.FAILED
        run.stages_completed = ["pm_planning", "chief_engineer_review"]
        run.stages_failed = ["director_dispatch"]
        run.metadata["current_stage"] = "director_dispatch"
        run.metadata["last_successful_stage"] = "pm_planning"
        run.metadata["failure"] = {
            "stage": "director_dispatch",
            "code": "FACTORY_STAGE_FAILED",
            "detail": "Director only executed 1/3 tasks",
        }
        run.metadata["summary_json"] = {
            "director": {"total": 3, "successes": 1, "failures": 2, "blocked": 0},
        }
        await service.store.save_run(run)

        await service._append_event(run.id, {"type": "stage_started", "stage": "pm_planning"})
        await service._append_event(run.id, {"type": "stage_completed", "stage": "pm_planning"})
        await service._append_event(run.id, {"type": "stage_started", "stage": "director_dispatch"})
        await service._append_event(
            run.id,
            {
                "type": "task_started",
                "stage": "director_dispatch",
                "task_id": "TASK-1",
                "taskboard": {"total": 3, "claimed": 1, "completed": 0, "failed": 0, "blocked": 0},
            },
        )
        await service._append_event(
            run.id,
            {
                "type": "task_completed",
                "stage": "director_dispatch",
                "task_id": "TASK-1",
                "taskboard": {"total": 3, "claimed": 1, "completed": 1, "failed": 0, "blocked": 0},
            },
        )
        await service._append_event(
            run.id,
            {
                "type": "task_failed",
                "stage": "director_dispatch",
                "task_id": "TASK-2",
                "taskboard": {"total": 3, "claimed": 2, "completed": 1, "failed": 1, "blocked": 0},
            },
        )
        await service._append_event(
            run.id,
            {
                "type": "stage_completed",
                "stage": "director_dispatch",
                "result": {"status": "failed", "total": 3, "successes": 1, "failures": 2},
            },
        )

        monkeypatch.setattr(factory_router_module, "_get_service", lambda workspace: service)
        state: Any = SimpleNamespace(settings=SimpleNamespace(workspace=tmp_path))
        response = await factory_router_module.get_factory_run_audit_bundle_v2(run.id, limit=20, state=state)
        return response.model_dump(mode="json")

    payload = asyncio.run(_exercise())

    assert payload["status"] == "failed"
    assert payload["current_stage"] == "director_dispatch"

    conv = payload.get("director_convergence")
    assert conv is not None, "director_partial bundle must include director_convergence"
    assert conv["qa_ran"] is False
    assert conv["blocking_phase"] == "director_dispatch"
    assert conv["missing_delivery_targets"] == ["quality_gate"]
    assert conv["director_summary"] == {"total": 3, "successes": 1, "failures": 2, "blocked": 0}

    tb_final = conv["taskboard_final"]
    assert tb_final.get("total") == 3
    assert tb_final.get("completed") == 1
    assert tb_final.get("failed") == 1

    bindings = conv["per_binding_task_status"]
    task_ids = {b["task_id"] for b in bindings}
    assert "TASK-1" in task_ids
    assert "TASK-2" in task_ids
    task1 = next(b for b in bindings if b["task_id"] == "TASK-1")
    task2 = next(b for b in bindings if b["task_id"] == "TASK-2")
    assert task1["status"] == "completed"
    assert task2["status"] == "failed"


def test_qa_completed_bundle_omits_convergence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When QA ran and run completed, director_convergence must be absent."""

    async def _exercise() -> dict[str, Any]:
        service = FactoryRunService(tmp_path)
        run = await service.create_run(FactoryConfig(name="clean-run", stages=["pm_planning", "quality_gate"]))
        run.status = FactoryRunStatus.COMPLETED
        run.stages_completed = ["pm_planning", "quality_gate"]
        await service.store.save_run(run)

        await service._append_event(run.id, {"type": "stage_started", "stage": "pm_planning"})
        await service._append_event(run.id, {"type": "stage_completed", "stage": "pm_planning"})
        await service._append_event(
            run.id,
            {
                "type": "stage_completed",
                "stage": "quality_gate",
                "result": {"passed": True},
            },
        )

        monkeypatch.setattr(factory_router_module, "_get_service", lambda workspace: service)
        state: Any = SimpleNamespace(settings=SimpleNamespace(workspace=tmp_path))
        response = await factory_router_module.get_factory_run_audit_bundle_v2(run.id, limit=5, state=state)
        return response.model_dump(mode="json")

    payload = asyncio.run(_exercise())

    assert payload["status"] == "completed"
    assert "director_convergence" not in payload
