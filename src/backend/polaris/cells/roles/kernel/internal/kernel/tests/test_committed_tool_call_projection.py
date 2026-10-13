"""Native history uses the sealed invocation, never receipt argument guesses."""

from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.kernel.role_result_projection import tool_calls_from_batch_receipt


def _outcome() -> dict[str, Any]:
    return {
        "execution": {
            "invocations": [{"call_id": "read-1", "tool_name": "read_file", "arguments": {"file": "answer.py"}}],
            "receipt": {
                "batch_id": "batch-1",
                "results": [{"call_id": "read-1", "tool_name": "read_file", "status": "success"}],
            },
        }
    }


def test_receipt_without_args_projects_exact_sealed_invocation() -> None:
    receipt: dict[str, Any] = {
        "batch_id": "batch-1",
        "results": [{"call_id": "read-1", "tool_name": "read_file", "status": "success"}],
    }
    assert tool_calls_from_batch_receipt(receipt, turn_outcome=_outcome()) == [
        {"tool": "read_file", "args": {"file": "answer.py"}, "call_id": "read-1"},
    ]


@pytest.mark.parametrize("field,value", [("call_id", "foreign"), ("tool_name", "write_file"), ("status", "error")])
def test_receipt_drift_cannot_change_sealed_native_history(field: str, value: str) -> None:
    receipt: dict[str, Any] = {
        "batch_id": "batch-1",
        "results": [{"call_id": "read-1", "tool_name": "read_file", "status": "success"}],
    }
    receipt["results"][0][field] = value
    with pytest.raises(RuntimeError, match="committed_tool_receipt_invocation_mismatch"):
        tool_calls_from_batch_receipt(receipt, turn_outcome=_outcome())


def test_receipt_without_args_or_sealed_invocation_cannot_guess_empty_args() -> None:
    receipt = {"results": [{"call_id": "read-1", "tool_name": "read_file"}]}
    with pytest.raises(RuntimeError, match="committed_tool_receipt_arguments_missing"):
        tool_calls_from_batch_receipt(receipt)


def test_durable_commit_retains_bootstrap_and_final_batch_invocations(tmp_path) -> None:
    from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
    from polaris.cells.roles.kernel.internal.transaction.outcome_commit import commit_turn_result
    from polaris.cells.roles.kernel.public.turn_contracts import ToolInvocation, TurnDecision
    from polaris.cells.roles.kernel.tests.test_directed_effect_lifecycle import _setup_attempt

    _setup_attempt(str(tmp_path))
    ledger = TurnLedger(turn_id="turn-joined")
    read = ToolInvocation(call_id="original-read", tool_name="read_file", arguments={"file": "answer.py"})
    write = ToolInvocation(
        call_id="original-write", tool_name="write_file", arguments={"file": "answer.py", "content": "VALUE=42\n"}
    )
    ledger.record_invocation_batch("bootstrap", [read])
    ledger.record_invocation_batch("final", [write])
    ledger.record_decision(
        TurnDecision.model_validate(
            {
                "turn_id": "turn-joined",
                "kind": "tool_batch",
                "tool_batch": {"batch_id": "final", "invocations": [write]},
                "visible_message": "",
                "finalize_mode": "none",
                "domain": "code",
            }
        )
    )
    receipt = {
        "batch_id": "bootstrap",
        "turn_id": "turn-joined",
        "results": [
            {"call_id": "original-write", "tool_name": "write_file", "status": "success", "result": {"ok": True}},
            {"call_id": "original-read", "tool_name": "read_file", "status": "success", "result": ""},
        ],
        "success_count": 2,
        "failure_count": 0,
    }
    committed = commit_turn_result(
        workspace=str(tmp_path),
        run_id="run-joined",
        task_id="task-joined",
        transition_id="transition-joined",
        ledger=ledger,
        result={"kind": "continue_multi_turn", "batch_receipt": receipt},
        completion_status="success",
    )
    from polaris.cells.events.fact_stream.public import QueryFactEventsV1, query_fact_events
    from polaris.cells.roles.kernel.public.turn_contracts import TurnOutcome

    events = query_fact_events(
        QueryFactEventsV1(workspace=str(tmp_path), stream="roles.kernel.turn_outcomes", strict_integrity=True)
    )
    assert events.events[0]["event_id"] == committed.receipt.fact_event_id
    persisted = events.events[0]["payload"]["outcome"]
    payload = TurnOutcome.model_validate(persisted).model_dump(mode="json")
    assert payload["execution"]["invocation_batches"][0]["invocations"][0]["call_id"] == "original-read"
    assert payload["execution"]["batch_id"] == "bootstrap"
    assert {item["call_id"] for item in payload["execution"]["invocations"]} == {"original-read", "original-write"}
    assert tool_calls_from_batch_receipt(receipt, turn_outcome=payload) == [
        {"tool": "write_file", "args": {"file": "answer.py", "content": "VALUE=42\n"}, "call_id": "original-write"},
        {"tool": "read_file", "args": {"file": "answer.py"}, "call_id": "original-read"},
    ]


@pytest.mark.parametrize("change", ["duplicate", "result", "effect", "omitted"])
def test_committed_receipt_cannot_be_forged_or_partially_projected(change: str) -> None:
    from copy import deepcopy

    outcome = _outcome()
    original = outcome["execution"]["receipt"]
    original["results"][0]["result"] = "original read observation"
    receipt = deepcopy(original)
    if change == "duplicate":
        receipt["results"].append(deepcopy(receipt["results"][0]))
    elif change == "result":
        receipt["results"][0]["result"] = "forged read observation"
    elif change == "effect":
        receipt["results"][0]["effect_receipt"] = {"ok": True, "path": "forged.py"}
    else:
        outcome["execution"]["receipt"]["results"].append(
            {"call_id": "other", "tool_name": "read_file", "status": "success"}
        )
    with pytest.raises(RuntimeError, match="committed_tool_receipt_invocation_mismatch"):
        tool_calls_from_batch_receipt(receipt, turn_outcome=outcome)


def test_prepared_inventory_is_detached_from_mutable_caller_input() -> None:
    from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
    from polaris.cells.roles.kernel.public.turn_contracts import ToolInvocation

    invocation = ToolInvocation(
        call_id="detached", tool_name="write_file", arguments={"file": "answer.py", "content": "original"}
    )
    ledger = TurnLedger(turn_id="detached")
    ledger.record_invocation_batch("detached-batch", [invocation])
    invocation.arguments["content"] = "changed after capture"
    assert ledger.invocation_batches[0].invocations[0].arguments["content"] == "original"
    with pytest.raises(RuntimeError, match="committed_tool_invocation_identity_collision"):
        ledger.record_invocation_batch("forged-second-batch", [invocation])


def test_native_alias_preserves_original_arguments_through_preparation() -> None:
    from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import ToolBatchExecutor
    from polaris.cells.roles.kernel.internal.turn_decision_decoder import TurnDecisionDecoder

    native = {
        "type": "tool_use",
        "id": "original-native",
        "name": "create_file",
        "input": {"path": "answer.py", "text": "VALUE=42\n"},
    }
    invocation = TurnDecisionDecoder()._parse_native_tool(native)
    assert invocation.arguments == {"file": "answer.py", "content": "VALUE=42\n"}
    assert invocation.native_arguments == native["input"]
    prepared = ToolBatchExecutor._canonicalize_directed_effect_invocations([invocation])[0]
    assert prepared.call_id == "original-native"
    assert prepared.raw_tool_name == "create_file"
    assert prepared.tool_name == "write_file"
    assert prepared.native_arguments == native["input"]
    native["input"]["text"] = "caller changed source"
    assert prepared.native_arguments == {"path": "answer.py", "text": "VALUE=42\n"}


def test_preparation_rejects_typed_invocation_with_forged_effect_class() -> None:
    from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import ToolBatchExecutor
    from polaris.cells.roles.kernel.public.turn_contracts import ToolEffectType, ToolInvocation

    invocation = ToolInvocation(
        call_id="forged-effect", tool_name="execute_command", arguments={"command": "python verify.py"}
    )
    forged = invocation.model_copy(update={"effect_type": ToolEffectType.READ})
    with pytest.raises(RuntimeError, match="directed_effect_batch_classification_failed"):
        ToolBatchExecutor._canonicalize_directed_effect_invocations([forged])


def test_missing_receipt_keeps_attempted_mutation_class_and_inputs() -> None:
    from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
    from polaris.cells.roles.kernel.internal.transaction.outcome_commit import _execution_from_result
    from polaris.cells.roles.kernel.public.turn_contracts import ToolInvocation, TurnDecision

    invocation = ToolInvocation(
        call_id="unknown-write", tool_name="write_file", arguments={"file": "answer.py", "content": "VALUE=42\n"}
    )
    ledger = TurnLedger(turn_id="unknown-write")
    ledger.record_invocation_batch("unknown-batch", [invocation])
    ledger.record_decision(
        TurnDecision.model_validate(
            {
                "turn_id": "unknown-write",
                "kind": "tool_batch",
                "tool_batch": {"batch_id": "unknown-batch", "invocations": [invocation]},
                "visible_message": "",
                "finalize_mode": "none",
                "domain": "code",
            }
        )
    )
    execution = _execution_from_result(ledger=ledger, result={"kind": "failed"})
    assert execution is not None
    assert execution.receipt is None
    assert execution.side_effect_class == "local_write"
    assert [item.call_id for item in execution.invocations] == ["unknown-write"]


@pytest.mark.parametrize("omitted", ["read", "write", "failed", "denied", "superseded"])
def test_nonempty_partial_receipt_cannot_erase_captured_batch_member(omitted: str) -> None:
    from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
    from polaris.cells.roles.kernel.internal.transaction.outcome_commit import (
        TurnOutcomeCommitError,
        _execution_from_result,
    )
    from polaris.cells.roles.kernel.public.turn_contracts import ToolInvocation, TurnDecision

    ledger = TurnLedger(turn_id="partial-turn")
    specifications = {
        "read": ("read_file", {"file": "answer.py"}),
        "write": ("write_file", {"file": "answer.py", "content": "VALUE=42\n"}),
        "failed": ("execute_command", {"command": "python verify.py"}),
        "denied": ("execute_command", {"command": "pwd && ls -la"}),
        "superseded": ("write_file", {"file": "answer.py", "content": "VALUE=0\n"}),
    }
    selected = ("read", "write") if omitted in {"read", "write"} else ("read", omitted)
    specifications = {name: specifications[name] for name in selected}
    invocations = [
        ToolInvocation(call_id=call_id, tool_name=name, arguments=args)
        for call_id, (name, args) in specifications.items()
    ]
    ledger.record_invocation_batch("partial-batch", invocations)
    ledger.record_decision(
        TurnDecision.model_validate(
            {
                "turn_id": "partial-turn",
                "kind": "tool_batch",
                "tool_batch": {"batch_id": "partial-batch", "invocations": invocations},
                "visible_message": "",
                "finalize_mode": "none",
                "domain": "code",
            }
        )
    )
    rows = [
        {
            "call_id": item.call_id,
            "tool_name": item.tool_name,
            "status": "error" if item.call_id in {"failed", "denied"} else "success",
            "result": {"superseded": True, "no_op": True}
            if item.call_id == "superseded"
            else {"ok": item.call_id not in {"failed", "denied"}},
        }
        for item in invocations
        if item.call_id != omitted
    ]
    receipt = {
        "batch_id": "partial-batch",
        "turn_id": "partial-turn",
        "results": rows,
        "success_count": sum(row["status"] == "success" for row in rows),
        "failure_count": sum(row["status"] == "error" for row in rows),
    }
    with pytest.raises(TurnOutcomeCommitError, match="committed_tool_receipt_invocation_mismatch"):
        _execution_from_result(ledger=ledger, result={"kind": "continue_multi_turn", "batch_receipt": receipt})
    assert [item.call_id for item in ledger.invocation_batches[0].invocations] == list(specifications)
