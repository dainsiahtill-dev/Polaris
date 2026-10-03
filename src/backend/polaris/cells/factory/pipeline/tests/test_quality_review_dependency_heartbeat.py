"""Native Factory regression proofs for preparation freeze and abandoned leases."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal import factory_workspace_quality_impl as quality_impl
from polaris.cells.factory.pipeline.internal.factory_run_service import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.native_validation_session import NativeValidationSession
from polaris.cells.factory.pipeline.tests._characterization_helpers import _executor
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import (
    _projection,
    _real_effect,
    authored as authored,
    production,
)
from polaris.cells.roles.adapters.internal.director.quality_gate._candidate_guard import (
    DirectorQualityRepairCandidateGuard,
)
from polaris.kernelone.process import ProcessTreeDrainError


def _quality_case(tmp_path: Path, authored: tuple, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, FactoryRun, dict]:
    portfolio, contract, _ = authored
    executor = _executor(tmp_path)
    run = FactoryRun(
        id=contract.run_id,
        config=FactoryConfig(name="review-lifecycle"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-04T00:00:00+00:00",
    )
    run.metadata["stage_results"] = {
        "chief_engineer_review": {"status": "success", "artifacts": [portfolio.portfolio_path]}
    }
    monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda _run, _context: None)
    monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda _context: None)
    return (
        executor,
        run,
        {
            "project_id": contract.project_id,
            "workspace_quality_repair_max_rounds": 1,
            "workspace_validation_install_dependencies": False,
            "quality_commands": [
                [
                    "/bin/sh",
                    "-c",
                    "test $(cat src/a.py) = after || { echo 'src/a.py: candidate not repaired' >&2; exit 1; }",
                ]
            ],
        },
    )


async def _committed_candidate(tmp_path: Path, authored: tuple) -> tuple[list[dict], dict]:
    portfolio, contract, _ = authored
    guard = await DirectorQualityRepairCandidateGuard.capture(
        workspace=tmp_path, candidate_id="review-candidate", target_files=["src/a.py"]
    )
    attempt, rows = await _real_effect(tmp_path)
    await guard.seal_effect()
    return rows, {
        "attempted": True,
        "success": True,
        "write_tool_evidence": True,
        "_pending_task_runtime_repair_attempt": {
            "task_id": attempt.external_task_id,
            "task_row_id": attempt.task_id,
            "execution_attempt": attempt,
            "candidate_guard": guard,
            "mutation_committed": True,
            "task_completion_projection": _projection(portfolio, contract),
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["none", "source_during_prepare", "dependency_after_freeze"])
@pytest.mark.parametrize("initial_dependency", [True, False])
async def test_candidate_dependencies_freeze_after_native_preparation(
    tmp_path: Path, authored: tuple, monkeypatch: pytest.MonkeyPatch, attack: str, initial_dependency: bool
) -> None:
    executor, run, context = _quality_case(tmp_path, authored, monkeypatch)
    dependency = tmp_path / "node_modules" / "fixture.txt"
    if initial_dependency:
        dependency.parent.mkdir()
        dependency.write_text("prepared\n", encoding="utf-8")
    prepare = [
        "/bin/sh",
        "-c",
        "if test $(cat src/a.py) = after; then mkdir -p node_modules; printf 'prepared\\n' > node_modules/fixture.txt; fi",
    ]
    context["quality_commands"][0][-1] += "; test $(cat node_modules/fixture.txt) = prepared"
    if attack == "source_during_prepare":
        prepare[-1] += "; if test $(cat src/a.py) = after; then printf 'unreceipted\\n' > src/a.py; fi"
    monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda _commands, _context: [prepare])

    async def repair(**_kwargs: object) -> tuple:
        return await _committed_candidate(tmp_path, authored)

    monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", repair)
    original_candidate = NativeValidationSession.candidate

    def open_candidate(session: NativeValidationSession, **kwargs: Any) -> None:
        original_candidate(session, **kwargs)
        if attack == "dependency_after_freeze":
            dependency.write_text("unapproved drift\n", encoding="utf-8")

    monkeypatch.setattr(NativeValidationSession, "candidate", open_candidate)
    passed, artifact = await executor._run_workspace_quality_checks(run, context)
    payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
    assert passed is (attack == "none"), [
        (row["phase"], row.get("exit_code"), row.get("error"), row.get("stderr_tail")) for row in payload["commands"]
    ]
    if attack == "none":
        checks = [row for row in payload["commands"] if row["phase"].startswith("check")]
        assert [row["exit_code"] for row in checks] == [1, 0]
        assert all("verification_group" in row for row in checks)
        assert sum(row["phase"].startswith("prepare") for row in payload["commands"]) == 2
        assert (tmp_path / "src/a.py").read_text(encoding="utf-8") == "after\n"
    elif attack == "source_during_prepare":
        assert "native_validation_" in json.dumps(payload)
    else:
        assert "verification_dependency_drift" in json.dumps(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("repeated_cancel", [False, True])
@pytest.mark.parametrize("heartbeat_failure", [False, True])
@pytest.mark.parametrize("note_api", ["native", "absent", "non_callable"])
async def test_drain_uncertainty_stops_native_heartbeat_without_settlement_or_disposal(
    tmp_path: Path,
    authored: tuple,
    monkeypatch: pytest.MonkeyPatch,
    repeated_cancel: bool,
    heartbeat_failure: bool,
    note_api: str,
) -> None:
    executor, run, context = _quality_case(tmp_path, authored, monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    drain_seen = asyncio.Event()
    observed: dict[str, Any] = {}
    original_run = NativeValidationSession.run_command
    original_settle = quality_impl._settle_pending_workspace_quality_repair_attempt
    settlements: list[str] = []

    class CompatibilityDrainError(ProcessTreeDrainError):
        """Model an optional API, not a claim of native Python 3.10 execution."""

        def __getattribute__(self, name: str) -> Any:
            if name == "add_note" and note_api == "absent":
                raise AttributeError(name)
            if name == "add_note" and note_api == "non_callable":
                return None
            return super().__getattribute__(name)

    drain_error = CompatibilityDrainError("fixture_physical_drain_unproved")
    monkeypatch.setattr(quality_impl, "_WORKSPACE_QUALITY_REPAIR_HEARTBEAT_INTERVAL_SECONDS", 0.001)

    async def repair(**_kwargs: object) -> tuple:
        rows, summary = await _committed_candidate(tmp_path, authored)
        pending = summary["_pending_task_runtime_repair_attempt"]
        authority = production._authority(pending["execution_attempt"])

        class BlockingAuthority:
            def heartbeat(self, **kwargs: Any) -> Any:
                entered.set()
                assert release.wait(10), "heartbeat test release timed out"
                verdict = authority.heartbeat(**kwargs)
                if heartbeat_failure:
                    raise LookupError("fixture heartbeat failed")
                return verdict

        stop = asyncio.Event()
        heartbeat = asyncio.create_task(
            quality_impl._run_workspace_quality_repair_heartbeat(
                BlockingAuthority(), stop=stop, failures=[], context_summary="review-drain"
            )
        )
        pending.update(heartbeat_task=heartbeat, heartbeat_stop=stop, heartbeat_failures=[])
        observed.update(pending)
        return rows, summary

    async def run_command(session: NativeValidationSession, command: list[str], timeout: float) -> dict:
        result = await original_run(session, command, timeout)
        if (tmp_path / "src/a.py").read_text(encoding="utf-8") == "after\n":
            assert await asyncio.to_thread(entered.wait, 5)
            observed["staging"] = session.group.staging_workspace
            drain_seen.set()
            raise drain_error
        return result

    async def settle(*args: Any, **kwargs: Any) -> Any:
        settlements.append(kwargs["reason"])
        return await original_settle(*args, **kwargs)

    monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", repair)
    monkeypatch.setattr(NativeValidationSession, "run_command", run_command)
    monkeypatch.setattr(quality_impl, "_settle_pending_workspace_quality_repair_attempt", settle)
    task = asyncio.create_task(executor._run_workspace_quality_checks(run, context))
    try:
        await asyncio.wait_for(drain_seen.wait(), 5)
        # Give the wrapper's finally block a scheduling turn, not a clock-based guess.
        await asyncio.sleep(0)
        if repeated_cancel:
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
        release.set()
        with pytest.raises(ProcessTreeDrainError) as raised:
            await task
        assert raised.value is drain_error
        assert observed["heartbeat_stop"].is_set()
        assert observed["heartbeat_task"].done()
        assert not observed["heartbeat_task"].cancelled()
        if heartbeat_failure:
            notes = (
                getattr(raised.value, "__notes__", ())
                if callable(getattr(raised.value, "add_note", None))
                else getattr(raised.value, "repair_heartbeat_cleanup_errors", ())
            )
            assert "repair heartbeat stop failed: LookupError: fixture heartbeat failed" in notes
        assert settlements == []
        assert observed["staging"].is_dir()
        assert (tmp_path / "src/a.py").read_text(encoding="utf-8") == "after\n"
    finally:
        # Test-only release prevents a RED regression from leaking a native thread.
        release.set()
        if observed:
            observed["heartbeat_stop"].set()
            await asyncio.gather(observed["heartbeat_task"], return_exceptions=True)
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
