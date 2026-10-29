"""Factory preserves canonical preflight owner without unnecessary verifier effects."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    CommandResult,
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
)
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.tests._characterization_helpers import (
    _factory_stage_context,
    _with_task_runtime_authority,
)
from polaris.cells.roles.adapters.tests.test_director_preflight_failure_owner import (
    AUTHORITY_ERROR,
    preflight_summary,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "earlier_effect,failure_kind",
    [(False, "preflight"), (True, "preflight"), (False, "command_exit_1"), (False, "foreign_run")],
)
async def test_canonical_preflight_failure_skips_settle_only_without_earlier_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    earlier_effect: bool,
    failure_kind: str,
) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    run = FactoryRun(
        id="factory-preflight",
        config=FactoryConfig(name="preflight"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-13T00:00:00Z",
    )
    task = {
        "id": "TASK-1",
        "target_files": ["src/index.ts"],
        "scope_paths": ["src/index.ts"],
        "steps": [{"action": "implement", "target": "src/index.ts"}],
        "acceptance": ["real entry works"],
    }
    executor._write_json_artifact("tasks/plan.json", {"tasks": [task]})
    row = {
        "id": 1,
        "status": "failed",
        "workflow_run_id": "director-preflight",
        "metadata": {
            "external_task_id": "TASK-1",
            "factory_run_id": run.id,
            "adapter_result": {
                "primary_llm": preflight_summary(),
                "materialization_error_code": AUTHORITY_ERROR,
                "failure_class": FailureClassV1.BLUEPRINT_VERIFY_INVALID.value,
                "responsible_layer": "execution_control_plane",
                "write_tool_evidence": earlier_effect,
                "new_file_count": int(earlier_effect),
                "modified_file_count": 0,
                "failure_evidence": [
                    {
                        "schema_version": "failure_evidence.v1",
                        "failure_class": FailureClassV1.BLUEPRINT_VERIFY_INVALID.value,
                        "responsible_layer": "execution_control_plane",
                        "metadata": {"error_code": AUTHORITY_ERROR, "failure_stage": "director_context_preflight"},
                    }
                ],
            },
        },
    }
    if failure_kind == "command_exit_1":
        row["metadata"]["adapter_result"]["primary_llm"]["llm_calls"] = 1
        row["metadata"]["adapter_result"]["primary_llm"]["metadata"]["continuous_execution"]["turns_executed"] = 1
    elif failure_kind == "foreign_run":
        row["metadata"]["factory_run_id"] = "another-factory"
    dispatched = False

    async def execute_director_run(**kwargs):
        nonlocal dispatched
        dispatched = True
        if earlier_effect:
            (tmp_path / "src").mkdir()
            (tmp_path / "src/index.ts").write_text("export const value = 1;\n", encoding="utf-8")
        return CommandResult(
            run_id="director-preflight",
            status="failed",
            message="canonical task failed",
            metadata={"canonical_authoritative": True, "terminal_source": "task_runtime.execution_fact"},
        )

    def stats():
        return {
            "total": 1,
            "pending": 0,
            "ready": int(not dispatched),
            "in_progress": 0,
            "completed": 0,
            "failed": int(dispatched),
            "blocked": 0,
        }

    monkeypatch.setattr(
        executor, "_build_orchestration_service", lambda _: SimpleNamespace(execute_director_run=execute_director_run)
    )
    monkeypatch.setattr(
        executor, "_materialize_pm_plan_taskboard", lambda *a, **k: {"created_count": 0, "binding_failures": []}
    )
    monkeypatch.setattr(executor, "_chief_engineer_handoff_signals_for_director", lambda *a, **k: [])
    monkeypatch.setattr(executor, "_read_taskboard_stats", stats)
    monkeypatch.setattr(executor, "_query_observable_task_rows", lambda **_: ([row] if dispatched else [], None))
    monkeypatch.setattr(executor, "_read_claimable_director_task_ids", lambda **_: ["TASK-1"])
    monkeypatch.setattr(executor, "_resolve_director_binding_fanout", lambda _: [])
    monkeypatch.setattr(executor, "_validate_director_binding_coverage", lambda **_: (False, []))
    monkeypatch.setattr(executor, "_director_provider_health_failure_signal", lambda: None)
    monkeypatch.setattr(
        executor,
        "_canonical_factory_projection",
        lambda *a: _with_task_runtime_authority({"task_boundary_verdicts": []}, incomplete_task_ids=("TASK-1",)),
    )
    settle = AsyncMock(
        return_value={
            "ok": False,
            "reason": "command_failed",
            "detail": "test exit 1",
            "failed_required_modalities": ["test"],
            "missing_required_modalities": [],
        }
    )
    monkeypatch.setattr(executor, "_run_director_stage_materialization_quality_settle", settle)
    monkeypatch.setattr(executor, "_seal_director_stage_missing_tool_lifecycles", Mock(return_value={}))
    result = await executor._execute_director_dispatch(
        run,
        _factory_stage_context(
            {"execution_mode": "serial", "max_workers": 1, "director_max_rounds": 1, "timeout": 120}
        ),
    )
    assert dispatched and result.status == "failed"
    if failure_kind != "preflight":
        assert settle.await_count == 1
        assert result.metadata["error_code"] == "director.materialization_verification_failed"
        assert result.metadata["director_stage_materialization_quality_settle"]["failed_required_modalities"] == [
            "test"
        ]
        return
    assert result.metadata["error_code"] == AUTHORITY_ERROR
    assert result.metadata["failure_class"] == FailureClassV1.BLUEPRINT_VERIFY_INVALID.value
    payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
    assert payload["error_code"] == AUTHORITY_ERROR
    assert payload["canonical_authority"]["authorized"] is False
    if earlier_effect:
        assert settle.await_count == 1
        assert result.metadata["director_stage_materialization_quality_settle"]["failed_required_modalities"] == [
            "test"
        ]
        assert payload["materialization_verification"]["missing_required_modalities"] == []
    else:
        assert settle.await_count == 0
        skipped = result.metadata["director_stage_materialization_quality_settle"]
        assert skipped["reason"] == "settle_not_applicable"
        assert skipped["not_applicable_reason"] == "preflight_blocked_without_execution_or_effects"
        assert "materialization_verification" not in payload
