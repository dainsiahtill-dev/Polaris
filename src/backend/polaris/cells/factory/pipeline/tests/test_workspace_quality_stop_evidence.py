"""Dynamic replay of repair exits, without a Provider or generated project."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal import factory_workspace_quality_impl as quality_impl
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
)
from polaris.cells.factory.pipeline.tests._characterization_helpers import _executor


@pytest.fixture(autouse=True)
def phase_only_validation_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise deadline phases, not CE/source/receipt or native sandbox authority."""

    class PhaseLogicSession:
        def __init__(self, executor: Any) -> None:
            self.executor = executor

        @classmethod
        def from_factory(cls, executor: Any, _run: Any, _context: Any, **_kwargs: Any) -> PhaseLogicSession:
            return cls(executor)

        async def run_command(self, command: list[str], timeout_seconds: float) -> dict[str, Any]:
            return await asyncio.to_thread(self.executor._run_workspace_quality_command, command, timeout_seconds)

        def before_repair(self) -> None:
            pass

        def restored(self) -> None:
            pass

        def close(self) -> None:
            pass

        def candidate(self, **_kwargs: Any) -> None:
            raise AssertionError("phase-only fixture does not authenticate native candidates")

    monkeypatch.setattr(quality_impl, "NativeValidationSession", PhaseLogicSession)


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline_phase", ("prepare", "check", "fallback", "before_round"))
async def test_repair_deadline_preserves_reason_and_consumed_rounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    deadline_phase: str,
) -> None:
    """Catch lost deadline detail and executed-but-unrecorded extra rounds."""
    executor = _executor(tmp_path)
    run = FactoryRun(
        id="factory-stop-evidence",
        config=FactoryConfig(name="stop-evidence"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-09-30T00:00:00+00:00",
    )
    state = {"remaining": 1000.0, "repairs": 0}
    command_calls: list[list[str]] = []

    def verifier(command: list[str], _timeout: float) -> dict[str, object]:
        command_calls.append(command)
        preparing = command == ["npm", "install"]
        if deadline_phase == "before_round" and not preparing:
            state["remaining"] = 1.0
        return {
            "command": command,
            "exit_code": 0 if preparing else 1,
            "passed": preparing,
            "stdout_tail": ""
            if preparing
            else (
                "src/b.ts(1,1): error TS2305: missing export"
                if deadline_phase == "fallback" and state["repairs"]
                else "src/a.ts(1,1): error TS2305: missing export"
            ),
            "stderr_tail": "",
            "error": "",
        }

    async def deterministic(**kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        state["repairs"] += 1
        attempt = kwargs["repair_attempt"]
        if deadline_phase == "fallback" and attempt == 1:
            return (
                [{"success": True, "tool": "edit_file", "file": "src/a.ts", "operation": "modify"}],
                {
                    "attempted": True,
                    "success": False,
                    "task_id": "TASK-A",
                    "repair_target_files": ["src/a.ts"],
                    "write_tool_evidence": True,
                    "task_boundary_scope_filter": {
                        "deferred": True,
                        "owner_task_retry_handoff_requests": [
                            {
                                "target_file": "src/b.ts",
                                "owner_step_id": "TASK-B",
                                "owner_found": True,
                                "status": "owner_found",
                                "recommended_route": "owner_task_retry",
                            }
                        ],
                    },
                },
            )
        state["remaining"] = 1.0
        if deadline_phase == "fallback":
            return [], {"attempted": True, "success": False, "write_tool_evidence": False}
        return (
            [{"success": True, "tool": "edit_file", "file": "src/a.ts", "operation": "modify"}],
            {"attempted": True, "success": True, "write_tool_evidence": True},
        )

    async def forbidden_llm(**_kwargs: Any) -> None:
        raise AssertionError("deadline exit must not issue a Provider request")

    monkeypatch.setattr(executor, "_factory_deadline_remaining_seconds", lambda _context: state["remaining"])
    monkeypatch.setattr(executor, "_workspace_quality_commands", lambda _context: [["npm", "run", "build"]])
    monkeypatch.setattr(
        executor,
        "_workspace_quality_prepare_commands",
        lambda _commands, _context: [["npm", "install"]] if deadline_phase == "prepare" else [],
    )
    monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda _run, _context: None)
    monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda _context: None)
    monkeypatch.setattr(executor, "_workspace_quality_repair_plan_probe_report", lambda _errors: {})
    monkeypatch.setattr(executor, "_run_workspace_quality_command", verifier)
    monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", deterministic)
    monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", forbidden_llm)

    passed, artifact = await executor._run_workspace_quality_checks(run, {"workspace_quality_repair_max_rounds": 1})

    payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
    repair = payload["repair"]
    assert passed is False
    assert repair["convergence_stop_reason"] == "quality_repair_deadline_insufficient"
    assert repair["deadline_blocker"]
    expected_rounds = 0 if deadline_phase == "before_round" else 2 if deadline_phase == "fallback" else 1
    assert state["repairs"] == expected_rounds
    assert len(repair["rounds"]) == expected_rounds
    assert repair["attempted"] is bool(expected_rounds)
    assert repair["extra_rounds_consumed"] == (1 if deadline_phase == "fallback" else 0)
    if deadline_phase == "fallback":
        assert repair["rounds"][1]["budget_admission"] == "residual_owner_rotation"
    assert len(command_calls) == (2 if deadline_phase in {"prepare", "fallback"} else 1)
