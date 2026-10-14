"""Actual Factory quality-loop groups consume public CE authority and sealed inputs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal import factory_workspace_quality_impl as quality_impl
from polaris.cells.factory.pipeline.internal.native_validation_session import NativeValidationSession
from polaris.cells.factory.pipeline.tests.test_exact_ce_clean_verification import _record_sources
from polaris.cells.factory.pipeline.tests.test_materialization_contract_verifiers import _fixture
from polaris.cells.factory.pipeline.tests.test_materialization_llm_continuation import _entrypoint_source_effect
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import _projection
from polaris.cells.roles.adapters.internal.director.quality_gate._candidate_guard import (
    DirectorQualityRepairCandidateGuard,
)


def _owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, failing: bool = False, cancel: bool = False) -> tuple:
    # The fixture authors the CE portfolio through its public compiler and binds
    # its execution-authority port. No verifier receipt or PASS is seeded.
    additions = {}
    if cancel:
        additions["tests/test_output.py"] = (
            "from pathlib import Path\nimport time, unittest\n"
            "class Output(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        import importlib.machinery\n"
            "        module = importlib.machinery.SourcelessFileLoader('built', 'src/module.pyc').load_module()\n"
            "        self.assertEqual(module.value, 8)\n"
            "        Path('verifier-ready').write_text('ready', encoding='utf-8')\n"
            "        time.sleep(10)\n"
        )
    executor, run, port = _fixture(
        tmp_path,
        monkeypatch,
        failing=failing,
        stdlib_verifier=True,
        task_id="DEO-LIFECYCLE",
        run_id="run",
        authored_contents=additions,
    )
    _record_sources(executor, run, port)
    monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda *_args: None)
    monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda *_args: [])
    monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda _context: None)
    return executor, run, port


def _report(executor: Any, reference: str) -> dict:
    return json.loads(executor._artifact_path(reference).read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_factory_quality_commands_run_on_one_group_not_live_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _ = _owner(tmp_path, monkeypatch)
    passed, artifact = await executor._run_workspace_quality_checks(run, {})
    assert passed is True
    assert not (executor.workspace / "src/module.pyc").exists()
    commands = _report(executor, artifact)["commands"]
    assert len(commands) == 2
    assert commands[0]["verification_group"]["input_hash"] == commands[1]["verification_group"]["input_hash"]
    assert "OK" in commands[1]["stderr_tail"]
    assert {row["ce_verification_binding"]["obligation_id"] for row in commands} == {"verify-build", "verify-test"}
    assert all(row["ce_verification_binding"]["authoritative_receipt"] is False for row in commands)


@pytest.mark.asyncio
@pytest.mark.parametrize("include_read", [False, True])
@pytest.mark.parametrize("artifact_record_fails", [False, True])
async def test_actual_factory_repair_revalidates_committed_candidate_in_new_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, include_read: bool, artifact_record_fails: bool
) -> None:
    executor, run, port = _owner(tmp_path, monkeypatch, failing=True)

    async def committed_repair(**_kwargs: object) -> tuple:
        guard = await DirectorQualityRepairCandidateGuard.capture(
            workspace=executor.workspace, candidate_id="owned-candidate", target_files=["src/module.py"]
        )
        attempt, rows = await _entrypoint_source_effect(
            executor.workspace, "src/module.py", "value = 8\n", "group-candidate"
        )
        if include_read:
            rows.insert(
                0,
                {
                    "tool_name": "read_file",
                    "tool": "read_file",
                    "success": True,
                    "result": {"file": "src/module.py", "content": "value = 7\n"},
                },
            )
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
                "task_completion_projection": _projection(port.blueprint_portfolio, port.completion_contract),
            },
        }

    monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", committed_repair)
    if artifact_record_fails:

        def failed_owner_record(_pending: object) -> tuple:
            raise RuntimeError("fixture_artifact_owner_commit_failed")

        monkeypatch.setattr(quality_impl, "_record_workspace_quality_repair_artifact_receipts", failed_owner_record)
    passed, artifact = await executor._run_workspace_quality_checks(run, {"workspace_quality_repair_max_rounds": 1})
    assert passed is not artifact_record_fails
    payload = _report(executor, artifact)
    checks = [row for row in payload["commands"] if row["phase"].startswith("check")]
    tests = [row for row in checks if row.get("ce_verification_binding", {}).get("obligation_id") == "verify-test"]
    if artifact_record_fails:
        assert tests[-1]["exit_code"] == 1
        assert (executor.workspace / "src/module.py").read_text(encoding="utf-8") == "value = 7\n"
        return
    assert [row["exit_code"] for row in tests] == [1, 0]
    assert tests[0]["verification_group"]["input_hash"] != tests[1]["verification_group"]["input_hash"]
    round_one = payload["repair"]["rounds"][0]
    assert round_one["repair_summary"]["task_runtime_repair_attempt"]["project_artifact_receipt_count"] == 3


@pytest.mark.asyncio
async def test_cancelled_candidate_drains_then_rolls_back_and_settles_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, port = _owner(tmp_path, monkeypatch, failing=True, cancel=True)
    entered = asyncio.Event()
    original_run = NativeValidationSession.run_command

    async def observe_ready(session: NativeValidationSession, command: list[str], timeout: float) -> dict:
        physical = asyncio.create_task(original_run(session, command, timeout))
        if (
            command.authority.modality == "test"
            and (executor.workspace / "src/module.py").read_text(encoding="utf-8") == "value = 8\n"
        ):
            for _ in range(1000):
                if (session.group.staging_workspace / "verifier-ready").exists():
                    entered.set()
                    break
                await asyncio.sleep(0.01)
        return await physical

    monkeypatch.setattr(NativeValidationSession, "run_command", observe_ready)

    async def committed_repair(**_kwargs: object) -> tuple:
        guard = await DirectorQualityRepairCandidateGuard.capture(
            workspace=executor.workspace, candidate_id="cancel-candidate", target_files=["src/module.py"]
        )
        attempt, rows = await _entrypoint_source_effect(
            executor.workspace, "src/module.py", "value = 8\n", "group-cancel"
        )
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
                "task_completion_projection": _projection(port.blueprint_portfolio, port.completion_contract),
            },
        }

    monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", committed_repair)
    task = asyncio.create_task(executor._run_workspace_quality_checks(run, {"workspace_quality_repair_max_rounds": 1}))
    try:
        await asyncio.wait_for(entered.wait(), 90)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert (executor.workspace / "src/module.py").read_text(encoding="utf-8") == "value = 7\n"
