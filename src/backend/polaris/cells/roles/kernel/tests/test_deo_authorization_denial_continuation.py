"""No-effect DEO admission denials remain failed observations for continuation."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.directed_effect_policy_guard import DirectedEffectPolicyGuardResultV1
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import DeliveryMode
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import _executor_core
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState
from polaris.cells.roles.kernel.tests.test_directed_effect_runtime_wiring import _attempt, _dependencies
from polaris.cells.roles.kernel.tests.test_tool_batch_executor_decision_tree import (
    _build_decoded_state_machine,
    _decision,
    _make_executor,
)
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptValidationVerdictV1,
)


class _DeniedRuntime:
    def __init__(self, code: str) -> None:
        self.code = code
        self.physical_calls: list[tuple[str, dict[str, Any]]] = []

    def directed_effect_policy_guard(self, _port: Any) -> _DeniedRuntime:
        return self

    async def evaluate_authoritative(self, _request: Any) -> DirectedEffectPolicyGuardResultV1:
        return DirectedEffectPolicyGuardResultV1(
            status="denied",
            error_code=self.code,
            preflight=None,
            snapshot=None,
            authorization_binding=None,
            public_policy_evidence=None,
            current_job_token_restriction_evidence=None,
        )

    async def __call__(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.physical_calls.append((tool, arguments))
        raise AssertionError("Denied batch must never execute any member")


def _executor(monkeypatch: pytest.MonkeyPatch, code: str, *, workspace: str, current: bool = True):
    runtime = _DeniedRuntime(code)
    token = {
        "token_id": "original-token",
        "execution_envelope_hash": "a" * 64,
        "capability_audit_ok": True,
        "verification_command_scope": [
            {"command": "python check.py", "cwd": ".", "argv": ["python", "check.py"], "authority_hash": "b" * 64},
        ],
    }
    executor = _make_executor(
        config=TransactionConfig(
            role_id="director", mutation_guard_mode="strict", workspace=workspace, run_id="same-run", task_id="TASK-1"
        ),
        tool_runtime=runtime,
        capability_token=token,
    )
    attempt = replace(_attempt(), workspace=workspace, external_task_id="TASK-1", run_id="same-run")
    executor.directed_effect_required = True
    executor.directed_effect_runtime = _dependencies()
    executor.directed_effect_execution_attempt = attempt
    executor.directed_effect_execution_attempt_authority = TaskRuntimeExecutionAttemptAuthorityV1(attempt)

    def validate(query):
        return TaskRuntimeExecutionAttemptValidationVerdictV1(
            valid=current,
            code="valid" if current else "session_not_found",
            workspace=query.workspace,
            identity=query.identity,
        )

    monkeypatch.setattr(_executor_core, "validate_task_runtime_execution_attempt", validate, raising=False)
    return executor, runtime, token


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["deo_command_scope_denied", "deo_path_scope_denied", "deo_tool_not_allowed"])
@pytest.mark.parametrize("continuous", [False, True])
async def test_ordinary_admission_denial_returns_failed_no_effect_receipt(monkeypatch, tmp_path, code, continuous):
    executor, runtime, token = _executor(monkeypatch, code, workspace=str(tmp_path))
    executor.config.continuous_execution = continuous
    turn = "same-task-turn-0"
    decision = _decision(
        turn,
        "denied-batch",
        [
            {"call_id": "bad-command", "tool_name": "execute_command", "arguments": {"command": "pwd && ls -la"}},
        ],
    )
    decision["metadata"].update(workspace=str(tmp_path), run_id="same-run", task_id="TASK-1")
    state = _build_decoded_state_machine(turn)
    ledger = TurnLedger(turn_id=turn)
    if continuous:
        ledger._original_delivery_mode = DeliveryMode.MATERIALIZE_CHANGES.value
    result = await executor.execute_tool_batch(
        decision,
        state,
        ledger,
        [{"role": "user", "content": "Execute approved recipe"}],
        stream=False,
    )
    assert result["kind"] == ("continue_multi_turn" if continuous else "tool_batch_with_receipt")
    assert state.current_state == (TurnState.TOOL_BATCH_EXECUTED if continuous else TurnState.COMPLETED)
    assert runtime.physical_calls == []
    row = result["batch_receipt"]["results"][0]
    assert row["call_id"] == "bad-command"
    assert row["result"]["ok"] is False
    assert row["result"]["error_type"] == code
    assert row["effect_receipt"] is None
    assert row["directed_effect_claim_status"] == "not_claimed"
    assert row["result"]["approved_command_scope"] == [
        {"command": item["command"], "cwd": item["cwd"]} for item in token["verification_command_scope"]
    ]
    assert any(flag["type"] == "DEO_ABORT" for flag in ledger.anomaly_flags)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    ["deo_job_token_invalid", "deo_authorization_hash_drift", "deo_context_replayed", "deo_director_policy_denied"],
)
async def test_authority_denial_remains_fatal(monkeypatch, tmp_path, code):
    executor, runtime, _token = _executor(monkeypatch, code, workspace=str(tmp_path))
    turn = "fatal-authority-turn"
    decision = _decision(
        turn,
        "fatal-batch",
        [
            {"call_id": "bad-command", "tool_name": "execute_command", "arguments": {"command": "pwd && ls -la"}},
        ],
    )
    decision["metadata"].update(workspace=str(tmp_path), run_id="same-run", task_id="TASK-1")
    with pytest.raises(RuntimeError, match=code):
        await executor.execute_tool_batch(
            decision,
            _build_decoded_state_machine(turn),
            TurnLedger(turn_id=turn),
            [{"role": "user", "content": "Execute approved recipe"}],
            stream=False,
        )
    assert runtime.physical_calls == []


@pytest.mark.asyncio
async def test_scope_denial_with_invalid_current_attempt_remains_fatal(monkeypatch, tmp_path):
    executor, runtime, _token = _executor(
        monkeypatch, "deo_command_scope_denied", workspace=str(tmp_path), current=False
    )
    with pytest.raises(RuntimeError, match="deo_execution_attempt_invalid"):
        await executor._prepare_directed_effect_dispatch(
            invocations=[
                {"call_id": "bad-command", "tool_name": "execute_command", "arguments": {"command": "pwd && ls -la"}}
            ],
            workspace=str(tmp_path),
            turn_id="same-task-turn",
            batch_id="no-regrant",
        )
    assert runtime.physical_calls == []


@pytest.mark.asyncio
async def test_persisted_validation_requires_typed_matching_verdict(monkeypatch, tmp_path):
    executor, runtime, _token = _executor(monkeypatch, "deo_command_scope_denied", workspace=str(tmp_path))
    monkeypatch.setattr(
        _executor_core, "validate_task_runtime_execution_attempt", lambda _query: SimpleNamespace(valid=True)
    )
    with pytest.raises(RuntimeError, match="deo_execution_attempt_invalid"):
        await executor._prepare_directed_effect_dispatch(
            invocations=[
                {"call_id": "bad-command", "tool_name": "execute_command", "arguments": {"command": "pwd && ls -la"}}
            ],
            workspace=str(tmp_path),
            turn_id="same-task-turn",
            batch_id="no-regrant",
        )
    assert runtime.physical_calls == []


@pytest.mark.asyncio
async def test_foreign_execution_frame_cannot_continue_denied_call(monkeypatch, tmp_path):
    executor, runtime, _token = _executor(monkeypatch, "deo_command_scope_denied", workspace=str(tmp_path))
    executor.config.run_id = "foreign-run"
    with pytest.raises(RuntimeError, match="deo_execution_attempt_invalid"):
        await executor._prepare_directed_effect_dispatch(
            invocations=[
                {"call_id": "bad-command", "tool_name": "execute_command", "arguments": {"command": "pwd && ls -la"}}
            ],
            workspace=str(tmp_path),
            turn_id="same-task-turn",
            batch_id="no-regrant",
        )
    assert runtime.physical_calls == []


@pytest.mark.asyncio
async def test_later_authority_failure_is_not_hidden_by_ordinary_denial(monkeypatch, tmp_path):
    executor, runtime, _token = _executor(monkeypatch, "deo_command_scope_denied", workspace=str(tmp_path))

    async def evaluate(request):
        code = (
            "deo_authorization_hash_drift"
            if request.invocation.call_id == "broken-proof"
            else "deo_command_scope_denied"
        )
        return DirectedEffectPolicyGuardResultV1(
            status="denied",
            error_code=code,
            preflight=None,
            snapshot=None,
            authorization_binding=None,
            public_policy_evidence=None,
            current_job_token_restriction_evidence=None,
        )

    runtime.evaluate_authoritative = evaluate
    with pytest.raises(RuntimeError, match="deo_authorization_hash_drift"):
        await executor._prepare_directed_effect_dispatch(
            invocations=[
                {"call_id": call_id, "tool_name": "execute_command", "arguments": {"command": "pwd && ls -la"}}
                for call_id in ("bad-command", "broken-proof")
            ],
            workspace=str(tmp_path),
            turn_id="same-task-turn",
            batch_id="no-regrant",
        )
    assert runtime.physical_calls == []


@pytest.mark.asyncio
async def test_superseded_write_does_not_hide_later_scope_feedback(monkeypatch, tmp_path):
    executor, runtime, _token = _executor(monkeypatch, "deo_path_scope_denied", workspace=str(tmp_path))
    executor.config.continuous_execution = True
    turn = "denied-repeated-write"
    ledger = TurnLedger(turn_id=turn)
    ledger._original_delivery_mode = DeliveryMode.MATERIALIZE_CHANGES.value
    decision = _decision(
        turn,
        "same-path-batch",
        [
            {
                "call_id": "previous",
                "tool_name": "write_file",
                "arguments": {"file": "outside.py", "content": "VALUE = 1\n"},
            },
            {
                "call_id": "latest",
                "tool_name": "write_file",
                "arguments": {"file": "outside.py", "content": "VALUE = 2\n"},
            },
        ],
    )
    decision["metadata"].update(workspace=str(tmp_path), run_id="same-run", task_id="TASK-1")
    result = await executor.execute_tool_batch(
        decision,
        _build_decoded_state_machine(turn),
        ledger,
        [{"role": "user", "content": "Execute approved recipe"}],
        stream=False,
    )
    assert result["kind"] == "continue_multi_turn"
    rows = {row["call_id"]: row for row in result["batch_receipt"]["results"]}
    assert rows["previous"]["status"] == "success"
    assert rows["previous"]["result"]["no_op"] is True
    assert rows["previous"]["result"]["superseded"] is True
    assert rows["latest"]["status"] == "error"
    assert rows["latest"]["error"] == "deo_path_scope_denied"
    assert all(
        row["effect_receipt"] is None and row["directed_effect_claim_status"] == "not_claimed" for row in rows.values()
    )
    assert runtime.physical_calls == []
    assert not (tmp_path / "outside.py").exists()
