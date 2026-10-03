"""A deferred planner result cannot stand in for committed source effect rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal import factory_workspace_quality_impl as quality
from polaris.cells.factory.pipeline.internal.factory_run_service import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.native_validation_session import NativeValidationSession
from polaris.cells.factory.pipeline.tests._characterization_helpers import _executor
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import (
    _projection,
    _real_effect,
    authored as authored,
    production,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "carrier", ["valid", "integer", "mapping", "string", "bad_row", "later_bad", "collection_int", "collection_none"]
)
async def test_actual_deterministic_producer_returns_committed_rows_for_native_validation(
    tmp_path: Path, authored: tuple, monkeypatch: pytest.MonkeyPatch, carrier: str
) -> None:
    portfolio, contract, _ = authored
    executor = _executor(tmp_path)
    run = FactoryRun(
        id=contract.run_id,
        config=FactoryConfig(name="physical-rows"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-03T00:00:00+00:00",
    )
    session = NativeValidationSession(
        workspace=tmp_path,
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
    )
    session.before_repair()
    attempt = production._setup_attempt(str(tmp_path))
    projection = _projection(portfolio, contract)
    owner = {"target_files": ["src/a.py"], "task_completion_projection": projection}
    monkeypatch.setattr(
        executor,
        "_claim_workspace_quality_repair_attempt",
        lambda **kwargs: (attempt.external_task_id, attempt.task_id, attempt, owner),
    )
    plan = {"tool_name": "planned_rule", "success": True, "result": {"status": "deferred_repair_effects_pending"}}
    monkeypatch.setattr(
        executor, "_apply_workspace_quality_repairs", lambda **kwargs: ([plan], {"source_tools": ["planned_rule"]})
    )
    monkeypatch.setattr(executor, "_director_stage_materialization_settle_commit_context", lambda **kwargs: {})
    committed = []

    async def commit(**kwargs: Any) -> list:
        assert kwargs["execution_attempt"] == attempt
        _, rows = await _real_effect(tmp_path, attempt=attempt)
        committed.extend(rows)
        if carrier == "collection_int":
            return 1
        if carrier == "collection_none":
            return None
        if carrier == "later_bad":
            return [
                {"success_count": 1, "failure_count": 0, "raw_results": rows, "results": rows},
                {"success_count": 1, "failure_count": 0, "raw_results": 1},
            ]
        raw = {"valid": rows, "integer": 1, "mapping": {}, "string": "bad", "bad_row": [1]}[carrier]
        return [{"success_count": 1, "failure_count": 0, "raw_results": raw, "results": rows}]

    monkeypatch.setattr("polaris.cells.roles.adapters.public.commit_materialization_deferred_repairs", commit)
    summary = {}
    try:
        rows, summary = await executor._apply_workspace_quality_deterministic_repairs(
            run=run, artifact_quality_errors=["src/a.py: source needs repair"], repair_attempt=1
        )
        if carrier != "valid":
            assert rows == ([{**committed[0], "success": True}] if carrier == "later_bad" else [])
            assert "physical_receipt_carrier_invalid" in summary["error"]
            assert summary["success"] is False
            assert "_pending_task_runtime_repair_attempt" not in summary
            assert summary["task_runtime_repair_attempt"]["settled"] is True
            assert summary["task_runtime_repair_attempt"]["outcome"] == "failed"
            if carrier == "later_bad":
                assert summary["committed_receipt_count"] == 1
                assert summary["task_runtime_repair_attempt"]["project_artifact_receipt_count"] == 3
            return
        assert rows == [{**committed[0], "success": True}]
        assert rows[0]["effect_receipt"]["receipt_outcome"] == "succeeded"
        session.candidate(pending=summary["_pending_task_runtime_repair_attempt"], results=rows)
        result = await session.run_command(["/bin/cat", "src/a.py"], 3)
        assert result["passed"] is True
        assert result["stdout_tail"] == "after\n"
    finally:
        pending = summary.get("_pending_task_runtime_repair_attempt")
        if pending:
            await quality._settle_pending_workspace_quality_repair_attempt(
                executor, pending, accepted=False, reason="physical-row-fixture-finished"
            )
        session.close()
