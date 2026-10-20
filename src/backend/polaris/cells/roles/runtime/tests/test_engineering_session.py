"""Real SQLite and OS-lock behavior of the engineering session owner."""

from __future__ import annotations

import importlib
import importlib.util
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


@pytest.fixture
def workspace():
    root = Path.home() / ".polaris" / "test-workspaces"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="engineering-session-", dir=root) as directory:
        yield Path(directory)


def api():
    module_name = "polaris.cells.roles.runtime.internal.engineering_session"
    assert importlib.util.find_spec(module_name) is not None, "engineering session store missing"
    store = importlib.import_module(module_name)
    types = importlib.import_module("polaris.cells.roles.runtime.public.contracts.engineering_run")
    return store, types


def create(workspace, *, request_key="request-1", **changes):
    module, types = api()
    command = types.RoleRunCommand("run-1", "director", "修复计算", workspace, "collaborative", ("python -m unittest",))
    command = replace(command, **changes)
    scope = types.PermissionScope((workspace,), (workspace,), frozenset({"read_file", "write_file"}), False)
    limits = types.RunLimits(1900000000.0, 10000, 2000)
    store = module.EngineeringSessionStore(workspace)
    result = store.create_run(
        command, scope=scope, limits=limits, engine_version="engineering_v2", request_key=request_key
    )
    return store, types, command, scope, limits, result


def test_restores_original_workflow_checks_permissions_and_absolute_deadline(workspace):
    store, _, _, _, _, session_id = create(workspace)
    restored = type(store)(workspace).load_run(session_id, workspace=workspace)
    assert restored["instruction"] == "修复计算"
    assert restored["workflow"] == "collaborative"
    assert restored["required_checks"] == ["python -m unittest"]
    assert restored["scope"] == {
        "read_roots": [str(workspace)],
        "write_roots": [str(workspace)],
        "enabled_tools": ["read_file", "write_file"],
        "network_allowed": False,
    }
    assert restored["limits"] == {"deadline": 1900000000.0, "max_output_bytes": 10000, "max_tool_output_bytes": 2000}
    assert restored["workspace_revision"] == 0
    assert restored["status"] == "pending"
    database = workspace / ".polaris/runtime/engineering/sessions.sqlite3"
    assert database.is_file()
    assert database.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA encoding").fetchone()[0] == "UTF-8"
        assert {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {
            "runs",
            "events",
            "tool_calls",
        }


def test_request_key_returns_original_id_even_if_server_generated_new_id(workspace):
    store, _, command, scope, limits, _ = create(workspace)
    session_id = store.create_run(
        replace(command, session_id="new-generated-id"),
        scope=scope,
        limits=limits,
        engine_version="engineering_v2",
        request_key="request-1",
    )
    assert session_id == "run-1"


@pytest.mark.parametrize(
    "change", [{"instruction": "other"}, {"role": "qa"}, {"workflow": "direct"}, {"required_checks": ("other-check",)}]
)
def test_request_key_changed_request_conflicts(workspace, change):
    store, _, command, scope, limits, _ = create(workspace)
    with pytest.raises(ValueError, match="conflict"):
        store.create_run(
            replace(command, **change),
            scope=scope,
            limits=limits,
            engine_version="engineering_v2",
            request_key="request-1",
        )


@pytest.mark.parametrize("change", ["scope", "limits"])
def test_request_key_changed_authority_or_limits_conflicts(workspace, change):
    store, _, command, scope, limits, _ = create(workspace)
    if change == "scope":
        scope = replace(scope, write_roots=())
    else:
        limits = replace(limits, deadline=None)
    with pytest.raises(ValueError, match="conflict"):
        store.create_run(command, scope=scope, limits=limits, engine_version="engineering_v2", request_key="request-1")


def test_session_id_collision_does_not_replace_existing_run(workspace):
    store, _, command, scope, limits, _ = create(workspace)
    with pytest.raises(ValueError, match="conflict"):
        store.create_run(
            replace(command, instruction="replace existing"),
            scope=scope,
            limits=limits,
            engine_version="engineering_v2",
            request_key="another-key",
        )
    assert store.load_run("run-1", workspace=workspace)["instruction"] == "修复计算"


def test_workspace_binding_rejects_cross_workspace_read_lock_and_create(workspace):
    store, _, command, scope, limits, _ = create(workspace)
    other = workspace / "other"
    other.mkdir()
    with pytest.raises(PermissionError, match="workspace"):
        store.load_run("run-1", workspace=other)
    with pytest.raises(PermissionError, match="workspace"), store.acquire_workspace(other):
        pass
    with pytest.raises(PermissionError, match="workspace"):
        store.create_run(
            replace(command, workspace=other),
            scope=scope,
            limits=limits,
            engine_version="engineering_v2",
            request_key=None,
        )


def test_events_and_tool_observations_share_transactional_single_journal(workspace):
    store, types, *_ = create(workspace)
    assert store.append_message("run-1", role="director", message={"role": "user", "content": "原始目标"}) == 1
    turn_seq = store.append_event("run-1", kind="turn_started", role="director", payload={})
    store.record_tool_dispatch(
        "run-1",
        turn_seq=turn_seq,
        call_id="same-id",
        tool="write_file",
        arguments={"file": "calc.py", "content": "x=1"},
        role="director",
    )
    assert store.pending_tool_calls("run-1")[0]["call_id"] == "same-id"
    observation = types.ToolObservation("same-id", "write_file", True, "已写入", None, False)
    store.record_tool_observation("run-1", turn_seq=turn_seq, observation=observation, role="director")
    assert store.pending_tool_calls("run-1") == []
    events = store.load_events("run-1")
    assert [event["seq"] for event in events] == [1, 2, 3, 4]
    assert [event["kind"] for event in events] == ["message", "turn_started", "tool_started", "tool_observed"]
    assert store.load_message_history("run-1") == [{"role": "user", "content": "原始目标"}]
    store.update_run("run-1", workspace_revision=1, status="completed", text="done", verification="not_run", checks=())
    assert store.load_run("run-1", workspace=workspace)["workspace_revision"] == 1


def test_duplicate_dispatch_rejected_within_turn_but_allowed_in_later_turn(workspace):
    store, types, *_ = create(workspace)
    store.record_tool_dispatch("run-1", turn_seq=1, call_id="call", tool="read_file", arguments={}, role="director")
    with pytest.raises(ValueError, match="conflict"):
        store.record_tool_dispatch("run-1", turn_seq=1, call_id="call", tool="read_file", arguments={}, role="director")
    store.record_tool_observation(
        "run-1",
        turn_seq=1,
        observation=types.ToolObservation("call", "read_file", True, "x", None, False),
        role="director",
    )
    store.record_tool_dispatch("run-1", turn_seq=2, call_id="call", tool="read_file", arguments={}, role="director")
    assert [item["turn_seq"] for item in store.pending_tool_calls("run-1")] == [2]


def test_orphan_observation_and_duplicate_terminal_are_rejected(workspace):
    store, types, *_ = create(workspace)
    observation = types.ToolObservation("call", "read_file", True, "x", None, False)
    with pytest.raises(ValueError, match="dispatch"):
        store.record_tool_observation("run-1", turn_seq=1, observation=observation, role="director")
    store.record_tool_dispatch("run-1", turn_seq=1, call_id="call", tool="read_file", arguments={}, role="director")
    store.record_tool_observation("run-1", turn_seq=1, observation=observation, role="director")
    with pytest.raises(ValueError, match="observed"):
        store.record_tool_observation("run-1", turn_seq=1, observation=observation, role="director")
    assert len(store.load_events("run-1")) == 2


def test_revision_cannot_move_backwards_and_invalid_update_is_atomic(workspace):
    store, *_ = create(workspace)
    store.update_run("run-1", workspace_revision=2)
    with pytest.raises(ValueError, match="revision"):
        store.update_run("run-1", workspace_revision=1, text="must not persist")
    restored = store.load_run("run-1", workspace=workspace)
    assert restored["workspace_revision"] == 2
    assert restored["text"] == ""
    with pytest.raises(ValueError):
        store.update_run("run-1", scope={"network_allowed": True})


def test_flock_rejects_live_process_and_releases_after_process_death(workspace):
    store, *_ = create(workspace)
    script = """from pathlib import Path
import sys
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
store=EngineeringSessionStore(Path(sys.argv[1]))
with store.acquire_workspace(Path(sys.argv[1])):
 print('locked', flush=True)
 sys.stdin.read()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(workspace)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(BlockingIOError), store.acquire_workspace(workspace):
            pytest.fail("second writer acquired workspace")
        process.terminate()
        process.wait(timeout=5)
        with store.acquire_workspace(workspace):
            pass
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


@pytest.mark.parametrize("component", [".polaris", ".polaris/runtime", ".polaris/runtime/engineering"])
def test_control_directory_symlinks_rejected_before_host_write(workspace, component):
    module, _ = api()
    target = workspace / "outside-control"
    target.mkdir()
    link = workspace / component
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(PermissionError, match="control"):
        module.EngineeringSessionStore(workspace)
    assert list(target.iterdir()) == []


def test_request_key_cannot_hide_collision_with_different_existing_session(workspace):
    store, _, command, scope, limits, _ = create(workspace)
    store.create_run(
        replace(command, session_id="other-run", instruction="unrelated"),
        scope=scope,
        limits=limits,
        engine_version="engineering_v2",
        request_key="other-request",
    )
    with pytest.raises(ValueError, match="conflict"):
        store.create_run(
            replace(command, session_id="other-run"),
            scope=scope,
            limits=limits,
            engine_version="engineering_v2",
            request_key="request-1",
        )


def test_invalid_checks_update_cannot_write_partial_state(workspace):
    store, *_ = create(workspace)
    with pytest.raises(ValueError, match="checks"):
        store.update_run("run-1", checks="passed", status="completed")
    assert store.load_run("run-1", workspace=workspace)["status"] == "pending"


def test_terminal_event_failure_rolls_back_tool_observation(workspace):
    store, types, *_ = create(workspace)
    store.record_tool_dispatch("run-1", turn_seq=1, call_id="call", tool="read_file", arguments={}, role="director")
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("""CREATE TRIGGER reject_terminal_event BEFORE INSERT ON events
            WHEN NEW.kind='tool_observed' BEGIN SELECT RAISE(ABORT, 'terminal event rejected'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="terminal event rejected"):
        store.record_tool_observation(
            "run-1",
            turn_seq=1,
            observation=types.ToolObservation("call", "read_file", True, "x", None, False),
            role="director",
        )
    pending = store.pending_tool_calls("run-1")
    assert len(pending) == 1
    assert pending[0]["observation"] is None
    assert [event["kind"] for event in store.load_events("run-1")] == ["tool_started"]
