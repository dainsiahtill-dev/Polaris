"""Real-store event selection keeps authority, ordered facts and recovery behavior."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType

SESSION_ID = "event-selection"


def _create(store: EngineeringSessionStore, *, session_id: str = SESSION_ID) -> None:
    store.create_run(
        RoleRunCommand(
            session_id, "director", "保留原始目标", store.workspace, required_checks=("python -m unittest",)
        ),
        scope=PermissionScope((store.workspace,), (), frozenset({"read_file", "execute_command"}), False),
        limits=RunLimits(4_000_000_000.0, 100000, 64000),
        engine_version="engineering_v2",
        request_key="original-request" if session_id == SESSION_ID else None,
    )


@pytest.fixture
def store(tmp_path: Path) -> EngineeringSessionStore:
    result = EngineeringSessionStore(tmp_path)
    _create(result)
    return result


@pytest.fixture
def journal(store: EngineeringSessionStore) -> EngineeringSessionStore:
    facts: list[tuple[str, str, dict[str, object]]] = [
        ("request_prepared", "director", {"auxiliary": "无关"}),
        ("message", "director", {"message": {"role": "user", "content": "原始目标"}}),
        ("turn_started", "qa", {}),
        ("tool_observed", "director", {"call_id": "same", "frame": 1}),
        ("message", "", {"message": {"role": "user", "content": "empty role"}}),
        ("turn_started", "director", {}),
        ("verification_completed", "qa", {"verification": "failed"}),
        ("tool_observed", "qa", {"call_id": "same", "frame": 2}),
        ("message", "director", {"message": {"role": "assistant", "content": "保留完整历史"}}),
        ("message') OR 1=1 --", "director' OR 1=1 --", {"literal": True}),
    ]
    for kind, role, payload in facts:
        store.append_event(SESSION_ID, kind=kind, role=role, payload=payload)
    _create(store, session_id="other-session")
    store.append_message("other-session", role="director", message={"role": "user", "content": "隔离"})
    return store


def _corrupt_payload(store: EngineeringSessionStore, seq: int) -> None:
    # Deliberate corruption belongs only to this disposable test database.
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("UPDATE events SET payload=? WHERE session_id=? AND seq=?", ("{broken", SESSION_ID, seq))


def test_default_and_explicit_none_keep_all_event_fields_and_history(journal: EngineeringSessionStore) -> None:
    events = journal.load_events(SESSION_ID)
    assert journal.load_events(SESSION_ID, kinds=None, role=None) == events
    assert [event["seq"] for event in events] == list(range(1, 11))
    assert [event["kind"] for event in events] == [
        "request_prepared",
        "message",
        "turn_started",
        "tool_observed",
        "message",
        "turn_started",
        "verification_completed",
        "tool_observed",
        "message",
        "message') OR 1=1 --",
    ]
    assert events[1]["payload"] == {"message": {"role": "user", "content": "原始目标"}}
    assert all(set(event) == {"seq", "kind", "role", "payload", "created_at"} for event in events)
    assert all(isinstance(event["created_at"], float) for event in events)


@pytest.mark.parametrize(
    ("kinds", "role", "sequences"),
    [
        (("message",), None, [2, 5, 9]),
        (None, "director", [1, 2, 4, 6, 9]),
        (("message", "tool_observed"), "director", [2, 4, 9]),
        (("tool_observed", "message", "message"), "qa", [8]),
        ((), None, []),
        ((), "director", []),
        (None, "", [5]),
        (("message",), "", [5]),
        (("absent",), None, []),
        (None, "absent", []),
        (("message') OR 1=1 --",), "director' OR 1=1 --", [10]),
        (("message",), "director' OR 1=1 --", []),
    ],
)
def test_selection_keeps_exact_values_and_original_order(
    journal: EngineeringSessionStore, kinds: tuple[str, ...] | None, role: str | None, sequences: list[int]
) -> None:
    # Literal sequence expectations catch widening, dropped history and SQL interpolation.
    selected = journal.load_events(SESSION_ID, kinds=kinds, role=role)
    assert [event["seq"] for event in selected] == sequences
    full = journal.load_events(SESSION_ID)
    assert selected == [event for event in full if event["seq"] in sequences]


@pytest.mark.parametrize("kinds", [None, (), ("absent",)])
def test_unknown_run_is_rejected_even_when_selection_would_be_empty(
    store: EngineeringSessionStore, kinds: tuple[str, ...] | None
) -> None:
    with pytest.raises(KeyError, match="unknown engineering session"):
        store.load_events("unknown", kinds=kinds, role="")


@pytest.mark.parametrize("kinds", [None, (), ("absent",)])
def test_replaced_database_is_rejected_even_when_selection_would_be_empty(
    store: EngineeringSessionStore, kinds: tuple[str, ...] | None
) -> None:
    replacement = store.database_path.with_name("replacement.sqlite3")
    shutil.copyfile(store.database_path, replacement)
    replacement.replace(store.database_path)
    with pytest.raises(PermissionError, match="control database changed"):
        store.load_events(SESSION_ID, kinds=kinds, role="")


def test_empty_selection_still_checks_control_directory_identity(store: EngineeringSessionStore) -> None:
    original = store.control_root
    displaced = original.with_name("displaced-engineering")
    original.rename(displaced)
    original.mkdir()
    shutil.copyfile(displaced / "sessions.sqlite3", original / "sessions.sqlite3")
    with pytest.raises(PermissionError, match="control directory changed"):
        store.load_events(SESSION_ID, kinds=())


def test_selected_invalid_json_and_default_full_read_both_fail(journal: EngineeringSessionStore) -> None:
    _corrupt_payload(journal, 2)
    with pytest.raises(json.JSONDecodeError):
        journal.load_events(SESSION_ID, kinds=("message",), role="director")
    with pytest.raises(json.JSONDecodeError):
        journal.load_events(SESSION_ID)


def test_selection_excludes_unrelated_payload_without_claiming_global_health(journal: EngineeringSessionStore) -> None:
    _corrupt_payload(journal, 1)
    assert [event["seq"] for event in journal.load_events(SESSION_ID, kinds=("turn_started",), role="director")] == [6]
    with pytest.raises(json.JSONDecodeError):
        journal.load_events(SESSION_ID)


def test_pending_calls_preserve_composite_identity_without_decoding_auxiliary_events(
    store: EngineeringSessionStore,
) -> None:
    auxiliary = store.append_event(SESSION_ID, kind="request_prepared", role="director", payload={})
    for turn in (1, 2):
        store.record_tool_dispatch(
            SESSION_ID,
            turn_seq=turn,
            call_id="reused",
            tool="write_file",
            arguments={"file": "fixture.txt"},
            role="director",
        )
    store.append_event(
        SESSION_ID,
        kind="tool_reconciled",
        role="director",
        payload={"turn_seq": 1, "call_id": "reused", "original_effect": "unknown"},
    )
    _corrupt_payload(store, auxiliary)
    pending = store.pending_tool_calls(SESSION_ID)
    assert [(row["turn_seq"], row["call_id"], row["status"]) for row in pending] == [(2, "reused", "started")]
    assert pending[0]["arguments"] == {"file": "fixture.txt"}


def test_review_binding_keeps_current_manifest_and_latest_completion_without_auxiliary_decode(
    store: EngineeringSessionStore,
) -> None:
    auxiliary = store.append_event(SESSION_ID, kind="request_prepared", role="director", payload={})
    store.append_event(SESSION_ID, kind="verification_completed", role="director", payload={"verification": "failed"})
    latest = store.append_event(
        SESSION_ID, kind="verification_completed", role="qa", payload={"verification": "unknown"}
    )
    verifier = EngineeringVerification(
        store.workspace,
        store=store,
        scope=PermissionScope((store.workspace,), (), frozenset(), False),
        limits=RunLimits(None, 100000, 64000),
        cancel=asyncio.Event(),
    )
    original = verifier.review_binding(SESSION_ID)
    _corrupt_payload(store, auxiliary)
    binding = verifier.review_binding(SESSION_ID)
    assert binding == original
    assert binding["verification_seq"] == latest
    assert binding["required_checks"] == ["python -m unittest"]
    inputs = binding["inputs"]
    assert isinstance(inputs, dict)
    assert str(store.workspace) in inputs
    assert set(binding) == {"workspace_revision", "verification_seq", "required_checks", "scope", "profile", "inputs"}


def test_native_capture_ignores_auxiliary_payload_but_preserves_python_turn_identity(
    tmp_path: Path,
) -> None:
    service = EngineeringRunService(tmp_path)
    _create(service.store)
    turn = service.store.append_event(SESSION_ID, kind="turn_started", role="director", payload={})
    auxiliary = service.store.append_event(SESSION_ID, kind="request_prepared", role="director", payload={})
    _corrupt_payload(service.store, auxiliary)
    request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="goal",
        context={
            "session_id": SESSION_ID,
            "workspace": str(tmp_path),
            "engine_version": "engineering_v2",
            "turn_seq": turn,
        },
    )
    for wrong_turn, wrong_role in ((str(turn), "director"), (turn, "qa")):
        request.context["turn_seq"] = wrong_turn
        request.role = wrong_role
        with pytest.raises(ValueError, match="no matching host role turn"):
            service.record_native_request_observation(request, {"phase": "prepared"})
    assert service.store.load_events(SESSION_ID, kinds=("llm_request_observed",)) == []
    request.context["turn_seq"] = turn
    request.role = "director"
    service.record_native_request_observation(request, {"phase": "prepared", "usage_status": "unknown"})
    observed = service.store.load_events(SESSION_ID, kinds=("llm_request_observed",))
    assert len(observed) == 1
    assert observed[0]["payload"]["turn_seq"] == turn
    assert observed[0]["role"] == "director"


def test_fresh_interpreter_reopens_original_facts_and_does_not_replay_unknown_command(
    store: EngineeringSessionStore,
) -> None:
    message = {"role": "user", "content": "原始目标和权限保持"}
    store.append_message(SESSION_ID, role="director", message=message)
    turn = store.append_event(SESSION_ID, kind="turn_started", role="director", payload={})
    marker = store.workspace / "must-not-execute.txt"
    store.record_tool_dispatch(
        SESSION_ID,
        turn_seq=turn,
        call_id="unknown-command",
        tool="execute_command",
        arguments={
            "command": f"python3 -c \"from pathlib import Path; Path('{marker.name}').write_text('replayed', encoding='utf-8')\""
        },
        role="director",
    )
    store.update_run(SESSION_ID, status="running", verification="unknown")
    original_turns = store.load_events(SESSION_ID, kinds=("turn_started",), role="director")
    script = """
import asyncio
import json
import sys
from pathlib import Path
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope
workspace = Path(sys.argv[1])
session_id = sys.argv[2]
scope = PermissionScope((workspace,), (), frozenset({'read_file', 'execute_command'}), False)
service = EngineeringRunService(workspace, permission_resolver=lambda row: scope)
async def main():
    before = service.store.load_run(session_id, workspace=workspace)
    messages = service.store.load_message_history(session_id, role='director')
    selected = service.store.load_events(session_id, kinds=('turn_started',), role='director')
    try:
        await service.resume(session_id, workspace=workspace)
    except RuntimeError as error:
        assert 'unknown' in str(error), str(error)
    else:
        raise AssertionError('unknown command resumed')
    after = service.store.load_run(session_id, workspace=workspace)
    print(json.dumps({'before': before, 'after': after, 'messages': messages, 'selected': selected,
                      'pending': service.store.pending_tool_calls(session_id)}, ensure_ascii=False))
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(store.workspace), SESSION_ID],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[5])},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    facts: dict[str, Any] = json.loads(result.stdout.splitlines()[-1])
    assert facts["messages"] == [message]
    assert facts["selected"] == original_turns
    assert facts["before"]["request_key"] == "original-request"
    for key in ("session_id", "instruction", "scope", "limits", "required_checks", "engine_version", "workspace"):
        assert facts["after"][key] == facts["before"][key]
    assert facts["after"]["status"] == "interrupted"
    assert facts["after"]["verification"] == "unknown"
    assert [(row["turn_seq"], row["call_id"], row["status"]) for row in facts["pending"]] == [
        (turn, "unknown-command", "started")
    ]
    assert not marker.exists()
    assert store.load_events(SESSION_ID, kinds=("tool_observed",)) == []
