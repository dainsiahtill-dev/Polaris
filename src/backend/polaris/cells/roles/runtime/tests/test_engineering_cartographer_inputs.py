from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.runtime.internal.engineering_cartographer import (
    CartographerRuntime,
    NarrativeRejectedError,
    current_facts,
    facts_for_run_card,
)


def test_draw_contains_only_bounded_observed_subjects_and_their_own_evidence() -> None:
    events: list[dict[str, Any]] = []

    def add(kind: str, **payload: Any) -> None:
        events.append({"seq": len(events) + 1, "role": "director", "kind": kind, "payload": payload})

    add(
        "tool_started",
        tool="write_file",
        turn_seq=1,
        call_id="same",
        arguments={"file": "first.ts", "content": "private file body"},
    )
    add("tool_started", tool="write_file", turn_seq=2, call_id="same", arguments={"file": "second.ts"})
    add("tool_observed", tool="write_file", turn_seq=2, call_id="same", ok=True, content="private file body")
    add(
        "tool_observed",
        tool="write_file",
        turn_seq=1,
        call_id="same",
        ok=False,
        content="ValueError: missing file body",
        exit_code=None,
    )
    add(
        "tool_started",
        tool="execute_command",
        turn_seq=5,
        call_id="check",
        arguments={"command": "cd /work && npm test"},
    )
    add(
        "tool_observed",
        tool="execute_command",
        turn_seq=5,
        call_id="check",
        ok=True,
        exit_code=0,
        content="# fail 0\nprivate process output",
    )
    add(
        "tool_started",
        tool="execute_command",
        turn_seq=7,
        call_id="script",
        arguments={"command": "python -c 'private inline script'"},
    )
    add("tool_observed", tool="execute_command", turn_seq=7, call_id="script", ok=True, exit_code=0)
    add("llm_request_observed", call_id="aaa-not-action-evidence")
    run = {
        "verification": "failed",
        "workspace_revision": 7,
        "status": "running",
        "instruction": "--- Frozen original requirements (verbatim) ---\n# Goal\nMake the counter persist.",
        "role": "director",
    }
    draw, key = facts_for_run_card(run, events)
    rows = draw["recent_activity"]
    assert "Make the counter persist." in draw["goal_excerpt"]
    assert [(r["subject"], r["turn_seq"], r["outcome"]) for r in rows[:2]] == [
        ("second.ts", 2, "succeeded"),
        ("first.ts", 1, "failed"),
    ]
    assert "missing file body" in rows[1]["failure"]
    assert rows[2]["subject"] == "npm test" and rows[2]["exit_code"] == 0 and "failure" not in rows[2]
    assert "event:3" in rows[0]["evidence_refs"] and "event:4" in rows[1]["evidence_refs"]
    assert set(draw["evidence_refs"]) == {ref for r in rows for ref in r["evidence_refs"]}
    assert "same" not in draw["evidence_refs"] and "aaa-not-action-evidence" not in draw["evidence_refs"]
    assert "private" not in json.dumps(draw)
    for _ in range(50):
        add("message", content="unrelated chatter")
    assert facts_for_run_card(run, events) == (draw, key)
    add("tool_observed", tool="read_file", turn_seq=59, call_id="new", ok=True)
    assert facts_for_run_card(run, events)[1] != key


@pytest.mark.parametrize("citation", ["event:2", "first"])
async def test_cited_failed_read_expires_only_when_its_own_operation_is_superseded(citation: str) -> None:
    events: list[dict[str, Any]] = []

    def read(file: str, call_id: str, ok: bool, **slice_args: int) -> None:
        turn = len(events) + 1
        identity = {"tool": "read_file", "turn_seq": turn, "call_id": call_id}
        events.append(
            {"seq": turn, "kind": "tool_started", "payload": {**identity, "arguments": {"file": file, **slice_args}}}
        )
        events.append({"seq": turn + 1, "kind": "tool_observed", "payload": {**identity, "ok": ok}})

    run = {"workspace_revision": 3}
    read("schema.json", "first", False)
    read("README.md", "context", True)
    request, fingerprint = facts_for_run_card(run, events)
    runtime = CartographerRuntime(AsyncMock())
    runtime.schedule(request, fingerprint=fingerprint)
    advice = {
        "phase": "DIAGNOSE",
        "completed": [],
        "next_actions": [{"action": "Recheck the schema", "why": "The read failed", "expected": "Schema is readable"}],
        "evidence_refs": [citation],
    }
    read("other.json", "unrelated", True)
    read("schema.json", "different-slice", True, start_line=2, end_line=4)
    read("README.md", "uncited-repeat", True)
    assert runtime.validate_current(advice, current_facts(run, events))
    read("schema.json", "recovered", True)
    with pytest.raises(NarrativeRejectedError) as rejected:
        runtime.validate_current(advice, current_facts(run, events))
    assert rejected.value.code == "superseded_evidence"


async def test_current_host_build_to_verify_expires_same_candidate_draw_and_renders_the_actual_phase() -> None:
    from polaris.cells.roles.runtime.internal.engineering_cartographer import deterministic_run_card
    from polaris.cells.roles.runtime.internal.engineering_memory_cards import run_card_section
    from polaris.cells.roles.runtime.internal.engineering_phase_policy import phase_for

    run = {"workspace_revision": 3, "role": "director", "verification": "not_run"}
    previous = [{"kind": "request_prepared", "role": "qa", "payload": {"request": {"context": {"phase": "report"}}}}]
    stages = [
        phase_for(
            "director",
            verification="not_run",
            first_verification_due=due,
            preparation_pending=False,
            qa_report_required=False,
        ).value
        for due in (False, True)
    ]
    build, verify = ({**run, "execution_phase": stage} for stage in stages)
    request, key = facts_for_run_card(build, previous)
    assert request["working_state"]["execution_phase"] == "build"
    runtime = CartographerRuntime(AsyncMock())
    runtime.schedule(request, fingerprint=key)
    advice = {"phase": "EDIT", "completed": [], "next_actions": [], "evidence_refs": []}
    assert runtime.validate_current(advice, current_facts(build, previous))
    with pytest.raises(NarrativeRejectedError) as rejected:
        runtime.validate_current(advice, current_facts(verify, previous))
    assert rejected.value.code == "stale_working_state"
    card = deterministic_run_card(verify, previous, phase="")
    section = run_card_section(card)
    assert "当前阶段：verify" in section and "DIAGNOSE" not in section


async def test_cartographer_draws_on_new_diagnostic_evidence_and_stalled_reads_only() -> None:
    pool = AsyncMock()
    pool.draw_many.return_value = ["{}"]
    runtime = CartographerRuntime(pool)
    events: list[dict[str, Any]] = []
    run = {"role": "director", "execution_phase": "build", "workspace_revision": 2, "verification": "not_run"}

    def record(tool: str, path: str, ok: bool = True, **extra: Any) -> None:
        turn = len(events) + 1
        common = {"turn_seq": turn, "tool": tool, "call_id": f"call-{turn}"}
        events.append({"seq": turn, "kind": "tool_started", "payload": {**common, "arguments": {"file": path}}})
        events.append({"seq": turn + 1, "kind": "tool_observed", "payload": {**common, "ok": ok, **extra}})

    def offer(enabled: frozenset[str] = frozenset({"read_file", "repo_rg"})) -> bool:
        req, key = facts_for_run_card(run, events)
        return runtime.schedule_for_decision(req, fingerprint=key, enabled_tools=enabled)

    for path in ("a.py", "b.py", "c.py"):
        record("write_file", path)
        assert not offer()
    for path in ("a.py", "b.py", "c.py"):
        record("read_file", path)
        assert not offer()
    record("read_file", "a.py")
    assert not offer(frozenset()) and offer()
    await asyncio.sleep(0)
    rendered = pool.draw_many.call_args.args[0][0]["decision"]
    assert rendered["reason"] == "repeated_read_without_progress" and rendered["evidence_refs"]
    assert "repo_rg" in rendered["available_checks"]
    receipt = runtime.decision_receipt
    restored = CartographerRuntime(AsyncMock())
    req, key = facts_for_run_card(run, events)
    assert not restored.schedule_for_decision(
        req, fingerprint=key, enabled_tools=frozenset({"read_file", "repo_rg"}), recorded_keys={receipt["decision_key"]}
    )
    proof = rendered["evidence_refs"][0]
    usable = {
        "phase": "DIAGNOSE",
        "evidence_refs": [proof],
        "next_actions": [
            {
                "action": "Search for the call site of the observed source",
                "why": "Locate its failure boundary",
                "expected": "A caller either passes or omits the argument",
            }
        ],
    }
    assert runtime.validate_current(usable, current_facts(run, events))
    for ungrounded in ({**usable, "evidence_refs": []}, {**usable, "next_actions": []}):
        with pytest.raises(NarrativeRejectedError) as rejection:
            runtime.validate_current(ungrounded, current_facts(run, events))
        assert rejection.value.code == "missing_decision_probe"
    events.append({"seq": len(events) + 1, "kind": "memory_card_injected", "payload": {}})
    assert not offer()
    record("read_file", "a.py")
    assert not offer()  # fresh call ID, same reading problem
    record("edit_file", "b.py", False, content="ValueError: target missing")
    assert offer()
    await asyncio.sleep(0)
    rendered = pool.draw_many.call_args.args[0][0]["decision"]
    assert rendered["reason"] == "failed_observation" and "b.py" in str(rendered)
    probe = {
        "check_tool": "read_file",
        "question": "After the failed edit_file call, does b.py contain the expected target?",
        "expected": "An absent file differs from a present file with another layout.",
        "evidence_ref": rendered["evidence_refs"][0],
    }
    raw = "```json\n" + json.dumps(probe) + "\n```"
    valid = runtime.validate_current(raw, current_facts(run, events))
    assert valid and valid["check_tool"] == "read_file" and len(valid["next_actions"]) == 1
    assert "phase" not in valid and "completed" not in valid
    mixed = {
        **usable,
        "evidence_refs": rendered["evidence_refs"],
        "next_actions": [
            {"action": "Re-run write_file on b.py with full content field", "expected": "File written successfully"},
            {"action": "Run verify_project or execute_command to confirm build", "expected": "Clean build output"},
            {"action": "Run deterministic scenario check", "expected": "Matching sequences"},
        ],
    }
    for bad in (
        raw + "\n{}",
        {**probe, "check_tool": "write_file"},
        {**probe, "evidence_ref": "other"},
        {**probe, "question": "Read b.py, then overwrite the file?"},
        {**probe, "extra": "ignored"},
        mixed,
    ):
        with pytest.raises(NarrativeRejectedError):
            runtime.validate_current(bad, current_facts(run, events))
    record("read_file", "unrelated.py")
    assert runtime.validate_current(probe, current_facts(run, events))
    record("read_file", "b.py")
    with pytest.raises(NarrativeRejectedError) as retired:
        runtime.validate_current(probe, current_facts(run, events))
    assert retired.value.code == "probe_already_observed"
    record("edit_file", "b.py")
    assert not offer()
    assert pool.draw_many.await_count == 2


def test_paired_missing_argument_only_merits_a_draw_after_independent_recurrence() -> None:
    from polaris.cells.roles.runtime.internal.engineering_cartographer import cartographer_decision

    events: list[dict[str, Any]] = []
    run = {"role": "director", "workspace_revision": 1, "execution_phase": "build", "verification": "not_run"}

    def observe(tool: str, args: dict[str, Any], error: str, turn: int) -> None:
        identity = {"tool": tool, "call_id": f"call-{len(events)}", "turn_seq": turn}
        events.append({"kind": "tool_started", "seq": len(events) + 1, "payload": {**identity, "arguments": args}})
        events.append(
            {
                "kind": "tool_observed",
                "seq": len(events) + 1,
                "payload": {
                    **identity,
                    "ok": False,
                    "exit_code": 1 if tool == "execute_command" else None,
                    "content": error,
                },
            }
        )

    def demand() -> Any:
        return cartographer_decision(
            facts_for_run_card(run, events)[0], frozenset({"read_file", "repo_rg", "execute_command"})
        )

    error = (
        "ValueError: write_file requires a content field with the complete file body for x.py; do not send only file."
    )
    observe("write_file", {"file": "x.py"}, error, 1)
    assert demand() is None
    observe("write_file", {"file": "x.py"}, error, 1)
    assert demand() is None
    observe("write_file", {"file": "x.py"}, error, 5)
    first = demand()
    assert first and first[0]["reason"] == "repeated_invalid_arguments"
    observe("write_file", {"file": "x.py"}, error, 7)
    assert demand()[1] == first[1]
    events.clear()
    observe("write_file", {"file": "x.py", "content": "actual"}, error, 1)
    assert demand()[0]["reason"] == "failed_observation"  # receipt conflicts with dispatched arguments
    events.clear()
    observe("execute_command", {"command": "npm test"}, "TypeError: invalid argument", 1)
    assert demand()[0]["reason"] == "failed_observation"


async def test_probe_is_consumed_only_after_exact_final_request_inclusion(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from polaris.cells.context.engine.public import snapshot_paths

    runtime = CartographerRuntime(AsyncMock())
    runtime.schedule({"decision": {"reason": "failed_observation"}}, fingerprint="draw")
    await asyncio.sleep(0)
    sent = {
        "kind": "run_card_injected",
        "role": "director",
        "payload": {
            "turn_seq": 11,
            "cartographer_fingerprint": "draw",
            "advisory_next_actions": [
                {"action": "repo_rg: Which caller supplies the field?", "expected": "A caller is located."}
            ],
        },
    }
    terminal: dict[str, Any] = {
        "kind": "llm_request_observed",
        "role": "director",
        "payload": {
            "turn_seq": 11,
            "phase": "terminal",
            "status": "completed",
            "call_id": "s:director:11",
            "context_snapshot_ref": "1" * 24,
        },
    }
    body: dict[str, Any] = {"messages": [{"role": "user", "content": "other guidance"}]}
    monkeypatch.setattr(
        snapshot_paths,
        "read_context_snapshot",
        lambda *_: SimpleNamespace(
            payload={"call_id": "s:director:11", "provider_request": {"final_physical_request": {"body": body}}}
        ),
    )
    assert not runtime.retire_delivered_probe([terminal], workspace="/w", session_id="s")
    assert not runtime.retire_delivered_probe([sent], workspace="/w", session_id="s")
    assert not runtime.retire_delivered_probe([sent, terminal], workspace="/w", session_id="s")
    terminal = {**terminal, "payload": {**terminal["payload"], "context_snapshot_ref": "2" * 24}}
    body["messages"][-1]["content"] = [
        {
            "type": "text",
            "text": "制图师建议（待检验）：repo_rg: Which caller supplies the field?；预期观察：A caller is located.",
        }
    ]
    assert runtime.retire_delivered_probe([sent, terminal], workspace="/w", session_id="s")
    assert runtime.poll() is None


def test_diagnostic_source_must_be_visible_bound_exact_and_latest() -> None:
    contents = "\n".join(
        ["unrelated"] * 77
        + [
            "",
            "  sim.setFlowerEmotion(BASE_FLOWERS[0].id, 'excited');",
            "  const before = sim.flashLog.length;",
            "outside",
        ]
    )
    path = "src/app/experiments.ts"
    args = {"file": path, "content": contents}
    result = {
        "call_id": "source",
        "tool": "write_file",
        "ok": True,
        "content": f"Wrote {len(contents.encode('utf-8'))} UTF-8 bytes to {path}.",
        "interrupted": False,
        "timed_out": False,
        "exit_code": None,
    }
    diag = "src/app/experiments.ts(79,40): error TS2339: Property 'id' does not exist on type '{ x: number; y: number; emotion: Emotion; }'."
    events: list[dict[str, Any]] = [
        {"seq": 1, "kind": "role_started", "role": "director", "payload": {}},
        {
            "seq": 2,
            "kind": "tool_started",
            "role": "director",
            "payload": {"turn_seq": 2, "call_id": "source", "tool": "write_file", "arguments": args},
        },
        {"seq": 3, "kind": "tool_observed", "role": "director", "payload": {**result, "turn_seq": 2}},
        {
            "seq": 4,
            "kind": "tool_started",
            "role": "director",
            "payload": {
                "turn_seq": 4,
                "call_id": "tsc",
                "tool": "execute_command",
                "arguments": {"command": "npm run build"},
            },
        },
        {
            "seq": 5,
            "kind": "tool_observed",
            "role": "director",
            "payload": {
                "turn_seq": 4,
                "call_id": "tsc",
                "tool": "execute_command",
                "ok": False,
                "exit_code": 1,
                "content": diag,
            },
        },
    ]
    messages: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "source", "type": "function", "function": {"name": "write_file", "arguments": json.dumps(args)}}
            ],
        },
        {"role": "tool", "tool_call_id": "source", "content": json.dumps(result)},
    ]
    run = {"session_id": "s", "role": "director", "execution_phase": "build", "workspace_revision": 2}

    def source(ms: list[dict[str, Any]], ledger: list[dict[str, Any]] = events, role: str = "director") -> Any:
        return facts_for_run_card({**run, "role": role}, ledger, retained_messages=ms, turn_seq=6, frame_seq=1)[0].get(
            "already_visible_source"
        )

    card = source(messages)
    assert card and card["source_kind"] == "retained_historical_write" and card["line"] == 79 and card["path"] == path
    assert (
        "BASE_FLOWERS[0].id" in card["excerpt"]
        and len(card["excerpt"].splitlines()) <= 3
        and "outside" not in card["excerpt"]
    )
    assert card["source_call_id"] == "source" and card["observed_seq"] == 3 and card["retained_for_turn_seq"] == 6
    assert source(messages[:1]) is None and source([], events) is None and source(messages, events, role="qa") is None
    assert source(messages, [events[0], {**events[1], "role": "qa"}, *events[2:]]) is None
    duplicate = {
        **events[1],
        "seq": 4,
        "role": "qa",
        "payload": {
            **events[1]["payload"],
            "turn_seq": 3,
            "arguments": {"file": "other/experiments.ts", "content": contents},
        },
    }
    assert source(messages, [*events, duplicate]) is None
    assert source(messages, [*events, {"kind": "role_started", "seq": 5, "role": "director", "payload": {}}]) is None
    import hashlib

    checksum = hashlib.sha256(contents.encode("utf-8")).hexdigest()

    def read_source(revision: int, historical: bool = False) -> Any:
        file_card = {
            "path": path,
            "stale": False,
            "sha256": checksum,
            "content_ref": f"filecard:{checksum}",
            "revision": revision,
            "content_preview": contents,
        }
        read_args = {"file": path, **({"content_ref": f"filecard:{checksum}"} if historical else {})}
        obs = {**result, "tool": "read_file", "content": json.dumps({"file_card": file_card})}
        ledger = [
            events[0],
            {**events[1], "payload": {**events[1]["payload"], "tool": "read_file", "arguments": read_args}},
            {**events[2], "payload": {**obs, "turn_seq": 2}},
            *events[3:],
        ]
        ms: list[dict[str, Any]] = [
            {
                "role": "assistant",
                "tool_calls": [{"id": "source", "function": {"name": "read_file", "arguments": json.dumps(read_args)}}],
            },
            {"role": "tool", "tool_call_id": "source", "content": json.dumps(obs)},
        ]
        return source(ms, ledger)

    assert read_source(2)["source_kind"] == "retained_current_read"
    assert read_source(1) is None and read_source(2, historical=True) is None
    assert (
        source(
            messages,
            [
                *events[:-1],
                {
                    **events[-1],
                    "payload": {**events[-1]["payload"], "content": diag.replace(path, "other/experiments.ts")},
                },
            ],
        )
        is None
    )
    assert (
        source(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "source",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps({"file": path, "fold_state": "folded"}),
                            },
                        }
                    ],
                },
                messages[1],
            ]
        )
        is None
    )
    assert (
        source(
            messages,
            [
                *events,
                {
                    "seq": 6,
                    "kind": "tool_started",
                    "role": "director",
                    "payload": {"turn_seq": 5, "call_id": "edit", "tool": "edit_file", "arguments": {"file": path}},
                },
            ],
        )
        is None
    )


async def test_decision_abstention_is_valid_without_a_probe_or_retry() -> None:
    from polaris.cells.roles.runtime.internal.engineering_cartographer import CartographerPool

    raw = json.dumps(
        {"abstain": True, "reason": "The supplied source resolves the question.", "evidence_ref": "event:2"}
    )
    pool = CartographerPool(base_url="unused", api_key="unused", model="fixture")
    calls = []

    def reply(messages: list[dict[str, str]]) -> str:
        calls.append(1)
        return raw

    pool._post = reply  # type: ignore[method-assign]
    runtime = CartographerRuntime(pool)
    state = {"verification": "not_run", "workspace_revision": 1, "phase": "build"}
    runtime.schedule(
        {
            "working_state": state,
            "decision": {"evidence_refs": ["event:2"], "available_checks": ["read_file"]},
            "evidence_refs": ["event:2"],
        },
        fingerprint="offer",
    )
    assert runtime._job is not None
    await runtime._job
    answer = runtime.validate_current(runtime.poll(), {**state, "evidence_refs": {"event:2"}})
    assert len(calls) == 1 and answer and answer["outcome"] == "abstained" and not answer.get("next_actions")
    assert not pool.prior_raw_responses


async def test_late_solicited_advice_expires_when_the_supplied_source_is_rewritten() -> None:
    runtime = CartographerRuntime(AsyncMock())
    state = {"verification": "not_run", "workspace_revision": 3, "phase": "build"}
    runtime.schedule(
        {
            "working_state": state,
            "evidence_refs": ["event:8"],
            "decision": {"evidence_refs": ["event:8"], "available_checks": ["repo_rg"]},
            "already_visible_source": {"path": "src/f.ts", "observed_seq": 4},
        },
        fingerprint="offer",
    )
    raw = {
        "check_tool": "repo_rg",
        "question": "Which declaration specifies the type?",
        "expected": "The declaration either defines or omits this member.",
        "evidence_ref": "event:8",
    }
    facts = {**state, "evidence_refs": {"event:8"}, "latest_source_change": {"src/f.ts": 1, "unrelated.ts": 9}}
    assert runtime.validate_current(raw, facts)
    with pytest.raises(NarrativeRejectedError) as rejected:
        runtime.validate_current(raw, {**facts, "latest_source_change": {"src/f.ts": 9}})
    assert rejected.value.code == "superseded_visible_source"
