"""Low-context engineering loop: bounded working set + StateFirst projection."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest
from polaris.cells.roles.runtime.internal.engineering_loop import (
    _bounded_request_messages,
    _history_allowances,
)
from polaris.cells.roles.runtime.internal.engineering_state_projection import (
    working_state_facts,
    working_state_message,
)
from polaris.cells.roles.runtime.public.contracts.engineering_run import RoleRunCommand
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_loop import _native, _setup
from polaris.kernelone.llm.engine.token_estimator import TokenEstimator


def test_history_allowances_tight_first_then_window_ceiling() -> None:
    # 100000 * 0.95 - 24576 = 70424; the tight working set is the operating point.
    assert _history_allowances(100_000) == [32_000, 70_424]


def test_history_allowances_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_ENGINEERING_WORKING_SET_TOKENS", "12000")
    assert _history_allowances(100_000) == [12_000, 70_424]


def test_history_allowances_degenerate_small_window() -> None:
    assert _history_allowances(10_000) == [5_000]


def _events() -> list[dict[str, Any]]:
    return [
        {"seq": 1, "kind": "role_started", "role": "director", "payload": {}},
        {
            "seq": 2,
            "kind": "tool_started",
            "role": "director",
            "payload": {"turn_seq": 1, "call_id": "w1", "tool": "write_file", "arguments": {"file": "a.py"}},
        },
        {
            "seq": 3,
            "kind": "tool_observed",
            "role": "director",
            "payload": {"turn_seq": 1, "call_id": "w1", "tool": "write_file", "ok": True, "content": "written"},
        },
        {
            "seq": 4,
            "kind": "message",
            "role": "director",
            "payload": {"message": {"role": "assistant", "content": "Now run the tests."}},
        },
        {
            "seq": 5,
            "kind": "tool_observed",
            "role": "director",
            "payload": {
                "turn_seq": 2,
                "call_id": "r1",
                "tool": "execute_command",
                "ok": False,
                "content": "AssertionError: bad\nmore detail",
            },
        },
        {"seq": 6, "kind": "loop_detected", "role": "director", "payload": {"turn_seq": 2}},
        {
            "seq": 7,
            "kind": "verification_completed",
            "role": "director",
            "payload": {"verification": "passed", "checks": [{"command": "pytest", "ok": True, "exit_code": 0}]},
        },
    ]


def test_working_state_facts_fold_journal() -> None:
    run = {"instruction": "Build the thing.", "workspace_revision": 3, "verification": "passed"}
    facts = working_state_facts(run, _events(), role="director", frame_seq=1, observed_at=1.0, deadline=None)
    assert facts["goal"] == "Build the thing."
    assert facts["candidate"] == {"workspace_revision": 3, "verification": "passed"}
    assert facts["files_changed"] == ["a.py"]
    assert facts["tool_activity"] == {"execute_command": 1, "write_file": 1}
    assert facts["loop_interventions"] == 1
    assert facts["last_intent"] == "Now run the tests."
    assert "AssertionError" in facts["last_error"]
    assert facts["last_verification"][0]["command"] == "pytest"
    assert "recorded facts" in str(facts["authority"]).lower()


def test_working_state_message_is_one_trailing_json_message() -> None:
    run = {"instruction": "Build the thing.", "workspace_revision": 3, "verification": "passed"}
    message = working_state_message(run, _events(), role="director", frame_seq=1, observed_at=1.0, deadline=None)
    assert message["role"] == "user"
    payload = json.loads(message["content"].split("\n", 1)[1])
    assert payload["candidate"]["workspace_revision"] == 3


class _FakeStore:
    def load_events(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return []


def _long_history(turns: int, payload: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "You are Director."},
        {"role": "user", "content": "Build the thing."},
    ]
    for index in range(turns):
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"c{index}",
                        "type": "function",
                        "function": {"name": "execute_command", "arguments": "{}"},
                    }
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": f"c{index}", "content": payload})
    return messages


def test_bounded_request_messages_caps_long_history(tmp_path: Path) -> None:
    payload = "x" * 8000
    messages = _long_history(30, payload)
    command = RoleRunCommand("low-context", "director", "Build the thing.", tmp_path)
    bounded = _bounded_request_messages(
        messages,
        store=cast(Any, _FakeStore()),
        command=command,
        role="director",
        context_window_tokens=100_000,
        visual_binding=None,
        audit={},
    )
    assert bounded is not None
    before = TokenEstimator.estimate(json.dumps(messages, ensure_ascii=False))
    after = TokenEstimator.estimate(json.dumps(bounded, ensure_ascii=False))
    assert after < before * 0.6
    assert "上下文已压缩" in json.dumps(bounded, ensure_ascii=False)


async def test_director_request_carries_working_state(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    (isolated_workspace / "a.py").write_text("x = 1\n", encoding="utf-8")
    loop, store, _tools, command, scope, limits = _setup(isolated_workspace)
    scripted_provider.enqueue_json(_native(("read", "read_file", {"file": "a.py"})))
    scripted_provider.enqueue_json(_native(text="Inspected."))
    result = await loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
    )
    assert result.status == "completed"
    bodies = [json.dumps(request["body"], ensure_ascii=False) for request in scripted_provider.requests]
    assert any("Host working-state projection" in body for body in bodies)
    assert any("recorded facts only" in body for body in bodies)


def test_working_state_excludes_control_plane_fields() -> None:
    run = {"instruction": "Build the thing.", "workspace_revision": 1, "verification": "passed"}
    message = working_state_message(run, _events(), role="director", frame_seq=1, observed_at=1.0, deadline=None)
    payload = json.loads(message["content"].split("\n", 1)[1])
    forbidden = {
        "budget_status",
        "metrics",
        "policy_verdict",
        "system_warnings",
        "telemetry",
        "telemetry_events",
        "thinking",
        "thinking_content",
        "raw_output",
    }
    assert not (set(payload) & forbidden)
    # No provider body, tool arguments or model text beyond the recorded intent.
    assert "provider" not in payload
    assert "tools" not in payload


def test_working_state_keeps_failed_verification_reason() -> None:
    run = {"instruction": "Build the thing.", "workspace_revision": 2, "verification": "failed"}
    events = [
        {"seq": 1, "kind": "role_started", "role": "director", "payload": {}},
        {
            "seq": 2,
            "kind": "verification_completed",
            "role": "director",
            "payload": {
                "verification": "failed",
                "checks": [
                    {
                        "command": "tsc",
                        "ok": False,
                        "exit_code": 1,
                        "content": "stdout:\nmain.ts(23,8): error TS2307: Cannot find module './garden'\n",
                    }
                ],
            },
        },
    ]
    facts = working_state_facts(run, events, role="director", frame_seq=1, observed_at=1.0, deadline=None)
    assert facts["candidate"]["verification"] == "failed"
    assert "TS2307" in facts["last_verification_error"]
