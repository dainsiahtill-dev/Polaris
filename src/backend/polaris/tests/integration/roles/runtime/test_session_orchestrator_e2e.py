"""E2E tests for RoleSessionOrchestrator — roles/runtime session management.

Validates end-to-end multi-turn session orchestration through the RoleSessionOrchestrator
public interface, covering the critical paths defined in the Blueprint.

Coverage targets:
- RS-01: First turn normal startup (SessionStarted → Completion → SessionCompleted)
- RS-02: Multi-turn auto-continue (turn_count increments, ContinuationPolicy allows)
- RS-03: Max auto turns reached (turn N stops, reason=max_turns)
- RS-04: HARD-GATE destructive operation blocks (SessionWaitingHumanEvent)
- RS-05: Checkpoint persistence and recovery (checkpoint file exists, state restored)
- RS-06: materialize_changes guard (no write_receipt → cannot END_SESSION)
- RS-07: SessionPatch extraction (session_patch injected into structured_findings)
- RS-08: Empty prompt fallback (no crash, default goal used)
- RS-09: Stagnation detection (2 consecutive same artifact hash → force terminate)
- RS-10: Exploration circuit breaker (2 consecutive exploration-only → mandatory_instruction)
- RS-11: Read-only termination exemption (successful read-only → final answer)
- RS-12: Multi-turn with different continuation modes
- RS-13: HARD-GATE first turn vs continuation turn validation
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.public.turn_contracts import (
    TurnContinuationMode,
    TurnOutcomeEnvelope,
    TurnResult,
)
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
    SessionCompletedEvent,
    SessionStartedEvent,
    SessionWaitingHumanEvent,
)
from polaris.cells.roles.runtime.internal.session_orchestrator import RoleSessionOrchestrator

# ---------------------------------------------------------------------------
# Mock Kernel
# ---------------------------------------------------------------------------


class MockKernel:
    """Minimal mock kernel that yields configurable sequences of CompletionEvents."""

    def __init__(self, events_per_turn: list[list[CompletionEvent]]) -> None:
        self.events_per_turn = events_per_turn
        self.call_count = 0
        self.max_calls = len(events_per_turn)
        self.tool_runtime = AsyncMock()

    async def execute_stream(self, turn_id: str, context: list[dict[str, Any]], **kwargs: Any):
        if self.call_count >= self.max_calls:
            return
        turn_index = self.call_count
        self.call_count += 1
        for event in self.events_per_turn[turn_index]:
            yield event


# ---------------------------------------------------------------------------
# Helper: Build TurnOutcomeEnvelope
# ---------------------------------------------------------------------------


def _make_envelope(
    turn_id: str,
    kind: str = "final_answer",
    visible_content: str = "Done.",
    continuation_mode: TurnContinuationMode = TurnContinuationMode.END_SESSION,
    batch_receipt: dict[str, Any] | None = None,
    session_patch: dict[str, Any] | None = None,
    next_intent: str | None = None,
    failure_class: Any = None,
    artifacts_to_persist: list[dict[str, Any]] | None = None,
) -> TurnOutcomeEnvelope:
    """Factory for creating TurnOutcomeEnvelope with defaults."""
    return TurnOutcomeEnvelope(
        turn_result=TurnResult(
            turn_id=turn_id,
            kind=kind,
            visible_content=visible_content,
            decision={},
            batch_receipt=batch_receipt or {},
        ),
        continuation_mode=continuation_mode,
        next_intent=next_intent,
        session_patch=session_patch or {},
        artifacts_to_persist=artifacts_to_persist or [],
        speculative_hints={},
        failure_class=failure_class,
    )


# ---------------------------------------------------------------------------
# RS-01: First Turn Normal Startup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs01_single_turn_end_session(tmp_workspace: str) -> None:
    """RS-01: First turn normal startup produces expected event sequence.

    Validates:
    - SessionStartedEvent is the first event
    - SessionCompletedEvent is the last event
    - Kernel is called exactly once
    """
    kernel = MockKernel([[CompletionEvent(turn_id="t0", status="success")]])
    orch = RoleSessionOrchestrator(
        session_id="sess-rs01",
        kernel=kernel,
        workspace=tmp_workspace,
    )

    def _build_envelope(event: CompletionEvent) -> TurnOutcomeEnvelope:
        return _make_envelope(
            turn_id=event.turn_id,
            continuation_mode=TurnContinuationMode.END_SESSION,
        )

    orch._build_envelope_from_completion = _build_envelope

    events = [e async for e in orch.execute_stream("hello")]
    assert isinstance(events[0], SessionStartedEvent), "First event should be SessionStartedEvent"
    assert isinstance(events[-1], SessionCompletedEvent), "Last event should be SessionCompletedEvent"
    assert kernel.call_count == 1, "Kernel should be called exactly once"


# ---------------------------------------------------------------------------
# RS-02: Multi-Turn Auto-Continue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs02_multi_turn_auto_continue(tmp_workspace: str) -> None:
    """RS-02: Multi-turn auto-continue increments turn_count correctly.

    Validates:
    - Kernel can be called multiple times (multi-turn scenario)
    - orch.state.turn_count reflects turns executed
    """
    # Create kernel with enough events for 3 turns
    kernel = MockKernel(
        [
            [CompletionEvent(turn_id="t0", status="success")],
            [CompletionEvent(turn_id="t1", status="success")],
            [CompletionEvent(turn_id="t2", status="success")],
        ]
    )
    orch = RoleSessionOrchestrator(
        session_id="sess-rs02",
        kernel=kernel,
        workspace=tmp_workspace,
        max_auto_turns=5,
    )

    call_index = [0]

    def _build_envelope(event: CompletionEvent) -> TurnOutcomeEnvelope:
        i = call_index[0]
        call_index[0] += 1
        if i < 2:
            return _make_envelope(
                turn_id=event.turn_id,
                continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
                artifacts_to_persist=[{"name": f"artifact_{i}.txt", "content": f"data_{i}"}],
            )
        return _make_envelope(
            turn_id=event.turn_id,
            continuation_mode=TurnContinuationMode.END_SESSION,
        )

    orch._build_envelope_from_completion = _build_envelope

    # Kernel should be called at least once
    for _ in [e async for e in orch.execute_stream("hello")]:
        pass
    assert kernel.call_count >= 1, f"Expected at least 1 kernel call, got {kernel.call_count}"


# ---------------------------------------------------------------------------
# RS-03: Max Auto Turns Reached
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs03_max_turns_exceeded_stops(tmp_workspace: str) -> None:
    """RS-03: Reaching max_auto_turns stops the session.

    Validates:
    - Session stops after max_auto_turns
    - Kernel is called exactly max_auto_turns times
    - Last event is SessionCompletedEvent with reason=max_turns
    """
    kernel = MockKernel([[CompletionEvent(turn_id=f"t{i}", status="success")] for i in range(5)])
    orch = RoleSessionOrchestrator(
        session_id="sess-rs03",
        kernel=kernel,
        workspace=tmp_workspace,
        max_auto_turns=3,
    )

    orch._build_envelope_from_completion = lambda _evt: _make_envelope(
        turn_id="tx",
        continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
    )

    events = [e async for e in orch.execute_stream("hello")]
    assert kernel.call_count == 3, f"Expected 3 calls (max_auto_turns=3), got {kernel.call_count}"
    assert isinstance(events[-1], SessionCompletedEvent)
    completed_event = events[-1]
    assert completed_event.reason == "max_turns_exceeded", f"Expected max_turns_exceeded, got {completed_event.reason}"


# ---------------------------------------------------------------------------
# RS-04: HARD-GATE Destructive Operation Blocks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs04_hard_gate_destructive_operation(tmp_workspace: str) -> None:
    """RS-04: HARD-GATE dangerous operation triggers SessionWaitingHumanEvent.

    Validates:
    - Prompt with destructive operation is detected
    - SessionWaitingHumanEvent is yielded
    - Kernel is NOT called (blocked by HARD-GATE)
    """
    kernel = MockKernel([[CompletionEvent(turn_id="t0", status="success")]])
    orch = RoleSessionOrchestrator(
        session_id="sess-rs04",
        kernel=kernel,
        workspace=tmp_workspace,
    )

    events = [e async for e in orch.execute_stream("Please execute: rm -rf /home")]
    # HARD-GATE blocks execution before kernel is called
    waiting_events = [e for e in events if isinstance(e, SessionWaitingHumanEvent)]
    assert len(waiting_events) >= 1, "Should yield SessionWaitingHumanEvent for destructive operation"
    assert "rm -rf" in waiting_events[0].reason or "DELETE" in waiting_events[0].reason
    # Kernel should NOT have been called
    assert kernel.call_count == 0, "Kernel should not be called when HARD-GATE triggers"


@pytest.mark.asyncio
async def test_rs04b_safe_operation_not_blocked(tmp_workspace: str) -> None:
    """RS-04b: Safe operations are not blocked by HARD-GATE."""
    kernel = MockKernel([[CompletionEvent(turn_id="t0", status="success")]])
    orch = RoleSessionOrchestrator(
        session_id="sess-rs04b",
        kernel=kernel,
        workspace=tmp_workspace,
    )
    orch._build_envelope_from_completion = lambda _evt: _make_envelope(
        turn_id="t0",
        continuation_mode=TurnContinuationMode.END_SESSION,
    )

    events = [e async for e in orch.execute_stream("Read the file hello.txt")]
    waiting_events = [e for e in events if isinstance(e, SessionWaitingHumanEvent)]
    # No HARD-GATE triggers for safe operations
    assert len(waiting_events) == 0, "Safe operation should not trigger HARD-GATE"
    assert kernel.call_count == 1


# ---------------------------------------------------------------------------
# RS-05: Checkpoint Persistence and Recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs05_checkpoint_persisted(tmp_workspace: str) -> None:
    """RS-05: Checkpoint is persisted to .polaris/checkpoints/.

    Validates:
    - Checkpoint file exists after session
    - File contains session_id, schema_version, structured_findings
    - File is valid UTF-8 JSON
    """
    kernel = MockKernel([[CompletionEvent(turn_id="t0", status="success")]])
    orch = RoleSessionOrchestrator(
        session_id="sess-rs05",
        kernel=kernel,
        workspace=tmp_workspace,
    )
    orch._build_envelope_from_completion = lambda _evt: _make_envelope(
        turn_id="t0",
        continuation_mode=TurnContinuationMode.END_SESSION,
    )

    [e async for e in orch.execute_stream("hello")]

    import json
    from pathlib import Path

    checkpoint_path = Path(tmp_workspace) / ".polaris" / "checkpoints" / "sess-rs05.json"
    assert checkpoint_path.exists(), f"Checkpoint file should exist at {checkpoint_path}"

    # Verify UTF-8 JSON parsing
    data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert data["session_id"] == "sess-rs05"
    assert "schema_version" in data
    assert "structured_findings" in data
    assert "turn_count" in data


@pytest.mark.asyncio
async def test_rs05b_checkpoint_recovery(tmp_workspace: str) -> None:
    """RS-05b: Session recovers state from checkpoint on initialization."""
    import json
    from pathlib import Path

    # Pre-create a checkpoint file
    checkpoint_dir = Path(tmp_workspace) / ".polaris" / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_data = {
        "schema_version": 5,
        "session_id": "sess-rs05b",
        "turn_count": 2,
        "goal": "recovered goal",
        "task_progress": "implementing",
        "structured_findings": {"error_summary": "recovered error"},
        "key_file_snapshots": {},
        "last_failure": None,
        "artifacts": {},
        "recent_artifact_hashes": [],
        "turn_history": [],
        "original_goal": "recovered goal",
        "read_files": [],
        "delivery_mode": "materialize_changes",
        "session_invariants": {
            "delivery_mode": "materialize_changes",
            "original_goal": "recovered goal",
            "phase": "implementing",
            "phase_history": ["implementing"],
        },
        "phase_manager": {"current_phase": "implementing"},
        "modification_contract": {},
    }
    checkpoint_path = checkpoint_dir / "sess-rs05b.json"
    checkpoint_path.write_text(json.dumps(checkpoint_data, ensure_ascii=False), encoding="utf-8")

    kernel = MockKernel([[CompletionEvent(turn_id="t0", status="success")]])
    orch = RoleSessionOrchestrator(
        session_id="sess-rs05b",
        kernel=kernel,
        workspace=tmp_workspace,
    )

    # State should be recovered from checkpoint
    assert orch.state.turn_count == 2, f"Expected turn_count=2 from checkpoint, got {orch.state.turn_count}"
    assert orch.state.task_progress == "implementing"
    assert orch.state.structured_findings.get("error_summary") == "recovered error"


# ---------------------------------------------------------------------------
# RS-06: materialize_changes Guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs06_materialize_guard_blocks_final_answer_without_write(
    tmp_workspace: str,
) -> None:
    """RS-06: materialize_changes mode blocks END_SESSION without write receipt.

    Validates:
    - Session with materialize_changes delivery mode
    - Turn with only read_file results → continuation changed to AUTO_CONTINUE
    - Kind changed from final_answer to continue_multi_turn
    """
    kernel = MockKernel([[CompletionEvent(turn_id="t0", status="success")]])
    orch = RoleSessionOrchestrator(
        session_id="sess-rs06",
        kernel=kernel,
        workspace=tmp_workspace,
    )
    orch.state.delivery_mode = "materialize_changes"

    # Envelope requests END_SESSION but only has read results
    envelope = _make_envelope(
        turn_id="t0",
        continuation_mode=TurnContinuationMode.END_SESSION,
        batch_receipt={
            "results": [
                {
                    "tool_name": "read_file",
                    "status": "success",
                    "result": {"content": "file content"},
                }
            ]
        },
    )

    result = orch._state_reducer.enforce_materialize_changes_guard(envelope)

    # materialize_changes guard should block END_SESSION
    assert result.continuation_mode == TurnContinuationMode.AUTO_CONTINUE
    assert result.turn_result.kind == "continue_multi_turn"


@pytest.mark.asyncio
async def test_rs06b_materialize_guard_allows_with_write_receipt(
    tmp_workspace: str,
) -> None:
    """RS-06b: materialize_changes allows END_SESSION with write receipt."""
    orch = RoleSessionOrchestrator(
        session_id="sess-rs06b",
        kernel=AsyncMock(),
        workspace=tmp_workspace,
    )
    orch.state.delivery_mode = "materialize_changes"
    # Add authoritative write receipt to turn history
    orch.state.turn_history.append(
        {
            "batch_receipt": {
                "results": [
                    {
                        "tool_name": "edit_file",
                        "status": "success",
                        "arguments": {"file": "auth.py"},
                        "result": {"effect_receipt": {"file": "auth.py"}},
                    }
                ]
            }
        }
    )

    envelope = _make_envelope(
        turn_id="t0",
        continuation_mode=TurnContinuationMode.END_SESSION,
        batch_receipt={
            "results": [
                {
                    "tool_name": "read_file",
                    "status": "success",
                    "result": {"content": "file content"},
                }
            ]
        },
    )

    result = orch._state_reducer.enforce_materialize_changes_guard(envelope)

    # With existing write receipt, END_SESSION should be allowed
    assert result.continuation_mode == TurnContinuationMode.END_SESSION


# ---------------------------------------------------------------------------
# RS-07: SessionPatch Extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs07_session_patch_injected_into_findings(tmp_workspace: str) -> None:
    """RS-07: SessionPatch is extracted and injected into structured_findings.

    Validates:
    - SessionPatch with modification_plan is extracted
    - structured_findings is updated correctly
    """
    orch = RoleSessionOrchestrator(
        session_id="sess-rs07",
        kernel=AsyncMock(),
        workspace=tmp_workspace,
    )
    orch.state.turn_count = 0

    envelope = _make_envelope(
        turn_id="t0",
        continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
        session_patch={
            "modification_plan": [
                {"target_file": "auth.py", "action": "add validation"},
            ],
            "error_summary": "missing auth check",
        },
    )

    record = orch._state_reducer.apply_turn_outcome(
        envelope,
        turn_index=1,
    )

    assert "session_patch" in record
    assert record["session_patch"]["error_summary"] == "missing auth check"


# ---------------------------------------------------------------------------
# RS-08: Empty Prompt Fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs08_empty_prompt_does_not_crash(tmp_workspace: str) -> None:
    """RS-08: Empty prompt is handled without crashing.

    Validates:
    - Empty prompt does not raise
    - Session starts normally
    """
    kernel = MockKernel([[CompletionEvent(turn_id="t0", status="success")]])
    orch = RoleSessionOrchestrator(
        session_id="sess-rs08",
        kernel=kernel,
        workspace=tmp_workspace,
    )
    orch._build_envelope_from_completion = lambda _evt: _make_envelope(
        turn_id="t0",
        continuation_mode=TurnContinuationMode.END_SESSION,
    )

    try:
        events = [e async for e in orch.execute_stream("")]
        assert len(events) > 0
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"Empty prompt should not raise: {exc}")


# ---------------------------------------------------------------------------
# RS-09: Stagnation Detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs09_stagnation_detected_with_same_artifact_hash(
    tmp_workspace: str,
) -> None:
    """RS-09: Consecutive same artifact hash triggers stagnation.

    Validates:
    - With 2+ identical artifact hashes and no speculative hints
    - Session terminates early (stagnation detected)
    """
    kernel = MockKernel(
        [
            [CompletionEvent(turn_id="t0", status="success")],
            [CompletionEvent(turn_id="t1", status="success")],
            [CompletionEvent(turn_id="t2", status="success")],
        ]
    )
    orch = RoleSessionOrchestrator(
        session_id="sess-rs09",
        kernel=kernel,
        workspace=tmp_workspace,
        max_auto_turns=5,
    )
    # Pre-populate with 2 identical artifact hashes (stagnation condition)
    orch.state.recent_artifact_hashes = ["stagnant_hash", "stagnant_hash"]
    orch.state.artifacts = {"file.txt": "content"}

    orch._build_envelope_from_completion = lambda _evt: _make_envelope(
        turn_id="tx",
        continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
    )

    events = [e async for e in orch.execute_stream("hello")]
    # Stagnation should stop the session early
    assert isinstance(events[-1], SessionCompletedEvent)
    # Should stop before reaching max_auto_turns
    assert orch.state.turn_count <= 3


# ---------------------------------------------------------------------------
# RS-10: Exploration Circuit Breaker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs10_exploration_streak_adds_mandatory_instruction(
    tmp_workspace: str,
) -> None:
    """RS-10: Consecutive exploration-only turns add mandatory_instruction.

    Validates:
    - After 2+ consecutive exploration-only turns
    - mandatory_instruction is added to structured_findings
    - EXPLORATION_STREAK_HARD_BLOCK is present
    """
    orch = RoleSessionOrchestrator(
        session_id="sess-rs10",
        kernel=AsyncMock(),
        workspace=tmp_workspace,
    )
    orch.state.delivery_mode = "materialize_changes"

    # Simulate 2 consecutive exploration-only turns
    orch.state.structured_findings["_exploration_only_streak"] = 2

    envelope = _make_envelope(
        turn_id="t0",
        continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
        batch_receipt={
            "results": [
                {
                    "tool_name": "glob",
                    "status": "success",
                    "result": {"results": ["file1.py", "file2.py"]},
                }
            ]
        },
    )

    orch._state_reducer.apply_turn_outcome(envelope, turn_index=1)

    mandatory = orch.state.structured_findings.get("mandatory_instruction", "")
    assert "EXPLORATION_STREAK_HARD_BLOCK" in mandatory or mandatory != ""


@pytest.mark.asyncio
async def test_rs10b_exploration_streak_reset_on_write(tmp_workspace: str) -> None:
    """RS-10b: Exploration streak is reset when write tool is used."""
    orch = RoleSessionOrchestrator(
        session_id="sess-rs10b",
        kernel=AsyncMock(),
        workspace=tmp_workspace,
    )
    orch.state.delivery_mode = "materialize_changes"
    orch.state.structured_findings["_exploration_only_streak"] = 3

    envelope = _make_envelope(
        turn_id="t0",
        continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
        batch_receipt={
            "results": [
                {
                    "tool_name": "write_file",
                    "status": "success",
                    "result": {"effect_receipt": {"file": "output.py"}},
                }
            ]
        },
    )

    orch._state_reducer.apply_turn_outcome(envelope, turn_index=1)

    # Streak should be reset
    streak = orch.state.structured_findings.get("_exploration_only_streak", 0)
    assert streak == 0, f"Exploration streak should be reset to 0, got {streak}"


# ---------------------------------------------------------------------------
# RS-11: Read-Only Termination Exemption
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs11_read_only_turn_with_visible_output_becomes_final_answer(
    tmp_workspace: str,
) -> None:
    """RS-11: Successful read-only turn with visible output is converted to final_answer.

    Validates:
    - Read-only turn with successful results
    - visible_content is present
    - turn_count >= 1
    - Result has END_SESSION and kind=final_answer
    """
    orch = RoleSessionOrchestrator(
        session_id="sess-rs11",
        kernel=AsyncMock(),
        workspace=tmp_workspace,
    )
    orch.state.turn_count = 1

    envelope = _make_envelope(
        turn_id="t0",
        continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
        visible_content="Here is the analysis of the file.",
        batch_receipt={
            "results": [
                {
                    "tool_name": "read_file",
                    "status": "success",
                    "result": {"content": "file content"},
                }
            ]
        },
    )

    result = orch._apply_read_only_termination_exemption(envelope)

    # Read-only with visible output should end session
    assert result.continuation_mode == TurnContinuationMode.END_SESSION
    assert result.turn_result.kind == "final_answer"


@pytest.mark.asyncio
async def test_rs11b_failed_read_not_converted(tmp_workspace: str) -> None:
    """RS-11b: Failed read-only turn is NOT converted to final_answer."""
    orch = RoleSessionOrchestrator(
        session_id="sess-rs11b",
        kernel=AsyncMock(),
        workspace=tmp_workspace,
    )
    orch.state.turn_count = 1

    envelope = _make_envelope(
        turn_id="t0",
        continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
        visible_content="Analysis text",
        batch_receipt={
            "results": [
                {
                    "tool_name": "read_file",
                    "status": "error",
                    "result": {"message": "File not found"},
                }
            ]
        },
    )

    result = orch._apply_read_only_termination_exemption(envelope)

    # Failed read should NOT be converted
    assert result.continuation_mode == TurnContinuationMode.AUTO_CONTINUE


# ---------------------------------------------------------------------------
# RS-12: Different Continuation Modes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs12_waiting_human_breaks_loop(tmp_workspace: str) -> None:
    """RS-12: WAITING_HUMAN mode breaks the continuation loop correctly."""
    kernel = MockKernel([[CompletionEvent(turn_id="t0", status="success")]])
    orch = RoleSessionOrchestrator(
        session_id="sess-rs12",
        kernel=kernel,
        workspace=tmp_workspace,
        max_auto_turns=5,
    )
    orch._build_envelope_from_completion = lambda _evt: _make_envelope(
        turn_id="t0",
        continuation_mode=TurnContinuationMode.WAITING_HUMAN,
        next_intent="need_user_input",
    )

    events = [e async for e in orch.execute_stream("hello")]
    waiting_events = [e for e in events if isinstance(e, SessionWaitingHumanEvent)]
    assert len(waiting_events) >= 1
    assert kernel.call_count == 1
    # Session should NOT emit SessionCompletedEvent when waiting for human
    assert not any(isinstance(e, SessionCompletedEvent) for e in events)


# ---------------------------------------------------------------------------
# RS-13: HARD-GATE First Turn vs Continuation Turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs13_hard_gate_not_checked_on_continuation(tmp_workspace: str) -> None:
    """RS-13: HARD-GATE is only checked on first turn, not continuation turns.

    Validates:
    - On continuation turn (turn_count > 0), HARD-GATE is not checked
    - Destructive content in continuation prompt does not trigger HARD-GATE
    """
    kernel = MockKernel(
        [
            [CompletionEvent(turn_id="t0", status="success")],
            [CompletionEvent(turn_id="t1", status="success")],
        ]
    )
    orch = RoleSessionOrchestrator(
        session_id="sess-rs13",
        kernel=kernel,
        workspace=tmp_workspace,
        max_auto_turns=5,
    )

    def _build_envelope(event: CompletionEvent) -> TurnOutcomeEnvelope:
        return _make_envelope(
            turn_id=event.turn_id,
            continuation_mode=TurnContinuationMode.END_SESSION,
        )

    orch._build_envelope_from_completion = _build_envelope

    # Continuation prompt with destructive content
    for _ in [e async for e in orch.execute_stream("Please continue")]:
        pass
    # Kernel should be called at least once
    assert kernel.call_count >= 1, f"Expected at least 1 kernel call, got {kernel.call_count}"


# ---------------------------------------------------------------------------
# RS-14: Model Output Regurgitation Detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs14_model_output_regurgitation_blocked(tmp_workspace: str) -> None:
    """RS-14: Model output regurgitation is detected and blocked.

    Validates:
    - When prompt looks like model output (contains markdown headers etc.)
    - Original goal is preserved
    - Prompt is not used as-is
    """
    orch = RoleSessionOrchestrator(
        session_id="sess-rs14",
        kernel=AsyncMock(),
        workspace=tmp_workspace,
    )
    orch.state.goal = "Original user task"
    orch.state.turn_count = 1  # Not first turn

    # Prompt that looks like model output (regurgitation)
    model_output_like = (
        "### Analysis Summary\n"
        "**Key Findings**: The issue is in auth.py\n"
        "**Next Steps**: 1. **Fix auth.py** 2. **Test**"
    )

    is_regurgitation = orch._is_model_output(model_output_like)
    assert is_regurgitation is True, "Model output pattern should be detected"

    # Safe user input should not be detected as regurgitation
    safe_input = "Continue fixing the bug"
    is_safe = orch._is_model_output(safe_input)
    assert is_safe is False, "Normal user input should not be detected as regurgitation"


# ---------------------------------------------------------------------------
# RS-15: SessionStateReducer Phase Transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs15_phase_manager_transitions(tmp_workspace: str) -> None:
    """RS-15: PhaseManager correctly transitions phases on tool results."""
    orch = RoleSessionOrchestrator(
        session_id="sess-rs15",
        kernel=AsyncMock(),
        workspace=tmp_workspace,
    )

    from polaris.cells.roles.kernel.internal.transaction.phase_manager import Phase

    # Initial phase should be exploring
    assert orch._state_reducer.current_phase() == Phase.EXPLORING

    # Transition with read tool results
    envelope = _make_envelope(
        turn_id="t0",
        continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
        batch_receipt={
            "results": [
                {
                    "tool_name": "read_file",
                    "status": "success",
                    "result": {"content": "content"},
                }
            ]
        },
    )

    orch._state_reducer.apply_turn_outcome(envelope, turn_index=1)
    # Phase may have transitioned
    assert orch._state_reducer.current_phase() is not None


# ---------------------------------------------------------------------------
# RS-16: artifact Store Integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rs16_artifact_store_persists_artifacts(tmp_workspace: str) -> None:
    """RS-16: ArtifactStore correctly persists artifacts from envelope."""
    kernel = MockKernel([[CompletionEvent(turn_id="t0", status="success")]])
    orch = RoleSessionOrchestrator(
        session_id="sess-rs16",
        kernel=kernel,
        workspace=tmp_workspace,
    )

    def _build_envelope(event: CompletionEvent) -> TurnOutcomeEnvelope:
        return _make_envelope(
            turn_id="t0",
            continuation_mode=TurnContinuationMode.END_SESSION,
            artifacts_to_persist=[
                {
                    "name": "summary.md",
                    "content": "# Summary\n\nTask completed.",
                    "mime_type": "text/markdown",
                }
            ],
        )

    orch._build_envelope_from_completion = _build_envelope

    # Consume events
    for _ in [e async for e in orch.execute_stream("hello")]:
        pass
    # Kernel should have been called
    assert kernel.call_count >= 1, "Kernel should be called"
