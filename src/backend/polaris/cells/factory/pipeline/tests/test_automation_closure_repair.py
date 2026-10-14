"""One owner repair loop must not confuse progress with delivery completion."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal import (
    factory_materialization_impl as materialization,
    factory_workspace_quality_impl as quality,
)
from polaris.cells.factory.pipeline.tests.test_materialization_contract_verifiers import _fixture
from polaris.cells.factory.pipeline.tests.test_materialization_llm_continuation import (
    _entrypoint_source_effect,
    bound_director_scope as bound_director_scope,
)
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import _projection
from polaris.cells.roles.adapters.internal.director.quality_gate._candidate_guard import (
    DirectorQualityRepairCandidateGuard,
)
from polaris.cells.runtime.execution_broker.public import project_verification as broker


@pytest.mark.asyncio
@pytest.mark.parametrize("progress", [True, False])
async def test_four_real_committed_progress_candidates_continue_within_original_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, progress: bool
) -> None:
    executor, run, port = _fixture(tmp_path, monkeypatch, task_id="DEO-LIFECYCLE", run_id="run", stdlib_verifier=True)
    (executor.workspace / "tests/test_output.py").write_text(
        "import unittest, importlib.machinery\n"
        "module = importlib.machinery.SourcelessFileLoader('built_module', 'src/module.pyc').load_module()\n"
        "class ThresholdTests(unittest.TestCase):\n"
        + "".join(
            f"    def test_threshold_{value}(self): self.assertGreaterEqual(module.value, {value})\n"
            for value in range(8, 12)
        ),
        encoding="utf-8",
    )
    contract = port.completion_contract
    for artifact in contract.obligations.artifacts:
        broker.record_project_artifact(
            broker.RecordProjectArtifactCommandV1(
                workspace=str(executor.workspace),
                project_id=contract.project_id,
                run_id=run.id,
                completion_contract_hash=contract.contract_hash,
                obligation_id=artifact.obligation_id,
                owner_task_id=artifact.owner_task_id,
                path=artifact.path,
            )
        )
    attempts: list[Any] = []
    frontiers = []
    classify = quality._workspace_quality_test_frontier_reduced

    def observed_frontier(before, after, **kwargs):
        result = classify(before, after, **kwargs)
        if result:
            frontiers.append((deepcopy(before), deepcopy(after), kwargs))
        return result

    monkeypatch.setattr(quality, "_workspace_quality_test_frontier_reduced", observed_frontier)
    initial = await executor._collect_director_stage_materialization_diagnostics(run=run)
    observations = [initial.to_dict()]

    async def repair(_executor: Any, **kwargs: Any) -> Any:
        ordinal = len(attempts)
        guard = await DirectorQualityRepairCandidateGuard.capture(
            workspace=executor.workspace, candidate_id=f"progress-{ordinal}", target_files=["src/module.py"]
        )
        attempt, rows = await _entrypoint_source_effect(
            executor.workspace,
            "src/module.py",
            f"value = {8 + ordinal if progress else 6}\n",
            f"progress-{ordinal}",
            attempt=attempts[-1] if attempts else None,
        )
        await guard.seal_effect()
        attempts.append(attempt)
        return rows, {
            "_pending_task_runtime_repair_attempt": {
                "task_id": attempt.external_task_id,
                "task_row_id": attempt.task_id,
                "execution_attempt": attempt,
                "mutation_committed": True,
                "candidate_guard": guard,
                "task_completion_projection": _projection(port.blueprint_portfolio, contract),
            }
        }

    monkeypatch.setattr(quality, "_apply_workspace_quality_llm_repairs", repair)
    monkeypatch.setattr(executor, "_factory_deadline_remaining_seconds", lambda context: 5400.0)
    result = await materialization._continue_materialization_verifier_repair(
        executor,
        run=run,
        context={},
        result={"ok": False, "post_commit_diagnostics": list(initial)},
        sessions=[],
        observations=observations,
    )
    import json

    (tmp_path / "frontier-observations.json").write_text(json.dumps(observations, default=str), encoding="utf-8")
    assert len(attempts) == (4 if progress else 2), result
    assert result["ok"] is progress
    assert all(item.external_task_id == "DEO-LIFECYCLE" for item in attempts)
    assert len({item.task_id for item in attempts}) == 1
    assert (executor.workspace / "src/module.py").read_text(encoding="utf-8") == (
        "value = 11\n" if progress else "value = 7\n"
    )

    if not progress:
        assert result["convergence_stop_reason"] == "two_nonprogress_repairs_without_verified_progress"
    else:
        assert len(frontiers) >= 3
        before, after, seals = frontiers[0]
        assert classify(before, after, **seals)
        assert not classify(before, after)  # Forged summary has no owner seal.
        changed = deepcopy(after)
        test_row = next(row for row in changed if "unittest" in row.get("command", []))
        test_row["command"] = ["python", "-m", "unittest", "discover", "-s", "other"]
        assert not classify(before, changed, **seals)
        changed = deepcopy(after)
        test_row = next(row for row in changed if "unittest" in row.get("command", []))
        for key in ("diagnostic_excerpt", "stdout_tail", "stderr_tail"):
            test_row[key] = "Ran 4 tests in 0.01s\nFAILED (failures=1)\n"
        assert not classify(before, changed, **seals)
        # Removing cases changes the real current test-input hash. Even a real
        # passing command/receipt cannot qualify that as this test frontier.
        (executor.workspace / "tests/test_output.py").write_text(
            "import unittest\nclass RemovedCases(unittest.TestCase):\n    def test_dummy(self): pass\n",
            encoding="utf-8",
        )
        test_artifact = next(item for item in contract.obligations.artifacts if item.path == "tests/test_output.py")
        broker.record_project_artifact(
            broker.RecordProjectArtifactCommandV1(
                workspace=str(executor.workspace),
                project_id=contract.project_id,
                run_id=run.id,
                completion_contract_hash=contract.contract_hash,
                obligation_id=test_artifact.obligation_id,
                owner_task_id=test_artifact.owner_task_id,
                path=test_artifact.path,
            )
        )
        deleted = await executor._collect_director_stage_materialization_diagnostics(run=run)
        assert not classify(
            before,
            deleted.verifier_results,
            before_receipts=seals["before_receipts"],
            after_receipts=deleted._owner_receipts,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["deadline", "cancel"])
async def test_terminal_budget_or_cancel_never_starts_a_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    executor, run, _port = _fixture(tmp_path, monkeypatch)
    calls = []

    async def forbidden(*args: Any, **kwargs: Any) -> Any:
        calls.append(True)
        raise AssertionError("terminal condition must not invoke Director")

    async def cancelled(_checker: Any) -> str:
        return "user_cancelled" if reason == "cancel" else ""

    monkeypatch.setattr(quality, "_apply_workspace_quality_llm_repairs", forbidden)
    monkeypatch.setattr(
        executor, "_factory_deadline_remaining_seconds", lambda context: 0 if reason == "deadline" else 5400
    )
    monkeypatch.setattr(executor, "_quality_gate_abort_reason", cancelled)
    result = await materialization._continue_materialization_verifier_repair(
        executor,
        run=run,
        context={},
        result={"ok": False, "post_commit_diagnostics": ["ordinary test failed"]},
        sessions=[],
        observations=[],
    )
    assert not calls and result["ok"] is False
    assert result["convergence_stop_reason"] in {"quality_repair_deadline_insufficient", "quality_repair_cancelled"}
