"""Model-selected verification: real commands, one SQLite owner, no replay."""

from __future__ import annotations

import asyncio
import json
import shlex
import sqlite3
from dataclasses import asdict
from pathlib import Path

import pytest
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    PermissionScope,
    RoleRunCommand,
    RunLimits,
    ToolObservation,
)

RTK = Path("/home/dains/.headroom/bin/rtk")


def command(source: str) -> str:
    return f"{RTK} proxy python3 -B -c {shlex.quote(source)}"


def setup(workspace: Path, required: tuple[str, ...] = (), *, require_qa=False):
    scope = PermissionScope((workspace, RTK), (workspace,), frozenset({"execute_command", "verify_project"}), False)
    store = EngineeringSessionStore(workspace)
    store.create_run(
        RoleRunCommand("selection", "director", "Verify actual addition.", workspace, required_checks=required),
        scope=scope,
        limits=RunLimits(None, 100_000, 20_000),
        engine_version="engineering_v2",
        request_key=None,
        require_qa=require_qa,
    )
    verifier = EngineeringVerification(
        workspace, store=store, scope=scope, limits=RunLimits(None, 100_000, 20_000), cancel=asyncio.Event()
    )
    return store, verifier


def dispatch(store, commands, identity="verify"):
    turn = store.append_event("selection", kind="turn_started", role="director", payload={})
    store.record_tool_dispatch(
        "selection",
        turn_seq=turn,
        call_id=identity,
        tool="verify_project",
        arguments={"commands": commands},
        role="director",
    )
    return turn, identity


async def request(store, verifier, workspace, commands, *, close=True, identity="verify"):
    parent = dispatch(store, commands, identity)
    with store.acquire_workspace(workspace):
        results = await verifier.run_verification(
            tuple(commands), session_id="selection", workspace_revision=0, requested_by=parent
        )
        if close:
            store.reconcile_verification_calls("selection", max_output_bytes=20_000)
    return parent, results


async def test_real_selected_checks_merge_reselect_and_survive_windowed_cold_snapshot(tmp_path):
    first = command('assert 2 + 3 == 5; print("addition")')
    second = command('assert 2 * 3 == 6; print("multiply")')
    third = command('assert 5 - 3 == 2; print("subtract")')
    store, verifier = setup(tmp_path, (first, first))
    assert store.load_run("selection", workspace=tmp_path).get("verification_commands") == [first, first]
    _parent, checks = await request(store, verifier, tmp_path, [first, second, second])
    assert [c.exit_code for c in checks] == [0, 0, 0]
    row = store.load_run("selection", workspace=tmp_path)
    assert row["required_checks"] == [first, first]
    assert row["verification_commands"] == [first, first, second]
    assert verifier.current_completed_result("selection")["verification"] == "passed"
    await request(store, verifier, tmp_path, [third], identity="reselect")
    for _ in range(210):
        store.append_event("selection", kind="note", role="director", payload={})
    cold = EngineeringSessionStore(tmp_path).load_snapshot("selection", workspace=tmp_path, event_limit=1)
    assert cold["run"]["verification_commands"] == [first, first, third]
    assert cold["run"]["required_checks"] == [first, first]
    assert len(cold["events"]) == 1
    with sqlite3.connect(store.database_path) as connection:
        assert {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {
            "runs",
            "events",
            "tool_calls",
        }


@pytest.mark.parametrize("invalid", [[], [""], ["\x00"], [7]])
async def test_invalid_selection_cannot_replace_actual_plan(tmp_path, invalid):
    store, verifier = setup(tmp_path)
    assert store.load_run("selection", workspace=tmp_path).get("verification_commands") == []
    valid = command("assert 1 + 1 == 2")
    await request(store, verifier, tmp_path, [valid])
    before = store.load_run("selection", workspace=tmp_path)
    starts = [e for e in store.load_events("selection") if e["kind"] == "verification_started"]
    parent = dispatch(store, invalid, "invalid")
    with pytest.raises(ValueError):
        await verifier.run_verification(
            tuple(invalid), session_id="selection", workspace_revision=0, requested_by=parent
        )
    after = store.load_run("selection", workspace=tmp_path)
    assert after["verification_commands"] == before["verification_commands"] == [valid]
    assert after["checks"] == before["checks"]
    assert [e for e in store.load_events("selection") if e["kind"] == "verification_started"] == starts


async def test_actual_failed_completion_is_current_until_source_changes(tmp_path):
    store, verifier = setup(tmp_path)
    assert store.load_run("selection", workspace=tmp_path).get("verification_commands") == []
    await request(store, verifier, tmp_path, [command("raise SystemExit(7)")])
    completion = verifier.current_completed_result("selection")
    assert completion["verification"] == "failed" and completion["checks"][0]["exit_code"] == 7
    (tmp_path / "changed.py").write_text("VALUE=2\n", encoding="utf-8")
    assert verifier.current_completed_result("selection") is None


async def test_complete_parent_recovery_is_exact_atomic_idempotent_and_does_not_execute(tmp_path):
    from polaris.cells.roles.runtime.internal import engineering_verification as module

    store, verifier = setup(tmp_path)
    assert store.load_run("selection", workspace=tmp_path).get("verification_commands") == []
    parent, checks = await request(store, verifier, tmp_path, [command('print("once")')], close=False)
    payload = verifier.current_completed_result("selection")
    assert payload["input_binding"]["requested_by"] == list(parent)
    assert payload["verification_commands"] == store.load_run("selection", workspace=tmp_path)["verification_commands"]
    observation = module.verification_observation(parent[1], payload, 20_000)
    assert observation.ok and not observation.interrupted and observation.tool == "verify_project"
    store.update_run("selection", status="completed")
    assert store.load_run("selection", workspace=tmp_path)["project_outcome"] != "accepted"
    before_children = [
        e
        for e in store.load_events("selection")
        if e["kind"] == "tool_started" and e["payload"]["tool"] == "execute_command"
    ]
    cold = EngineeringSessionStore(tmp_path)
    with cold.acquire_workspace(tmp_path):
        cold.reconcile_verification_calls("selection", max_output_bytes=20_000)
        cold.reconcile_verification_calls("selection", max_output_bytes=20_000)
    assert cold.pending_tool_calls("selection") == []
    after_children = [
        e
        for e in cold.load_events("selection")
        if e["kind"] == "tool_started" and e["payload"]["tool"] == "execute_command"
    ]
    assert before_children == after_children and len(checks) == 1
    replies = [m for m in cold.load_message_history("selection") if m.get("tool_call_id") == parent[1]]
    assert len(replies) == 1 and json.loads(replies[0]["content"]) == asdict(observation)
    assert cold.load_run("selection", workspace=tmp_path)["project_outcome"] == "accepted"


async def test_observed_parent_missing_message_repaired_without_duplicate_observation(tmp_path):
    from polaris.cells.roles.runtime.internal.engineering_verification import verification_observation

    store, verifier = setup(tmp_path)
    assert store.load_run("selection", workspace=tmp_path).get("verification_commands") == []
    parent, _checks = await request(store, verifier, tmp_path, [command("assert 3 == 3")], close=False)
    observation = verification_observation(parent[1], verifier.current_completed_result("selection"), 20_000)
    store.record_tool_observation("selection", turn_seq=parent[0], observation=observation, role="director")
    before = [e for e in store.load_events("selection") if e["kind"] == "tool_observed"]
    with store.acquire_workspace(tmp_path):
        store.reconcile_verification_calls("selection", max_output_bytes=20_000)
    assert [e for e in store.load_events("selection") if e["kind"] == "tool_observed"] == before
    assert len([m for m in store.load_message_history("selection") if m.get("tool_call_id") == parent[1]]) == 1


async def test_missing_completion_keeps_known_commands_and_parent_unknown(tmp_path, monkeypatch):
    store, verifier = setup(tmp_path)
    assert store.load_run("selection", workspace=tmp_path).get("verification_commands") == []
    parent = dispatch(store, [command('print("actual command")')])

    def commit_failure(*args, **kwargs):
        raise OSError("injected completed binding commit failure")

    monkeypatch.setattr(store, "record_verification", commit_failure)
    with pytest.raises(OSError, match="commit failure"):
        await verifier.run_verification(
            (command('print("actual command")'),), session_id="selection", workspace_revision=0, requested_by=parent
        )
    before = store.load_events("selection")
    cold = EngineeringSessionStore(tmp_path)
    with cold.acquire_workspace(tmp_path):
        cold.reconcile_verification_calls("selection", max_output_bytes=20_000)
    assert cold.load_events("selection") == before
    assert [p["call_id"] for p in cold.pending_tool_calls("selection")] == [parent[1]]
    assert verifier.current_completed_result("selection") is None


async def test_unknown_child_or_wrong_parent_cannot_be_reconciled(tmp_path):
    store, verifier = setup(tmp_path)
    assert store.load_run("selection", workspace=tmp_path).get("verification_commands") == []
    parent, _checks = await request(store, verifier, tmp_path, [command('print("completed")')], close=False)
    payload = verifier.current_completed_result("selection")
    attempt = payload["input_binding"]["attempt_seq"]
    store.record_tool_dispatch(
        "selection",
        turn_seq=attempt,
        call_id="unknown-extra-child",
        tool="execute_command",
        arguments={"command": "never replay"},
        role="director",
    )
    other_parent = dispatch(store, [command('print("completed")')], "different-parent")
    before = store.load_events("selection")
    with store.acquire_workspace(tmp_path):
        store.reconcile_verification_calls("selection", max_output_bytes=20_000)
    assert store.load_events("selection") == before
    assert {p["call_id"] for p in store.pending_tool_calls("selection")} == {
        parent[1],
        other_parent[1],
        "unknown-extra-child",
    }


async def test_recovery_of_stale_completion_cannot_accept_new_source(tmp_path):
    store, verifier = setup(tmp_path)
    assert store.load_run("selection", workspace=tmp_path).get("verification_commands") == []
    await request(store, verifier, tmp_path, [command("assert 8 == 8")], close=False)
    (tmp_path / "source.py").write_text("changed=1\n", encoding="utf-8")
    with store.acquire_workspace(tmp_path):
        store.reconcile_verification_calls("selection", max_output_bytes=20_000)
    assert not store.pending_tool_calls("selection")
    assert verifier.current_completed_result("selection") is None
    assert verifier.current_verification("selection") == "unknown"


async def test_selection_start_atomically_invalidates_previous_checks_and_binds_exact_attempt(tmp_path, monkeypatch):
    store, verifier = setup(tmp_path)
    assert store.load_run("selection", workspace=tmp_path).get("verification_commands") == []
    await request(store, verifier, tmp_path, [command("assert 4 == 4")])
    original = verifier.tools.execute
    observed = []

    async def inspect_start(*args, **kwargs):
        row = store.load_run("selection", workspace=tmp_path)
        observed.append((row["verification"], row["checks"]))
        return await original(*args, **kwargs)

    monkeypatch.setattr(verifier.tools, "execute", inspect_start)
    await request(store, verifier, tmp_path, [command("assert 6 == 6")], identity="second")
    assert observed == [("not_run", [])]
    payload = verifier.current_completed_result("selection")
    wrong_binding = {**payload["input_binding"], "attempt_seq": payload["input_binding"]["attempt_seq"] + 1000}
    with pytest.raises(ValueError):
        store.record_verification(
            "selection",
            workspace_revision=0,
            checks=tuple(ToolObservation(**c) for c in payload["checks"]),
            verification="passed",
            detail="must reject receipts from another turn",
            input_binding=wrong_binding,
        )
    store.update_run("selection", workspace_revision=1, verification="not_run", checks=())
    assert verifier.current_completed_result("selection") is None


async def test_same_unclosed_parent_cannot_start_a_second_physical_attempt(tmp_path):
    store, verifier = setup(tmp_path)
    selected = [command('print("exactly once")')]
    parent, _checks = await request(store, verifier, tmp_path, selected, close=False)
    with pytest.raises(ValueError, match=r"already.*attempt"):
        await verifier.run_verification(
            tuple(selected), session_id="selection", workspace_revision=0, requested_by=parent
        )
    children = [
        e
        for e in store.load_events("selection")
        if e["kind"] == "tool_started" and e["payload"]["tool"] == "execute_command"
    ]
    assert len(children) == 1


async def test_recovery_response_and_message_roll_back_together(tmp_path):
    store, verifier = setup(tmp_path)
    parent, _checks = await request(store, verifier, tmp_path, [command("assert 10 == 10")], close=False)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("""CREATE TRIGGER reject_recovered_message BEFORE INSERT ON events
            WHEN NEW.kind='message' BEGIN SELECT RAISE(ABORT, 'injected message failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected message failure"), store.acquire_workspace(tmp_path):
        store.reconcile_verification_calls("selection", max_output_bytes=20_000)
    assert [p["call_id"] for p in store.pending_tool_calls("selection")] == [parent[1]]
    assert not store.load_message_history("selection")
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("DROP TRIGGER reject_recovered_message")
    with store.acquire_workspace(tmp_path):
        store.reconcile_verification_calls("selection", max_output_bytes=20_000)
    assert not store.pending_tool_calls("selection")


async def test_real_command_without_observation_cannot_close_unknown_parent(tmp_path, monkeypatch):
    store, verifier = setup(tmp_path)
    selected = [command('print("actual effect before observation")')]
    parent = dispatch(store, selected)
    original = store.record_tool_observation

    def fail_child_observation(session_id, *, turn_seq, observation, role):
        if observation.tool == "execute_command":
            raise OSError("injected child observation failure")
        return original(session_id, turn_seq=turn_seq, observation=observation, role=role)

    monkeypatch.setattr(store, "record_tool_observation", fail_child_observation)
    await verifier.run_verification(tuple(selected), session_id="selection", workspace_revision=0, requested_by=parent)
    before = store.load_events("selection")
    cold = EngineeringSessionStore(tmp_path)
    with cold.acquire_workspace(tmp_path):
        cold.reconcile_verification_calls("selection", max_output_bytes=20_000)
    assert cold.load_events("selection") == before
    assert len(cold.pending_tool_calls("selection")) == 2
    assert cold.load_run("selection", workspace=tmp_path)["verification"] == "unknown"
    assert verifier.current_completed_result("selection") is None


@pytest.mark.parametrize("old_verdict", ["approved", "rejected"])
async def test_new_selection_immediately_invalidates_previous_qa_verdict(tmp_path, monkeypatch, old_verdict):
    store, verifier = setup(tmp_path, require_qa=True)
    await request(store, verifier, tmp_path, [command("assert 12 == 12")])
    # A durable host QA fixture isolates projection invalidation, not real QA execution.
    store.append_event(
        "selection",
        kind="qa_review_completed",
        role="qa",
        payload={"verdict": old_verdict, "input_binding": verifier.review_binding("selection")},
    )
    store.update_run("selection", status="completed")
    assert store.load_run("selection", workspace=tmp_path)["qa_verdict"] == old_verdict
    observed = []
    original = verifier.tools.execute

    async def inspect_start(*args, **kwargs):
        event = next(e for e in reversed(store.load_events("selection")) if e["kind"] == "verification_started")
        row = store.load_run("selection", workspace=tmp_path)
        observed.append((event["payload"], row))
        return await original(*args, **kwargs)

    monkeypatch.setattr(verifier.tools, "execute", inspect_start)
    await request(store, verifier, tmp_path, [command("assert 13 == 13")], identity="fresh-selection")
    for projection in observed[0]:
        assert projection["qa_verdict"] == "unknown" and projection["project_outcome"] == "unknown"
        assert projection["acceptance_basis"] is None and projection["checks"] == []
    assert store.load_run("selection", workspace=tmp_path)["qa_verdict"] == "unknown"
