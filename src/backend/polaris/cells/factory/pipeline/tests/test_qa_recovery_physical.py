"""QA repair consumes real Director effects and isolated verifier execution."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal import factory_qa_recovery as recovery
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.internal.native_validation_session import NativeValidationSession
from polaris.cells.factory.pipeline.tests.test_materialization_llm_continuation import _entrypoint_source_effect
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import (
    _projection,
    _real_effect,
    authored as _authored,
)
from polaris.cells.roles.adapters.internal.director.quality_gate._candidate_guard import (
    DirectorQualityRepairCandidateGuard,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService

authored = _authored


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["pass", "retry", "cancel", "foreign_owner", "verifier_failure", "heartbeat_failure"])
async def test_owned_qa_repair_uses_real_effect_then_verifies_or_drains_cancelled_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authored: tuple[Any, Any, Any],
    case: str,
) -> None:
    portfolio, contract, _fs = authored
    executor = OrchestrationStageExecutor(tmp_path)
    authority_port = SimpleNamespace(mint_authority_binding=lambda _role: object())
    monkeypatch.setattr(executor, "_factory_role_evidence_cutoff_port", lambda _context: authority_port)

    async def authorized(_port: Any, role: str, operation: Any, **_kwargs: Any) -> Any:
        assert _port is authority_port and role == "director"
        return await operation()

    monkeypatch.setattr(executor, "_call_with_factory_role_evidence_authority", authorized)
    run = FactoryRun(
        id="run",
        config=FactoryConfig(name="physical QA"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-13T00:00:00+00:00",
    )
    projection = _projection(portfolio, contract)
    command = [
        "/usr/bin/python3",
        "-c",
        "from pathlib import Path; assert Path('src/a.py').read_text(encoding='utf-8') == 'after\\n'",
    ]
    monkeypatch.setattr(executor, "_workspace_quality_commands", lambda _context, *, run=None: [command])
    monkeypatch.setattr(recovery, "owned_finding_groups", lambda *_args: {"DEO-LIFECYCLE": ["src/a.py"]})

    def session(*_args: Any, **_kwargs: Any) -> NativeValidationSession:
        return NativeValidationSession(
            workspace=tmp_path,
            project_id=contract.project_id,
            run_id="run",
            completion_contract_hash=contract.contract_hash,
        )

    monkeypatch.setattr(NativeValidationSession, "from_factory", session)
    attempts = []
    heartbeat_tasks = []
    settlements = []
    original_settle = recovery.quality._settle_pending_workspace_quality_repair_attempt

    async def settle(*args: Any, **kwargs: Any) -> Any:
        result = await original_settle(*args, **kwargs)
        settlements.append(result)
        return result

    monkeypatch.setattr(recovery.quality, "_settle_pending_workspace_quality_repair_attempt", settle)

    async def repair(**_kwargs: Any) -> Any:
        if attempts and (case != "retry" or len(attempts) >= 2):
            return [], {"attempted": False}
        guard = await DirectorQualityRepairCandidateGuard.capture(
            workspace=tmp_path,
            candidate_id="independent-qa-repair",
            target_files=["src/a.py"],
        )
        if case == "retry":
            attempt, effects = await _entrypoint_source_effect(
                tmp_path,
                "src/a.py",
                "wrong\n" if not attempts else "after\n",
                f"qa-retry-{len(attempts)}",
                attempt=attempts[-1] if attempts else None,
            )
        else:
            attempt, effects = await _real_effect(tmp_path)
        await guard.seal_effect()
        attempts.append(attempt)
        stop = asyncio.Event()
        failures = [{"code": "lease_rejected"}] if case == "heartbeat_failure" else []
        heartbeat = asyncio.create_task(
            recovery.quality._run_workspace_quality_repair_heartbeat(
                object(),
                stop=stop,
                failures=failures,
                context_summary="physical qa test",
            )
        )
        heartbeat_tasks.append(heartbeat)
        # The production repair adapter projects ToolBatch status to success;
        # keep the real owner-sealed effects intact at that boundary.
        normalized = [{**item, "success": item.get("status") == "success"} for item in effects]
        return normalized, {
            "_pending_task_runtime_repair_attempt": {
                "task_id": "FOREIGN" if case == "foreign_owner" else "DEO-LIFECYCLE",
                "task_row_id": attempt.task_id,
                "execution_attempt": attempt,
                "task_completion_projection": projection,
                "mutation_committed": True,
                "candidate_guard": guard,
                "heartbeat_task": heartbeat,
                "heartbeat_stop": stop,
                "heartbeat_failures": failures,
            }
        }

    monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", repair)
    original_command = NativeValidationSession.run_command
    checks = []

    async def run_command(self: Any, argv: list[str], timeout: float) -> Any:
        if case == "cancel":
            raise asyncio.CancelledError
        if case == "verifier_failure":
            argv = ["/usr/bin/python3", "-c", "raise SystemExit(7)"]
        result = await original_command(self, argv, timeout)
        checks.append(result)
        return result

    monkeypatch.setattr(NativeValidationSession, "run_command", run_command)

    async def final_checks(*_args: Any) -> tuple[bool, str]:
        fresh = session()
        try:
            result = await fresh.run_command(command, 10.0)
            return result["passed"], "runtime/qa/workspace-validation.json"
        finally:
            fresh.close()

    monkeypatch.setattr(executor, "_run_workspace_quality_checks", final_checks)
    kwargs = {
        "run": run,
        "context": {},
        "qa_payload": {"source": "qa_adapter_v3", "passed": False, "critical_issues": ["src/a.py: wrong behavior"]},
        "report_hash": "original-qa",
    }
    if case == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await recovery.repair_qa_role_findings(executor, **kwargs)
    elif case in {"pass", "retry"}:
        proof = await recovery.repair_qa_role_findings(executor, **kwargs)
        assert checks[0]["passed"] is False  # Actual pre-repair source baseline.
        candidate_index = 3 if case == "retry" else 1
        assert checks[candidate_index]["passed"] is True, checks[candidate_index].get("error")
        assert settlements[-1]["artifact_receipt_error"] == ""
        assert settlements[-1]["project_artifact_receipt_count"] == len(projection["owned_artifacts"])
        assert settlements[-1]["outcome"] == "completed", settlements[-1]
        assert proof is not None and proof.matches(str(tmp_path), "run"), (checks, settlements)
        assert proof.owner_task_ids == ("DEO-LIFECYCLE",)
        assert proof.effect_receipt_ids
        if case == "retry":
            assert len(checks) == 5 and checks[1]["exit_code"] == 1
            assert settlements[0]["outcome"] == "failed"
            assert settlements[0]["candidate_guard_receipt"]["status"] == "restored"
            assert attempts[0].task_id == attempts[1].task_id
        else:
            assert len(checks) == 3 and all(item["exit_code"] == 0 for item in checks[1:])
    else:
        assert await recovery.repair_qa_role_findings(executor, **kwargs) is None
    if case not in {"pass", "retry"}:
        assert (tmp_path / "src/a.py").read_text(encoding="utf-8") == "before\n"
        if case != "cancel":
            assert settlements[0]["candidate_guard_receipt"]["status"] == "restored"
    assert all(task.done() for task in heartbeat_tasks)
    assert len(attempts) == (0 if case == "cancel" else 2 if case == "retry" else 1)
    rows = TaskRuntimeService(str(tmp_path)).list_task_rows(include_terminal=True)
    assert not any(str(row.get("status") or "") in {"in_progress", "claimed"} for row in rows)


def test_ambiguous_qa_observation_never_selects_an_arbitrary_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    tasks = [{"id": "TASK-1"}, {"id": "TASK-2"}]

    def restore(_executor: Any, *, run_id: str, task_id: str, canonical_task: Any) -> Any:
        return {
            "task_completion_projection": {
                "run_id": run_id,
                "task_id": task_id,
                "owned_artifacts": [{"owner_task_id": task_id, "path": f"src/{task_id}.py"}],
            }
        }

    monkeypatch.setattr(recovery.quality, "_workspace_quality_frozen_ce_owner_task", restore)
    executor = SimpleNamespace(_load_pm_plan_tasks=lambda _ref: tasks, _task_id=lambda task, _index: task["id"])
    with pytest.raises(ValueError, match="exact_owner_missing_or_ambiguous"):
        recovery.owned_finding_groups(executor, SimpleNamespace(id="run"), ("wrong behavior",))
    assert recovery.owned_finding_groups(executor, SimpleNamespace(id="run"), ("src/TASK-2.py: wrong behavior",)) == {
        "TASK-2": ["src/TASK-2.py"]
    }
