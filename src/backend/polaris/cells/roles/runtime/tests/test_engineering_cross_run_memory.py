from __future__ import annotations

from polaris.cells.roles.runtime.internal.engineering_memory_cards import build_prior_run_memory


def test_prior_run_memory_carries_unresolved_failures() -> None:
    prior_run = {
        "session_id": "prior-session-1234",
        "instruction": "build",
        "verification": "failed",
    }
    prior_events = [
        {
            "seq": 1,
            "kind": "tool_observed",
            "payload": {
                "tool": "execute_command",
                "ok": False,
                "content": '{"command_card": {"summary": "browser launch failed", "content_ref": "command:old"}}',
            },
        }
    ]
    messages = build_prior_run_memory(prior_run, prior_events)
    assert len(messages) == 1 and messages[0]["role"] == "system"
    assert "上一轮工程" in messages[0]["content"]
    assert "browser launch failed" in messages[0]["content"]


def test_prior_run_memory_empty_when_no_unresolved_failures() -> None:
    prior_run = {"session_id": "prior-session", "instruction": "build", "verification": "passed"}
    assert build_prior_run_memory(prior_run, []) == []
