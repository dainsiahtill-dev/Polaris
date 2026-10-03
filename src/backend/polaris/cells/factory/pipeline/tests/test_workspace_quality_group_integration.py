"""Exercise the actual Factory quality-loop command boundary with sealed inputs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal import factory_workspace_quality_impl as quality_impl
from polaris.cells.factory.pipeline.internal.factory_run_service import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.native_validation_session import NativeValidationSession
from polaris.cells.factory.pipeline.tests._characterization_helpers import _executor
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import (
    _projection,
    _real_effect,
    authored as authored,
)
from polaris.cells.roles.adapters.internal.director.quality_gate._candidate_guard import (
    DirectorQualityRepairCandidateGuard,
)


@pytest.mark.asyncio
async def test_factory_quality_commands_run_on_one_group_not_live_source(
    tmp_path: Path, authored: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    portfolio, contract, _ = authored
    executor = _executor(tmp_path)
    run = FactoryRun(
        id=contract.run_id,
        config=FactoryConfig(name="group-integration"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-01T00:00:00+00:00",
    )
    run.metadata["stage_results"] = {
        "chief_engineer_review": {
            "status": "success",
            "artifacts": [portfolio.portfolio_path],
        }
    }
    monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda _run, _context: None)
    monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda _context: None)
    passed, artifact = await executor._run_workspace_quality_checks(
        run,
        {
            "project_id": contract.project_id,
            "quality_commands": [
                ["/bin/sh", "-c", "mkdir -p dist; cp src/a.py dist/result.txt"],
                ["/bin/cat", "dist/result.txt"],
            ],
            "workspace_validation_install_dependencies": False,
        },
    )
    assert passed is True
    assert not (tmp_path / "dist").exists()
    payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
    commands = payload["commands"]
    assert len(commands) == 2
    assert commands[0]["verification_group"]["input_hash"] == commands[1]["verification_group"]["input_hash"]
    assert commands[1]["stdout_tail"] == "before\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("include_read", [False, True])
@pytest.mark.parametrize("artifact_record_fails", [False, True])
async def test_actual_factory_repair_revalidates_committed_candidate_in_new_group(
    tmp_path: Path, authored: tuple, monkeypatch: pytest.MonkeyPatch, include_read: bool, artifact_record_fails: bool
) -> None:
    portfolio, contract, _ = authored
    executor = _executor(tmp_path)
    run = FactoryRun(
        id=contract.run_id,
        config=FactoryConfig(name="candidate-integration"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-01T00:00:00+00:00",
    )
    run.metadata["stage_results"] = {
        "chief_engineer_review": {
            "status": "success",
            "artifacts": [portfolio.portfolio_path],
        }
    }
    monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda _run, _context: None)
    monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda _context: None)

    async def committed_repair(**_kwargs: object) -> tuple:
        guard = await DirectorQualityRepairCandidateGuard.capture(
            workspace=tmp_path, candidate_id="owned-candidate", target_files=["src/a.py"]
        )
        attempt, rows = await _real_effect(tmp_path)
        if include_read:
            rows.insert(
                0,
                {
                    "tool_name": "read_file",
                    "tool": "read_file",
                    "success": True,
                    "result": {"file": "src/a.py", "content": "before\n"},
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
                "task_completion_projection": _projection(portfolio, contract),
            },
        }

    monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", committed_repair)
    if artifact_record_fails:

        def failed_owner_record(_pending: object) -> tuple:
            raise RuntimeError("fixture_artifact_owner_commit_failed")

        monkeypatch.setattr(quality_impl, "_record_workspace_quality_repair_artifact_receipts", failed_owner_record)
    passed, artifact = await executor._run_workspace_quality_checks(
        run,
        {
            "project_id": contract.project_id,
            "workspace_quality_repair_max_rounds": 1,
            "quality_commands": [
                [
                    "/bin/sh",
                    "-c",
                    "test $(cat src/a.py) = after || { echo 'src/a.py: candidate not repaired' >&2; exit 1; }",
                ]
            ],
            "workspace_validation_install_dependencies": False,
        },
    )
    assert passed is not artifact_record_fails
    payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
    checks = [row for row in payload["commands"] if row["phase"].startswith("check")]
    if artifact_record_fails:
        assert checks[-1]["exit_code"] == 1
        assert (tmp_path / "src/a.py").read_text(encoding="utf-8") == "before\n"
        return
    assert [row["exit_code"] for row in checks] == [1, 0]
    assert checks[0]["verification_group"]["input_hash"] != checks[1]["verification_group"]["input_hash"]
    round_one = payload["repair"]["rounds"][0]
    assert round_one["repair_summary"]["task_runtime_repair_attempt"]["project_artifact_receipt_count"] == 3


@pytest.mark.asyncio
async def test_cancelled_candidate_drains_then_rolls_back_and_settles_failed(
    tmp_path: Path, authored: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    portfolio, contract, _ = authored
    executor = _executor(tmp_path)
    run = FactoryRun(
        id=contract.run_id,
        config=FactoryConfig(name="cancel-integration"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-01T00:00:00+00:00",
    )
    run.metadata["stage_results"] = {
        "chief_engineer_review": {
            "status": "success",
            "artifacts": [portfolio.portfolio_path],
        }
    }
    monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda _run, _context: None)
    monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda _context: None)
    entered = asyncio.Event()
    original_run = NativeValidationSession.run_command

    async def observe_ready(session: NativeValidationSession, command: list[str], timeout: float) -> dict:
        physical = asyncio.create_task(original_run(session, command, timeout))
        if (tmp_path / "src/a.py").read_text(encoding="utf-8") == "after\n":
            for _ in range(200):
                if (session.group.staging_workspace / "verifier-ready").exists():
                    entered.set()
                    break
                await asyncio.sleep(0.005)
        return await physical

    monkeypatch.setattr(NativeValidationSession, "run_command", observe_ready)

    async def committed_repair(**_kwargs: object) -> tuple:
        guard = await DirectorQualityRepairCandidateGuard.capture(
            workspace=tmp_path, candidate_id="cancel-candidate", target_files=["src/a.py"]
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

    monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", committed_repair)
    task = asyncio.create_task(
        executor._run_workspace_quality_checks(
            run,
            {
                "project_id": contract.project_id,
                "workspace_quality_repair_max_rounds": 1,
                "quality_commands": [
                    [
                        "/bin/sh",
                        "-c",
                        "test $(cat src/a.py) = after || { echo 'src/a.py: needs repair' >&2; exit 1; }; touch verifier-ready; sleep 10",
                    ]
                ],
                "workspace_validation_install_dependencies": False,
            },
        )
    )
    await asyncio.wait_for(entered.wait(), 4)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (tmp_path / "src/a.py").read_text(encoding="utf-8") == "before\n"
