"""Cold read-only lifecycle projection; never current-artifact acceptance."""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits


def create(
    workspace: Path,
    *,
    session_id: str = "run-1",
    status: str = "completed",
    release: bool | None = True,
    engine: str = "engineering_v2",
) -> EngineeringSessionStore:
    workspace.mkdir(exist_ok=True)
    store = EngineeringSessionStore(workspace)
    store.create_run(
        RoleRunCommand(session_id, "director", "Synthetic goal", workspace),
        scope=PermissionScope((workspace,), (), frozenset({"read_file"}), False),
        limits=RunLimits(1900000000.0, 10000, 2000),
        engine_version=engine,
        request_key="request-" + session_id,
    )
    store.update_run(session_id, status=status, verification="passed")
    if release is not None:
        store.append_event(
            session_id,
            kind="run_ended",
            role="director",
            payload={
                "status": status,
                "verification": "passed",
                "release_allowed": release,
                "drain_reporting_error": None,
            },
        )
    return store


def query(workspace: Path, **kwargs):
    name = "polaris.cells.roles.runtime.public.engineering_resource_state"
    assert importlib.util.find_spec(name) is not None, "public resource query missing"
    return importlib.import_module(name).query_engineering_resource_state(workspace, **kwargs)


def test_cold_terminal_release_is_readonly_and_never_acceptance(tmp_path: Path):
    store = create(tmp_path)
    before = store.database_path.read_bytes()
    result = query(tmp_path, session_id="run-1", request_key="request-run-1")
    assert result["safe_to_stop"] is True
    assert result["run_id"] == "run-1" and result["run_status"] == "completed"
    assert result["release_allowed"] is True
    assert result["acceptance"] == "unknown"
    assert "project_outcome" not in result
    assert store.database_path.read_bytes() == before


@pytest.mark.parametrize(
    "status,release,engine",
    [
        ("running", True, "engineering_v2"),
        ("completed", None, "engineering_v2"),
        ("completed", False, "engineering_v2"),
        ("completed", True, "old"),
    ],
)
def test_nonreleased_active_and_legacy_refuse(tmp_path: Path, status, release, engine):
    create(tmp_path, status=status, release=release, engine=engine)
    assert query(tmp_path, session_id="run-1")["safe_to_stop"] is False


def test_missing_database_does_not_initialize_owner(tmp_path: Path):
    assert query(tmp_path, session_id="absent")["safe_to_stop"] is False
    assert not (tmp_path / ".polaris").exists()


def test_exact_request_lookup_cannot_select_unrelated_run(tmp_path: Path):
    create(tmp_path)
    assert query(tmp_path, request_key="request-run-1")["run_id"] == "run-1"
    assert query(tmp_path, session_id="wrong", request_key="request-run-1")["safe_to_stop"] is False
    assert query(tmp_path, request_key="absent")["safe_to_stop"] is False


def test_other_active_and_unknown_tool_effect_refuse(tmp_path: Path):
    store = create(tmp_path)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "INSERT INTO tool_calls VALUES(?,?,?,?,?,?,?, ?,?)",
            ("run-1", 1, "unknown-write", "write_file", "{}", "started", None, 1.0, None),
        )
    result = query(tmp_path, session_id="run-1")
    assert result["safe_to_stop"] is False and result["pending_tool_calls"][0]["call_id"] == "unknown-write"
    create(tmp_path, session_id="other", status="running")
    assert query(tmp_path, session_id="run-1")["active_runs"][0]["session_id"] == "other"


def test_busy_and_corrupt_database_refuse_without_writing(tmp_path: Path):
    store = create(tmp_path)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("BEGIN EXCLUSIVE")
        assert query(tmp_path, session_id="run-1")["safe_to_stop"] is False
    store.database_path.write_bytes(b"not sqlite")
    assert query(tmp_path, session_id="run-1")["safe_to_stop"] is False


def test_symlink_control_namespace_refuses(tmp_path: Path):
    root = tmp_path / "workspace"
    create(root)
    original = root / ".polaris"
    replacement = tmp_path / "elsewhere"
    original.rename(replacement)
    original.symlink_to(replacement, target_is_directory=True)
    assert query(root, session_id="run-1")["safe_to_stop"] is False


def test_query_never_constructs_recovery_writing_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = create(tmp_path)
    before = store.database_path.read_bytes()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("read must not construct/migrate/recover a writer")

    monkeypatch.setattr(EngineeringSessionStore, "__init__", forbidden)
    assert query(tmp_path, session_id="run-1")["safe_to_stop"] is True
    assert store.database_path.read_bytes() == before


def test_drain_reporting_error_and_later_work_refuse(tmp_path: Path):
    store = create(tmp_path)
    store.append_event(
        "run-1",
        kind="run_ended",
        role="director",
        payload={"status": "completed", "release_allowed": True, "drain_reporting_error": "journal unavailable"},
    )
    assert query(tmp_path, session_id="run-1")["safe_to_stop"] is False
    store.append_event(
        "run-1",
        kind="run_ended",
        role="director",
        payload={"status": "completed", "release_allowed": True, "drain_reporting_error": None},
    )
    store.append_event("run-1", kind="role_started", role="director", payload={})
    assert query(tmp_path, session_id="run-1")["safe_to_stop"] is False


def test_reconciled_unknown_effect_remains_unknown_but_is_not_pending(tmp_path: Path):
    store = create(tmp_path)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "INSERT INTO tool_calls VALUES(?,?,?,?,?,?,?,?,?)",
            ("run-1", 1, "write", "write_file", "{}", "started", None, 1.0, None),
        )
    store.append_event(
        "run-1",
        kind="tool_reconciled",
        role="director",
        payload={
            "turn_seq": 1,
            "call_id": "write",
            "original_effect": "unknown",
            "continuation_basis": "current_file_matches_requested_write",
        },
    )
    result = query(tmp_path, session_id="run-1")
    assert result["pending_tool_calls"] == []
    assert result["reconciled_tool_calls"][0]["original_effect"] == "unknown"
    assert result["acceptance"] == "unknown" and result["safe_to_stop"] is True
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT status FROM tool_calls").fetchone()[0] == "started"


@pytest.mark.parametrize("payload", ["null", "[]", "123"])
def test_malformed_terminal_fact_is_unknown(tmp_path: Path, payload: str):
    store = create(tmp_path)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("UPDATE events SET payload=? WHERE kind='run_ended'", (payload,))
    result = query(tmp_path, session_id="run-1")
    assert result["status"] == "unknown" and result["safe_to_stop"] is False
