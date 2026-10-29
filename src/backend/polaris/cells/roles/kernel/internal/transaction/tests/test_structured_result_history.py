"""Result-only tool surfaces cannot retrieve pointerized conversation turns."""

from copy import deepcopy

from polaris.cells.roles.kernel.internal.transaction.decision_message_builder import build_decision_messages
from polaris.cells.roles.kernel.internal.transaction.tests.test_decision_message_builder import _structured_result_tool


def test_result_submission_omits_unreadable_history_and_preserves_current_contract() -> None:
    pointer = "[Large content stored in receipt evt_old]\n\n[receipt_ref:evt_old]"
    context = [
        {"role": "system", "content": "You are Chief Engineer. PM authority: TASK-1, src/main.ts."},
        {"role": "assistant", "content": pointer},
        {"role": "user", "content": pointer},
        {"role": "user", "content": pointer + "\n\nReconstruct from the current validated PM contract."},
    ]
    before = deepcopy(context)

    messages = build_decision_messages(context, [_structured_result_tool()])

    assert [m["content"] for m in messages if m["role"] == "user"] == [
        "Reconstruct from the current validated PM contract."
    ]
    assert not any(m["role"] == "assistant" for m in messages)
    assert messages[0] == before[0]
    assert context == before


def test_result_submission_preserves_readable_history_and_literal_pointer_examples() -> None:
    context = [
        {"role": "user", "content": "Earlier requirement: preserve CLI output."},
        {"role": "assistant", "content": "Current candidate has three task plans."},
        {
            "role": "user",
            "content": "Explain the literal [receipt_ref:example] notation and preserve that requirement.",
        },
    ]

    messages = build_decision_messages(context, [_structured_result_tool()])

    assert messages[:3] == context


def test_tool_turn_retains_history_for_available_receipt_reader() -> None:
    pointer = "[Large content stored in receipt evt_old]\n\n[receipt_ref:evt_old]"
    context = [{"role": "assistant", "content": pointer}, {"role": "user", "content": "Read the task context."}]
    tools = [{"type": "function", "function": {"name": "read_artifact", "parameters": {"type": "object"}}}]

    messages = build_decision_messages(context, tools)

    assert messages[:2] == context


def test_pm_workspace_readers_cannot_resolve_opaque_receipt_history() -> None:
    pointer = "[Large content stored in receipt evt_old]\n\n[receipt_ref:evt_old]"
    context = [
        {"role": "system", "content": "You are PM."},
        {"role": "assistant", "content": pointer},
        {"role": "user", "content": pointer},
        {"role": "user", "content": 'Correct the current complete task plan: {"tasks":[]}'},
    ]
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]
    messages = build_decision_messages(context, tools)
    assert not any("evt_old" in str(row.get("content", "")) for row in messages)
    assert any(row.get("content") == context[-1]["content"] for row in messages)


def test_native_tool_exchange_identity_is_preserved_with_workspace_readers() -> None:
    pointer = "[Large content stored in receipt evt_old]\n\n[receipt_ref:evt_old]"
    context = [
        {"role": "assistant", "content": pointer, "tool_calls": [{"id": "call-1"}]},
        {"role": "tool", "content": pointer, "tool_call_id": "call-1"},
        {"role": "user", "content": "Continue the original task."},
    ]
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]
    assert build_decision_messages(context, tools)[:3] == context
