"""Session continuation must retain real effects across kernel turn boundaries."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from polaris.cells.roles.profile.public.service import RoleTurnRequest, RoleTurnResult
from polaris.cells.roles.runtime.public.service import RoleRuntimeService


class _PhysicalTurnKernel:
    """Deterministic provider/tool boundary; effects and verifier really execute."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.requests: list[RoleTurnRequest] = []
        self.gate_exits: list[int] = []
        self.results: list[RoleTurnResult] = []

    async def run(self, role, request):
        return self._execute(role, request)

    async def run_stream(self, role, request):
        yield {"type": "complete", "result": self._execute(role, request)}

    def _execute(self, role, request):
        assert role == "director"
        assert request.context_override["execution_envelope"]["allowed_paths"] == ["answer.py"]
        self.requests.append(request)
        index = len(self.requests) - 1
        file = self.workspace / "answer.py"
        status = "success"
        if index < 3:
            tool = "read_file"
            path = f"input{index}.txt"
            payload = {"file": path, "content": (self.workspace / path).read_text(encoding="utf-8")}
        elif index in (3, 5):
            tool = "write_file" if index == 3 else "edit_file"
            file.write_text("print(41)\n" if index == 3 else "print(42)\n", encoding="utf-8")
            payload = {"file": "answer.py", "ok": True}
        else:
            tool = "execute_command"
            gate = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import subprocess,sys; out=subprocess.check_output([sys.executable,'answer.py'],text=True); sys.exit(0 if out.strip()=='42' else 1)",
                ],
                cwd=self.workspace,
                capture_output=True,
                encoding="utf-8",
                check=False,
            )
            self.gate_exits.append(gate.returncode)
            payload = {"command": "python actual_answer_gate", "exit_code": gate.returncode, "stderr": gate.stderr}
            status = "success" if gate.returncode == 0 else "error"
        row = {"call_id": f"call-{index}", "tool_name": tool, "status": status, "result": payload}
        if tool in {"write_file", "edit_file"}:
            row["effect_receipt"] = {"committed": True, "side_effect_class": "write", "after_hash": f"hash-{index}"}
        final = index == 6
        result = RoleTurnResult(
            content="verified" if final else "continue",
            is_complete=final,
            tool_calls=[{"tool": tool, "call_id": row["call_id"]}],
            tool_results=[{"tool_name": tool, "success": status == "success", "result": payload}],
            batch_receipt={
                "batch_id": f"batch-{index}",
                "results": [row],
                "success_count": int(status == "success"),
                "failure_count": int(status != "success"),
            },
            execution_stats={"tool_calls": 1, "llm_calls": 1, "transaction_kernel": True},
            metadata={
                "transaction_kind": "final_answer" if final else "continue_multi_turn",
                "needs_followup_workflow": not final,
                "workflow_reason": "verification_failed" if index == 4 else "observation",
                "turn_commit_receipt": {"transition_id": f"transition-{index}"},
                "turn_outcome": {"turn_id": f"physical-{index}", "completion_status": "success"},
            },
            turn_history=[("assistant", f"turn-{index}")],
            turn_events_metadata=[{"event_id": f"event-{index}", "role": "assistant", "content": f"turn-{index}"}],
        )
        self.results.append(result)
        return result


def _install_runtime_boundary(monkeypatch, workspace, kernel):
    service = RoleRuntimeService()
    monkeypatch.setattr(service, "_get_kernel", lambda _: kernel)
    monkeypatch.setattr(service, "_emit_cognitive_runtime_shadow_artifacts", lambda **_: {})

    async def prepare(command, *, include_session_snapshot):
        return service._build_session_request(command)

    persisted = []

    async def persist(command, *, turn_history, turn_events_metadata=None):
        persisted.extend(turn_events_metadata or [])
        command.context["context_os_snapshot"] = {"transcript_log": list(persisted)}

    monkeypatch.setattr(service, "_prepare_session_request", prepare)
    monkeypatch.setattr(service, "_persist_session_turn_state", persist)
    return service


@pytest.mark.asyncio
async def test_retired_legacy_session_scenarios_stay_closed(tmp_path: Path) -> None:
    """The legacy session scenarios have no live subject after the retirement.

    These scenarios drove the legacy role execution kernel, which is
    retired: a new engineering run has exactly one owner, the engineering run
    service. The scenarios are replaced by the retirement contract, so the
    legacy write entry cannot silently come back.
    """
    from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
        assert_legacy_session_entry_is_closed,
    )

    await assert_legacy_session_entry_is_closed(
        session_id="retired-kernel-session-continuation",
        workspace=str(tmp_path),
    )


@pytest.mark.asyncio
async def test_explicit_legacy_orchestrator_keeps_its_interactive_preview(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from polaris.cells.roles.kernel.public.transaction_contracts import CognitiveGateway
    from polaris.cells.roles.kernel.public.turn_events import SessionWaitingHumanEvent
    from polaris.cells.roles.runtime.internal.session_orchestrator import RoleSessionOrchestrator

    async def no_warmup():
        return None

    monkeypatch.setattr(CognitiveGateway, "default", no_warmup)
    orchestrator = RoleSessionOrchestrator("legacy-preview", SimpleNamespace(), str(tmp_path))

    async def no_checkpoint():
        pass

    monkeypatch.setattr(orchestrator, "_checkpoint_session", no_checkpoint)
    events = [event async for event in orchestrator.execute_stream("rm -rf the workspace")]

    waiting = [event for event in events if isinstance(event, SessionWaitingHumanEvent)]
    assert len(waiting) == 1
    assert waiting[0].reason == "DESTRUCTIVE_OPERATION_REQUIRES_APPROVAL:DELETE_DIRECTORY_RECURSIVE"


@pytest.mark.parametrize("different_targets, expected_stagnation", [(True, False), (False, True)])
def test_continuation_policy_measures_receipt_progress_not_read_count(different_targets, expected_stagnation):
    from polaris.cells.roles.kernel.public.turn_contracts import TurnContinuationMode, TurnOutcomeEnvelope, TurnResult
    from polaris.cells.roles.runtime.internal.continuation_policy import ContinuationPolicy, OrchestratorSessionState

    state = OrchestratorSessionState(session_id="progress", turn_count=4)
    state.structured_findings = {
        "_canonical_role_kernel": True,
        "_findings_trajectory": [{"task_progress": "content_gathered"}] * 4,
    }
    state.turn_history = [
        {
            "batch_receipt": {
                "results": [
                    {
                        "tool_name": "read_file",
                        "arguments": {"file": f"file-{index if different_targets else 0}.py"},
                        "status": "success",
                        "result": {"content": "new facts"},
                    }
                ]
            }
        }
        for index in range(4)
    ]
    envelope = TurnOutcomeEnvelope(
        turn_result=TurnResult(turn_id="progress-turn", kind="continue_multi_turn", visible_content="", decision={}),
        continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
    )
    assert ContinuationPolicy._detect_stagnation_v2(state, envelope) is expected_stagnation


def test_local_repair_keeps_session_in_verifying_phase():
    from polaris.cells.roles.kernel.public.turn_contracts import TurnContinuationMode, TurnOutcomeEnvelope, TurnResult
    from polaris.cells.roles.runtime.internal.continuation_policy import OrchestratorSessionState
    from polaris.cells.roles.runtime.internal.session_orchestrator import SessionStateReducer

    state = OrchestratorSessionState(session_id="repair", task_progress="verifying")
    state.structured_findings["_canonical_role_kernel"] = True
    state.session_invariants.freeze("materialize_changes", "implement and verify", "verifying")
    receipt = {
        "results": [
            {
                "tool_name": "edit_file",
                "status": "success",
                "result": {"ok": True},
                "effect_receipt": {"committed": True, "side_effect_class": "write"},
            }
        ]
    }
    envelope = TurnOutcomeEnvelope(
        turn_result=TurnResult(
            turn_id="repair-turn",
            kind="continue_multi_turn",
            visible_content="repaired",
            decision={},
            batch_receipt=receipt,
        ),
        continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
    )
    SessionStateReducer(state).apply_turn_outcome(envelope, turn_index=1)
    assert state.task_progress == "verifying"
    state.session_invariants.validate("materialize_changes", "implement and verify", state.task_progress)


@pytest.mark.parametrize(
    "status,expected_feedback",
    [
        ("error", True),
        ("failed", True),
        ("timeout", True),
        ("aborted", True),
        ("cancelled", True),
        ("success", False),
        ("pending", False),
    ],
)
def test_failure_feedback_projects_terminal_receipt_statuses_without_mutation(status, expected_feedback):
    from copy import deepcopy

    from polaris.cells.roles.runtime.internal.kernel_session_turn import _failed_batch_feedback_summary

    receipt = {
        "results": [
            {
                "tool_name": "execute_command",
                "status": status,
                "arguments": {"file": "tests/garden.test.ts"},
                "error": "command stopped before verifier completion",
                "result": {"error_type": "verifier_interrupted", "effect_recovery": {"token": "PRIVATE_TOKEN"}},
                "effect_receipt": None,
            }
        ],
    }
    before = deepcopy(receipt)
    summary = _failed_batch_feedback_summary(receipt)
    if expected_feedback:
        assert "execute_command" in summary
        assert "tests/garden.test.ts" in summary
        assert "verifier_interrupted" in summary
        assert "command stopped" in summary
        assert "PRIVATE_TOKEN" not in summary
    else:
        assert summary == ""
    assert receipt == before
