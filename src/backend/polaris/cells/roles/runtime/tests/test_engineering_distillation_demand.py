"""A paid summary needs a consumer in the role's projected native history."""

from __future__ import annotations

import json

from polaris.cells.roles.runtime.internal.engineering_command_cards import build_command_card
from polaris.cells.roles.runtime.internal.engineering_semantic_runtime import facts_for_advice
from polaris.cells.roles.runtime.internal.engineering_tool_payload_projection import distillation_targets


def test_only_retained_payloads_that_can_actually_fold_are_requested() -> None:
    events = []
    messages = []
    for index, (name, length) in enumerate((("evicted", 6000), ("small", 3000), ("visible", 5000))):
        card = build_command_card("check", name * length, "", 0, "command:" + name)
        card["stdout_preview"] = "x" * length
        content = json.dumps({"command_card": card})
        payload = {"tool": "execute_command", "ok": True, "call_id": name, "content": content}
        events.append({"seq": index + 1, "role": "director", "kind": "tool_observed", "payload": payload})
        if name != "evicted":
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "tool_calls": [{"id": name, "function": {"name": "execute_command", "arguments": "{}"}}],
                    },
                    {"role": "tool", "tool_call_id": name, "content": content},
                ]
            )
    events.extend({"kind": "turn_started", "seq": n + 4, "role": "director", "payload": {}} for n in range(22))
    messages.extend({"role": "assistant", "content": "next"} for _ in range(15))
    needs = distillation_targets(messages, fold_window=24)
    req, _, _ = facts_for_advice({"instruction": "fix"}, events, targets=needs, role="director")
    assert [item["snapshot_id"] for item in req["candidates"]] == ["command:visible"]
    empty, _, _ = facts_for_advice({}, events, targets={}, role="director")
    assert empty["candidates"] == []


def test_observed_failed_command_can_receive_a_summary_after_it_folds() -> None:
    card = build_command_card("build", "log " * 2000, "TypeError: missing attribute", 1, "command:failed")
    raw = json.dumps({"command_card": card})
    events = [
        {
            "seq": 1,
            "role": "director",
            "kind": "tool_observed",
            "payload": {"tool": "execute_command", "call_id": "c", "ok": False, "exit_code": 1, "content": raw},
        }
    ]
    folded = {**card, "fold_state": "FOLDED", "summary_kind": "structural", "stdout_preview": "", "stderr_preview": ""}
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "c", "function": {"name": "execute_command", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c", "content": json.dumps({"command_card": folded})},
    ]
    needs = distillation_targets(messages, fold_window=24)
    req, _, _ = facts_for_advice({}, events, targets=needs, role="director")
    assert len(req["candidates"]) == 1
    assert "TypeError" in req["candidates"][0]["content"]


def test_replaced_write_uses_host_envelope_unless_history_is_requested() -> None:
    body = "export const transform = (value) => value + 1;\n" * 100
    events = [
        {
            "seq": 1,
            "kind": "tool_started",
            "role": "director",
            "payload": {
                "tool": "write_file",
                "call_id": "w1",
                "turn_seq": 1,
                "arguments": {"file": "a.ts", "content": body},
            },
        },
        {
            "seq": 2,
            "kind": "tool_observed",
            "role": "director",
            "payload": {"tool": "write_file", "call_id": "w1", "turn_seq": 1, "ok": True},
        },
        {
            "seq": 3,
            "kind": "tool_started",
            "role": "director",
            "payload": {
                "tool": "edit_file",
                "call_id": "e2",
                "turn_seq": 3,
                "arguments": {"file": "a.ts", "search": "value + 1", "replace": "value + 2"},
            },
        },
        {
            "seq": 4,
            "kind": "tool_observed",
            "role": "director",
            "payload": {"tool": "edit_file", "call_id": "e2", "turn_seq": 3, "ok": True},
        },
    ]
    demand = {("w1", "write:a.ts"): True}
    req, _, _ = facts_for_advice(
        {"instruction": "Finish the current implementation"}, events, targets=demand, role="director"
    )
    assert req["candidates"] == []
    requested, _, _ = facts_for_advice(
        {"instruction": "Explain the historical write at call w1"}, events, targets=demand, role="director"
    )
    assert [item["call_id"] for item in requested["candidates"]] == ["w1"]
    events[-1]["payload"]["ok"] = False
    unchanged, _, _ = facts_for_advice({}, events, targets=demand, role="director")
    assert [item["call_id"] for item in unchanged["candidates"]] == ["w1"]
