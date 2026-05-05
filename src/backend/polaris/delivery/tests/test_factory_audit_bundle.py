"""Tests for Factory audit bundle assembly."""

from __future__ import annotations

import asyncio
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
        await service.store.save_run(run)
        artifact_path = service.store.get_run_dir(run.id) / "artifacts" / "evidence.json"
        artifact_path.write_text('{"ok": true}\n', encoding="utf-8")
        await service._append_event(run.id, {"type": "stage_started", "stage": "pm_planning"})
        await service._append_event(run.id, {"type": "stage_completed", "stage": "pm_planning"})

        monkeypatch.setattr(factory_router_module, "_get_service", lambda workspace: service)
        state: Any = SimpleNamespace(settings=SimpleNamespace(workspace=tmp_path))
        return await factory_router_module.get_factory_run_audit_bundle(run.id, limit=1, state=state)

    payload = asyncio.run(_exercise())

    assert payload["run_id"].startswith("factory_")
    assert payload["events_tail"][0]["type"] == "stage_completed"
    assert payload["artifacts"][0]["name"] == "evidence.json"
    assert payload["summary_md"] == "# Summary"
    assert payload["summary_json"] == {"status": "PENDING"}
    assert payload["evidence_counts"]["events_total"] == 2
    assert payload["evidence_counts"]["artifacts"] == 1


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
        asyncio.run(factory_router_module.get_factory_run_audit_bundle("missing", state=state))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Run missing not found"
