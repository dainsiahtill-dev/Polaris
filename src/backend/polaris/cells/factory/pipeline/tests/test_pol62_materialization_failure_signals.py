"""Actual dispatch metadata survives StageResult and the existing audit chain."""

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from polaris.cells.factory.pipeline.internal.factory_run_service import FactoryRunService, StageResult
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.tests.test_materialization_controlled_verifiers import _run


@pytest.mark.asyncio
async def test_materialization_failure_producer_survives_stage_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    monkeypatch.setattr(executor, "_build_orchestration_service", Mock(return_value=object()))
    monkeypatch.setattr(executor, "_factory_role_evidence_cutoff_port", Mock(return_value=None))
    monkeypatch.setattr(
        executor,
        "_run_director_stage_materialization_quality_settle",
        AsyncMock(
            return_value={
                "ok": False,
                "reason": "materialization_verification_blocked",
                "detail": "collector denied",
            }
        ),
    )
    monkeypatch.setattr(executor, "_seal_director_stage_missing_tool_lifecycles", Mock(return_value={}))
    current = _run()
    result = await executor._execute_director_dispatch(current, {"project_id": "pol54", "factory_run_id": current.id})
    assert result.metadata["failure_class"] == "materialization_verification"
    assert result.metadata["error_code"] == "director.materialization_verification_failed"
    assert any(
        s["code"] == "director.materialization_verification_failed" and s["severity"] == "error"
        for s in result.metadata["signals"]
    )
    service = FactoryRunService(tmp_path, executor=executor)
    service._apply_stage_result_to_run(current, result, source_run=current, error=None)
    failure = current.metadata["failure"]
    assert failure["code"] == result.metadata["error_code"]
    assert failure["failure_class"] == "materialization_verification"
    assert failure["signals"] == result.metadata["signals"]
    assert (
        current.metadata["stage_results"]["director_dispatch"]["metadata"]["failure_class"] == failure["failure_class"]
    )
    from polaris.delivery.http.routers._factory_handlers.stage_ops.run_summary import _build_factory_audit_bundle

    bundle = _build_factory_audit_bundle(run=current, events=[], artifacts=[])
    assert bundle["failure"]["code"] == failure["code"]
    assert bundle["failure"]["failure_class"] == failure["failure_class"]
    assert bundle["failure"]["signals"] == failure["signals"]


def test_untyped_failure_prose_does_not_mint_signals(tmp_path: Path) -> None:
    service = FactoryRunService(tmp_path)
    current = _run()
    result = StageResult(
        stage="director_dispatch", status="failed", output="error_code=claimed; materialization failed"
    )
    service._apply_stage_result_to_run(current, result, source_run=current, error=None)
    assert current.metadata["failure"]["code"] == "FACTORY_STAGE_FAILED"
    assert "failure_class" not in current.metadata["failure"]
    assert "signals" not in current.metadata["failure"]
