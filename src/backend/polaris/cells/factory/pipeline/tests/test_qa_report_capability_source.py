"""QA report facts reuse the canonical task lineage, not a Director write context."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.control_plane.run_ledger.public import AppendRunLedgerEventCommandV1, append_run_ledger_event
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.internal.run_ledger import load_run_ledger_projection


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_token", [False, True])
@pytest.mark.parametrize("context_project", ["owned-project", None, "foreign-project"])
async def test_qa_report_uses_exact_committed_boundary_metadata_without_repair_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, foreign_token: bool, context_project: str | None
) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path), streams=fact_stream_bootstrap_streams(), maintenance_reason="qa_report_source_test"
        )
    )
    task_id, child_run = "TASK-QA", "director-qa-child"
    token = {
        "task_id": "TASK-FOREIGN" if foreign_token else task_id,
        "run_id": child_run,
        "project_id": "owned-project",
        "capability_audit": {"ok": True, "issues": []},
        "gate_policy": {"required_evidence_modalities": []},
    }
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id=child_run,
            event={
                "event_type": "task_boundary_verdict",
                "run_id": child_run,
                "task_id": task_id,
                "job_token": token,
                "task_boundary_verdict": {
                    "task_id": task_id,
                    "run_id": child_run,
                    "ok": True,
                    "status": "completed_verified",
                },
            },
        )
    )
    executor = OrchestrationStageExecutor(tmp_path)
    run = FactoryRun(
        id="factory-qa-parent",
        config=FactoryConfig(name="QA report"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-12T00:00:00+00:00",
    )
    monkeypatch.setattr(executor, "_canonical_qa_commit_identity", lambda **_kw: (task_id, child_run))
    monkeypatch.setattr(
        executor, "_canonical_factory_projection", lambda *_args: load_run_ledger_projection(tmp_path, run_id=child_run)
    )
    monkeypatch.setattr(executor, "_load_pm_plan_tasks", lambda *_args: [])
    captured = []

    def commit(command: object) -> SimpleNamespace:
        captured.append(command)
        return SimpleNamespace(metadata={"qa_verdict_committed": False}, verdict="EVIDENCE_ONLY", ok=False)

    monkeypatch.setattr("polaris.cells.qa.audit_verdict.public.commit_qa_role_verdict", commit)
    result = await executor._commit_qa_role_report_authority(
        run=run,
        context={"project_id": context_project} if context_project else {},
        qa_payload={"passed": True, "verdict": "PASS"},
    )
    assert result["success"] is False
    if foreign_token or context_project == "foreign-project":
        assert captured == []
        assert result["reason"] == "canonical_qa_source_token_identity_mismatch"
    else:
        assert len(captured) == 1
        assert captured[0].job_token == token
        assert captured[0].run_id == child_run
        assert captured[0].task_id == task_id
