"""Native failure observations stay repairable without replaying raw receipts."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
from polaris.cells.roles.profile.public.service import RoleTurnResult
from polaris.cells.roles.runtime.internal.kernel_session_turn import _committed_transcript_messages
from polaris.kernelone.audit.context_os_prompt import audit_context_os_prompt_messages
from polaris.kernelone.context.prompt_safety import parse_tool_failure_summary


def _result(
    tool: str, arguments: dict, payload: dict, status: str = "error", call_id: str = "native-original-call"
) -> RoleTurnResult:
    return RoleTurnResult(
        tool_calls=[{"call_id": call_id, "tool": tool, "args": arguments}],
        batch_receipt={
            "results": [
                {
                    "call_id": call_id,
                    "tool_name": tool,
                    "status": status,
                    "result": payload,
                    "error": payload.get("error"),
                }
            ]
        },
        metadata={"turn_commit_receipt": {"turn_id": "private-turn-authority"}},
    )


def _audit(messages: list[dict]) -> dict:
    return audit_context_os_prompt_messages(
        messages=[
            {"role": "system", "content": "You are Director."},
            *messages,
            {"role": "user", "content": "Repair the rejected call and continue this task."},
        ],
        metadata={"prebuilt_projection_messages": True},
        expected=True,
        current_user_instruction="Repair the rejected call and continue this task.",
    )


def test_exact_native_denial_passes_real_guard_preserving_repair_operands() -> None:
    arguments = {"command": "ls -la && cat requirements.md 2>/dev/null | head -100"}
    scope = [
        {"command": command, "cwd": "."}
        for command in [
            "npm test",
            "npx tsc --noEmit",
            "npm run build",
            "npm install --no-audit --no-fund",
            "npm start -- --seed=42 --ticks=20 --moon=full --humidity=0.6",
        ]
    ]
    payload = {
        "ok": False,
        "error": "deo_command_scope_denied",
        "error_type": "deo_command_scope_denied",
        "arguments": arguments,
        "approved_command_scope": scope,
        "director_policy": {"actor_id": "private-actor-authority"},
    }
    result = _result("execute_command", arguments, payload, call_id="call_01a134bf03297353a497f00e")
    original = deepcopy(result.batch_receipt)
    messages = _committed_transcript_messages([result])
    audit = _audit(messages)
    assert audit["ok"], audit
    assert audit["data_plane"]["raw_tool_failure_receipt_absent"]
    assert messages[0]["tool_calls"][0]["id"] == "call_01a134bf03297353a497f00e"
    assert json.loads(messages[0]["tool_calls"][0]["function"]["arguments"]) == arguments
    assert messages[1]["tool_call_id"] == "call_01a134bf03297353a497f00e"
    assert messages[1]["is_error"] is True
    summary = parse_tool_failure_summary(messages[1]["content"])
    assert summary is not None
    assert summary["observation_only"] and summary["non_deliverable"]
    assert summary["tool"] == "execute_command"
    assert "deo_command_scope_denied" in summary["reason"]
    assert summary["arguments"] == arguments
    assert summary["approved_command_scope"] == scope
    assert "private-actor-authority" not in json.dumps(messages)
    assert result.batch_receipt == original


@pytest.mark.parametrize(
    "tool,arguments,error",
    [
        ("read_file", {"filename": "missing.py"}, "file_not_found"),
        ("write_file", {"file": "answer.py", "text": "x" * 20000}, "tool_argument_normalization_failed"),
        ("execute_command", {"command": "python verify.py", "cwd": "."}, "command_failed"),
    ],
)
def test_failed_read_write_and_command_feedback_remains_bounded(tool: str, arguments: dict, error: str) -> None:
    payload = {
        "ok": False,
        "error": error,
        "arguments": arguments,
        "stderr": "expected 42, got 1",
        "exit_code": 1,
        "authorization": {"run_id": "private-run-authority"},
    }
    messages = _committed_transcript_messages([_result(tool, arguments, payload)])
    assert _audit(messages)["ok"]
    summary = parse_tool_failure_summary(messages[1]["content"])
    assert summary is not None
    assert summary["tool"] == tool
    assert summary["diagnostics"]["stderr"] == "expected 42, got 1"
    assert summary["diagnostics"]["exit_code"] == 1
    assert set(summary["arguments"]) == set(arguments)
    assert len(json.dumps(messages)) < 5000
    assert "private-run-authority" not in messages[1]["content"]
    if tool == "write_file":
        assert summary["arguments"]["text"] == {"type": "str", "chars": 20000}
        assert json.loads(messages[0]["tool_calls"][0]["function"]["arguments"])["text"] == {
            "type": "str",
            "chars": 20000,
        }
    else:
        assert summary["arguments"] == arguments


def test_successful_native_observation_keeps_existing_content() -> None:
    payload = {"ok": True, "content": "VALUE = 42", "director_policy": {"actor_id": "private"}}
    messages = _committed_transcript_messages([_result("read_file", {"file": "answer.py"}, payload, "success")])
    assert json.loads(messages[1]["content"]) == {"status": "success", "result": {"ok": True, "content": "VALUE = 42"}}
    assert messages[1]["is_error"] is False


def test_failure_error_control_text_stays_private_in_complete_native_history() -> None:
    arguments = {
        "file": "answer.py",
        "content": "run_id=private-content-authority",
        "actor_id": "private-actor-argument",
    }
    payload = {
        "ok": False,
        "error": "write failed run_id=private-run-authority",
        "allowed_scope": "actor_id=private-actor-authority",
        "arguments": arguments,
    }
    result = _result("write_file", arguments, payload)
    original = deepcopy(result.batch_receipt)
    messages = _committed_transcript_messages([result])
    assert _audit(messages)["ok"]
    assert "private-" not in json.dumps(messages)
    assert result.batch_receipt == original
