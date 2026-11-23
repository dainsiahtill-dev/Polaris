"""Low-context engineering loop: bounded working set + StateFirst projection."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, cast

import pytest
from polaris.cells.roles.runtime.internal.engineering_loop import (
    _WORKING_SET_TOKENS_DEFAULT,
    _bounded_request_messages,
    _history_allowances,
    _retention_fold_window,
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
    # The window ceiling is the window minus the output reserve; the tight
    # working set is the configured cap clamped to that ceiling.
    ceiling = 100_000 * 95 // 100 - (16_384 + 8_192)
    assert ceiling == 70_424
    assert _history_allowances(100_000) == sorted({min(_WORKING_SET_TOKENS_DEFAULT, ceiling), ceiling})
    # With a window large enough, the configured cap is the operating point.
    assert _history_allowances(1_000_000) == sorted({_WORKING_SET_TOKENS_DEFAULT, 1_000_000 * 95 // 100 - 24_576})


def test_retention_fold_window_narrows_only_when_history_exceeds_the_working_set() -> None:
    # Healthy history keeps the configured fold window.
    assert _retention_fold_window(24, history_tokens=1_000, context_window_tokens=1_000_000) == 24
    # History that cannot fit the retained working set is folded harder first,
    # so distillation compresses it instead of wholesale turn dropping. The
    # narrowing is bounded: two halvings, never below four activities.
    assert _retention_fold_window(24, history_tokens=900_000, context_window_tokens=1_000_000) == 6
    assert _retention_fold_window(8, history_tokens=900_000, context_window_tokens=1_000_000) == 4
    # A disabled fold window stays disabled.
    assert _retention_fold_window(None, history_tokens=900_000, context_window_tokens=1_000_000) is None
    assert _retention_fold_window(0, history_tokens=900_000, context_window_tokens=1_000_000) == 0


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
            "payload": {
                "workspace_revision": 3,
                "verification": "passed",
                "checks": [{"command": "pytest", "ok": True, "exit_code": 0}],
            },
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
    def __init__(self) -> None:
        self.projections: list[dict[str, Any]] = []

    def append_event(self, *_args: Any, **kwargs: Any) -> None:
        self.projections.append(kwargs.get("payload") or {})

    def load_events(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    def load_run(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "session_id": "low-context",
            "workspace": ".",
            "status": "running",
            "verification": "not_run",
            "workspace_revision": 0,
        }


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


def _write_history(turns: int, size: int) -> list[dict[str, Any]]:
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
                        "id": f"w{index}",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({"file": f"src/f{index}.ts", "content": "x" * size}),
                        },
                    }
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": f"w{index}", "content": f"Wrote {size} bytes."})
    return messages


def test_bounded_request_messages_folds_old_write_calls(tmp_path: Path) -> None:
    """The model's own document bodies must not accumulate without bound."""

    messages = _write_history(60, 6000)
    command = RoleRunCommand("low-context", "director", "Build the thing.", tmp_path)
    bounded = _bounded_request_messages(
        messages,
        store=cast(Any, _FakeStore()),
        command=command,
        role="director",
        context_window_tokens=1_000_000,
        visual_binding=None,
        audit={},
    )
    projected = [
        json.loads(message["tool_calls"][0]["function"]["arguments"])
        for message in bounded
        if message.get("tool_calls")
    ]
    folded_count = sum(1 for item in projected if item.get("fold_state") == "FOLDED")
    # Only age decides now: everything outside the 24-activity focus window and
    # above the call-plane floor folds, the current edit stays intact.
    assert folded_count > 10
    assert projected[-1].get("fold_state") != "FOLDED"
    assert all(
        item.get("fold_reason") == "outside_current_focus" for item in projected if item.get("fold_state") == "FOLDED"
    )


def test_bounded_request_messages_caps_long_history(tmp_path: Path) -> None:
    payload = "x" * 8000
    # Enough turns to exceed the window ceiling for a 100k window, so the
    # compressor must elide older turns instead of shipping the full history.
    messages = _long_history(60, payload)
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
    note = next(message["content"] for message in bounded if "上下文已压缩" in str(message.get("content")))
    # The model must be told the real constraint, not a false window excuse.
    assert "有界上下文预算" in note
    assert "模型窗口" not in note


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
    prepared = store.load_events(command.session_id, kinds=("request_prepared",))
    shadow = prepared[-1]["payload"]["shadow_decision_pack"]
    assert shadow["session_id"] == command.session_id
    assert shadow["status"] == "assembled"
    assert all("shadow_decision_pack" not in body for body in bodies)


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
                "workspace_revision": 2,
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


@pytest.mark.parametrize("change", ["revision", "restart", "unbound", "invalidated"])
def test_projection_never_reuses_stale_verification(change: str) -> None:
    events = _events()
    verdict = "not_run" if change == "invalidated" else "passed"
    run = {"workspace_revision": 4 if change == "revision" else 3, "verification": verdict}
    if change == "restart":
        events.append({"seq": 8, "kind": "verification_started", "role": "director", "payload": {}})
    if change == "unbound":
        events[-1]["payload"].pop("workspace_revision")
    facts = working_state_facts(run, events, role="director", frame_seq=1, observed_at=1, deadline=None)
    assert facts["candidate"]["verification"] == ("not_run" if change == "invalidated" else "unknown")
    assert facts["last_verification"] == []


def test_projection_binds_latest_checks_and_successful_edits() -> None:
    events = _events()
    events.append(
        {
            **events[-1],
            "seq": 8,
            "payload": {
                "workspace_revision": 3,
                "verification": "failed",
                "checks": [{"command": "new", "ok": False, "content": "Error: current", "exit_code": 1}],
            },
        }
    )
    events.append(
        {
            **events[1],
            "seq": 9,
            "payload": {
                "turn_seq": 9,
                "call_id": "pending",
                "tool": "edit_file",
                "arguments": {"file": "pending.txt"},
            },
        }
    )
    facts = working_state_facts(
        {"workspace_revision": 3, "verification": "failed"},
        events,
        role="director",
        frame_seq=1,
        observed_at=1,
        deadline=None,
    )
    assert facts["last_verification"] == [{"command": "new", "ok": False, "exit_code": 1}]
    assert facts["last_verification_error"] == "Error: current"
    assert facts["files_changed"] == ["a.py"]


def test_shadow_pack_replay_and_overflow_preserve_evidence_location() -> None:
    from polaris.cells.roles.runtime.internal.engineering_state_projection import shadow_decision_pack

    run = {
        "session_id": "r",
        "instruction": "首要约束" * 1000,
        "required_checks": ["test"],
        "workspace_revision": 4,
        "verification": "not_run",
    }
    events = _events()
    events.append(
        {
            **events[1],
            "seq": 8,
            "payload": {
                "turn_seq": 8,
                "call_id": "unknown",
                "tool": "execute_command",
            },
        }
    )
    full = shadow_decision_pack(run, events, max_tokens=32000)
    assert full == shadow_decision_pack(run, events, max_tokens=32000)
    assert full["status"] == "assembled"
    assert full["pack"]["contract"]["instruction"] == run["instruction"]
    assert full["pack"]["current_verification_seq"] is None
    assert full["pack"]["last_verification_seq"] == 7
    assert full["pack"]["pending_tool_started_seqs"] == [8]
    assert full["pack"]["intervention_seqs"] == [6]
    overflow = shadow_decision_pack(run, events, max_tokens=20)
    assert overflow["status"] == "overflow"
    assert "pack" not in overflow
    assert overflow["as_of_selected_event_seq"] == 8
    assert overflow["estimated_pack_tokens"] > overflow["max_tokens"]


def test_shadow_projection_inputs_share_one_sqlite_snapshot(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, store, _, command, _, _ = _setup(isolated_workspace)
    # Permit the competing test connection to commit while the reader holds its
    # snapshot. Production coherence must hold under either journal mode.
    with closing(sqlite3.connect(store.database_path)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    existing = store.load_events(command.session_id, kinds=("tool_started",))
    acceptance = store._acceptance

    def interleave(*args: Any, **kwargs: Any) -> Any:
        result = acceptance(*args, **kwargs)
        store.append_event(
            command.session_id,
            kind="tool_started",
            role="director",
            payload={"call_id": "late", "turn_seq": 23, "tool": "test"},
        )
        return result

    monkeypatch.setattr(store, "_acceptance", interleave)
    _, selected = store.load_projection_inputs(
        command.session_id, workspace=isolated_workspace, kinds=("tool_started",)
    )
    assert selected == existing
    assert len(store.load_events(command.session_id, kinds=("tool_started",))) == len(existing) + 1


def test_shadow_separates_reconciled_effect_and_pending_observation() -> None:
    from polaris.cells.roles.runtime.internal.engineering_state_projection import shadow_decision_pack

    events = _events()
    events.append({**events[1], "seq": 8, "payload": {"turn_seq": 8, "call_id": "edit", "tool": "write_file"}})
    events.append(
        {
            "seq": 9,
            "role": "director",
            "kind": "tool_reconciled",
            "payload": {
                "turn_seq": 8,
                "call_id": "edit",
                "original_effect": "unknown",
                "continuation_basis": "current_file_matches_requested_write",
            },
        }
    )
    pack = shadow_decision_pack({}, events, max_tokens=5000)["pack"]
    assert pack["pending_tool_started_seqs"] == []
    assert pack["reconciliation_seqs"] == [9]


def test_embedded_image_bytes_are_priced_as_binary_not_text() -> None:
    """A browser capture must not make a healthy history look oversized.

    The retention allowance compares the history against 256k; counting base64
    as text pushed any run with screenshots past it and collapsed the fold window
    from 24 activities to 12, which defeated the focus window.
    """

    import json as _json

    from polaris.cells.roles.runtime.internal.engineering_tool_payload_projection import (
        message_text_tokens,
    )

    plain = message_text_tokens({"role": "tool", "content": "x" * 300_000})
    image = message_text_tokens(
        {"role": "tool", "content": _json.dumps({"images": [{"data_base64": "A" * 300_000}]})}
    )
    # 截图字节被整块排除，而不是按文本单价折算。
    assert image < 2_000
    assert plain > 70_000
