"""Stage continuation orchestration units; no provider or physical qualification."""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal import (
    factory_materialization_impl as materialization,
    factory_workspace_quality_impl as quality,
)
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor

_BOUND_DIRECTOR = ContextVar("materialization_unit_bound_director", default=False)


@pytest.fixture(autouse=True)
def bound_director_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    port = SimpleNamespace(mint_authority_binding=lambda _role: object())

    async def bound(_port: Any, role: str, operation: Any, **_kwargs: Any) -> Any:
        assert _port is port and role == "director"
        token = _BOUND_DIRECTOR.set(True)
        try:
            return await operation()
        finally:
            _BOUND_DIRECTOR.reset(token)

    monkeypatch.setattr(OrchestrationStageExecutor, "_factory_role_evidence_cutoff_port", staticmethod(lambda _c: port))
    monkeypatch.setattr(OrchestrationStageExecutor, "_call_with_factory_role_evidence_authority", staticmethod(bound))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "residual, recover_on_retry", [([], False), (["npm test still fails"], False), (["tsx dependency missing"], True)]
)
async def test_uncovered_stage_failure_uses_director_and_verifies_before_accepting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, residual: list[str], recover_on_retry: bool
) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    source = tmp_path / "package.json"
    source.write_text("before", encoding="utf-8")
    failures = ["artifact_quality_error: npm test failed: package.json scripts.test discovered zero tests"]
    run = FactoryRun(
        id="continuation-unit",
        config=FactoryConfig(name="continuation"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-12T00:00:00Z",
    )
    session = SimpleNamespace(
        before_repair=lambda: None,
        candidate=lambda **_k: None,
        restored=lambda: None,
        close=lambda: None,
    )

    async def deterministic(_executor: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs["sessions"].append(session)
        return {"ok": False, "reason": "materialization_verifier_residual", "post_commit_diagnostics": failures}

    class Guard:
        def accept(self, **_kwargs: Any) -> dict[str, Any]:
            return {"status": "accepted"}

        async def rollback(self, **_kwargs: Any) -> dict[str, Any]:
            source.write_text("before", encoding="utf-8")
            return {"status": "restored"}

    calls = 0

    async def director(_executor: Any, **_kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        nonlocal calls
        calls += 1
        assert _BOUND_DIRECTOR.get(), "repair provider invocation lacks the Factory Director request binding"
        if recover_on_retry and calls > 1:
            assert _kwargs["context"]["director_quality_repair"]["candidate_rejection_errors"] == residual
        source.write_text("candidate", encoding="utf-8")
        return [{"tool": "edit_file"}], {
            "attempted": True,
            "success": True,
            "_pending_task_runtime_repair_attempt": {
                "task_id": "original-owner",
                "task_row_id": "1",
                "execution_attempt": SimpleNamespace(session_id="owned-attempt"),
                "mutation_committed": True,
                "candidate_guard": Guard(),
            },
        }

    async def collect(**_kwargs: Any) -> materialization.MaterializationDiagnostics:
        assert source.read_text(encoding="utf-8") == "candidate"
        return materialization.MaterializationDiagnostics(
            [] if recover_on_retry and calls > 1 else residual,
            static_evidence={"scan_mode": "pure_static", "scan_complete": True},
        )

    monkeypatch.setattr(materialization, "_run_materialization_settle_with_session", deterministic)
    monkeypatch.setattr(quality, "_apply_workspace_quality_llm_repairs", director)
    monkeypatch.setattr(quality, "_record_workspace_quality_repair_artifact_receipts", lambda _pending: ())
    monkeypatch.setattr(executor, "_collect_director_stage_materialization_diagnostics", collect)
    monkeypatch.setattr(executor, "_settle_director_stage_materialization_attempt", lambda **_k: {"success": True})
    result = await materialization._run_director_stage_materialization_quality_settle(
        executor, run=run, stage_status="failed", error_code="director.canonical_task_boundary_missing"
    )
    resolved = not residual or recover_on_retry
    assert result["ok"] is resolved
    assert source.read_text(encoding="utf-8") == ("candidate" if resolved else "before")
    assert result["validation_observations"][-1]["diagnostics"] == ([] if resolved else residual)
    assert "_pending_task_runtime_repair_attempt" not in str(result)


@pytest.mark.asyncio
async def test_admission_blocker_does_not_invoke_director(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    run = FactoryRun(
        id="continuation-unit",
        config=FactoryConfig(name="continuation"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-12T00:00:00Z",
    )

    async def blocked(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "reason": "materialization_verification_blocked"}

    async def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("unqualified input must not reach provider repair")

    monkeypatch.setattr(materialization, "_run_materialization_settle_with_session", blocked)
    monkeypatch.setattr(quality, "_apply_workspace_quality_llm_repairs", forbidden)
    result = await materialization._run_director_stage_materialization_quality_settle(
        executor, run=run, stage_status="failed", error_code="director.canonical_task_boundary_missing"
    )
    assert result["ok"] is False
    assert result["reason"] == "materialization_verification_blocked"


@pytest.mark.asyncio
async def test_provider_success_without_effect_does_not_clear_verifier_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    run = FactoryRun(
        id="continuation-unit",
        config=FactoryConfig(name="continuation"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-12T00:00:00Z",
    )
    failures = ["artifact_quality_error: npm test failed: package.json scripts.test discovered zero tests"]

    async def unresolved(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "reason": "materialization_verifier_residual", "post_commit_diagnostics": failures}

    async def no_effect(*_args: Any, **_kwargs: Any) -> tuple[list[Any], dict[str, Any]]:
        assert _BOUND_DIRECTOR.get(), "repair provider invocation lacks the Factory Director request binding"
        return [], {"attempted": True, "success": True}

    monkeypatch.setattr(materialization, "_run_materialization_settle_with_session", unresolved)
    monkeypatch.setattr(quality, "_apply_workspace_quality_llm_repairs", no_effect)
    result = await materialization._run_director_stage_materialization_quality_settle(
        executor, run=run, stage_status="failed", error_code="director.canonical_task_boundary_missing"
    )
    assert result["ok"] is False
    assert result["post_commit_diagnostics"] == failures


@pytest.mark.asyncio
async def test_unproven_process_drain_does_not_settle_or_rollback_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.kernelone.process import ProcessTreeDrainError

    executor = OrchestrationStageExecutor(tmp_path)
    run = FactoryRun(
        id="continuation-unit",
        config=FactoryConfig(name="continuation"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-12T00:00:00Z",
    )
    session = SimpleNamespace(before_repair=lambda: None, candidate=lambda **_k: None, close=lambda: None)

    async def unresolved(_executor: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs["sessions"].append(session)
        return {"ok": False, "reason": "materialization_verifier_residual", "post_commit_diagnostics": ["test failure"]}

    async def director(*_args: Any, **_kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        assert _BOUND_DIRECTOR.get(), "repair provider invocation lacks the Factory Director request binding"
        return [{"tool": "edit_file"}], {"_pending_task_runtime_repair_attempt": {"mutation_committed": True}}

    async def undrained(**_kwargs: Any) -> Any:
        raise ProcessTreeDrainError("physical execution is still active")

    async def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("pending physical execution must not authorize settlement or rollback")

    monkeypatch.setattr(materialization, "_run_materialization_settle_with_session", unresolved)
    monkeypatch.setattr(quality, "_apply_workspace_quality_llm_repairs", director)
    monkeypatch.setattr(quality, "_record_workspace_quality_repair_artifact_receipts", lambda _pending: ())
    monkeypatch.setattr(quality, "_settle_pending_workspace_quality_repair_attempt", forbidden)
    monkeypatch.setattr(executor, "_collect_director_stage_materialization_diagnostics", undrained)
    with pytest.raises(ProcessTreeDrainError, match="physical execution is still active"):
        await materialization._run_director_stage_materialization_quality_settle(
            executor, run=run, stage_status="failed", error_code="director.canonical_task_boundary_missing"
        )
