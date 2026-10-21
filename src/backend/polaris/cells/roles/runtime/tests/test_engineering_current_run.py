"""Workspace-scoped current-run discovery through the real engineering SQLite owner."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionConflictError
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService


def _create_run(
    service: EngineeringRunService,
    session_id: str,
    *,
    status: str = "pending",
    role: str = "director",
    engine_version: str = "engineering_v2",
    created_at: float = 1,
    updated_at: float = 1,
) -> None:
    service.store.create_run(
        RoleRunCommand(session_id, role, "原始目标", service.workspace),
        scope=PermissionScope((), (), frozenset(), False),
        limits=RunLimits(None, 1000, 1000),
        engine_version=engine_version,
        request_key=None,
    )
    with sqlite3.connect(service.store.database_path) as connection:
        connection.execute(
            "UPDATE runs SET status=?,created_at=?,updated_at=? WHERE session_id=?",
            (status, created_at, updated_at, session_id),
        )


def _dump(service: EngineeringRunService) -> list[str]:
    with sqlite3.connect(service.store.database_path) as connection:
        return list(connection.iterdump())


def test_empty_current_run_is_none_and_preserves_store(tmp_path: Path) -> None:
    service = EngineeringRunService(tmp_path)
    before = _dump(service)
    assert service.store.find_current_session_id(workspace=tmp_path, engine_version="engineering_v2") is None
    assert service.current_snapshot(workspace=tmp_path) is None
    assert _dump(service) == before


@pytest.mark.parametrize("status", ["pending", "running"])
@pytest.mark.parametrize("role", ["pm", "chief_engineer", "director", "qa"])
def test_active_run_precedes_newer_terminal_for_any_role(tmp_path: Path, status: str, role: str) -> None:
    service = EngineeringRunService(tmp_path)
    _create_run(service, "active", status=status, role=role, created_at=1)
    _create_run(service, "newer-terminal", status="completed", created_at=20)
    assert service.store.find_current_session_id(workspace=tmp_path, engine_version="engineering_v2") == "active"
    snapshot = service.current_snapshot(workspace=tmp_path)
    assert snapshot is not None
    assert snapshot["session_id"] == "active"
    assert snapshot["status"] == status and snapshot["role"] == role


@pytest.mark.parametrize("statuses", [("pending", "running"), ("pending", "pending"), ("running", "running")])
def test_multiple_active_runs_are_ambiguous(tmp_path: Path, statuses: tuple[str, str]) -> None:
    service = EngineeringRunService(tmp_path)
    for identity, status in zip(("first", "second"), statuses, strict=True):
        _create_run(service, identity, status=status)
    before = _dump(service)
    with pytest.raises(EngineeringSessionConflictError):
        service.store.find_current_session_id(workspace=tmp_path, engine_version="engineering_v2")
    with pytest.raises(EngineeringSessionConflictError):
        service.current_snapshot(workspace=tmp_path)
    assert _dump(service) == before


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "interrupted"])
def test_latest_created_terminal_wins_with_deterministic_ties(tmp_path: Path, status: str) -> None:
    service = EngineeringRunService(tmp_path)
    _create_run(service, "old-but-updated", status="completed", created_at=1, updated_at=100)
    _create_run(service, "z-tied", status=status, created_at=2, updated_at=3)
    _create_run(service, "a-tied", status=status, created_at=2, updated_at=4)
    assert service.store.find_current_session_id(workspace=tmp_path, engine_version="engineering_v2") == "z-tied"
    snapshot = service.current_snapshot(workspace=tmp_path)
    assert snapshot is not None and snapshot["session_id"] == "z-tied"


def test_selection_filters_exact_engine_and_stored_workspace(tmp_path: Path) -> None:
    service = EngineeringRunService(tmp_path)
    _create_run(service, "current", status="failed")
    _create_run(service, "old-engine-active", engine_version="legacy", created_at=30)
    _create_run(service, "foreign-active", created_at=40)
    with sqlite3.connect(service.store.database_path) as connection:
        connection.execute("UPDATE runs SET workspace=? WHERE session_id='foreign-active'", (str(tmp_path / "other"),))
    assert service.store.find_current_session_id(workspace=tmp_path, engine_version="engineering_v2") == "current"
    assert service.store.find_current_session_id(workspace=tmp_path, engine_version="absent") is None
    snapshot = service.current_snapshot(workspace=tmp_path)
    assert snapshot is not None and snapshot["session_id"] == "current"


def test_workspace_mismatch_is_rejected_without_creating_foreign_state(tmp_path: Path) -> None:
    service = EngineeringRunService(tmp_path)
    foreign = tmp_path / "uncreated"
    before = _dump(service)
    with pytest.raises(PermissionError, match="workspace mismatch"):
        service.store.find_current_session_id(workspace=foreign, engine_version="engineering_v2")
    with pytest.raises(PermissionError, match="workspace mismatch"):
        service.current_snapshot(workspace=foreign)
    assert not foreign.exists()
    assert _dump(service) == before


def test_cold_current_read_preserves_running_unknown_tool_and_events(tmp_path: Path) -> None:
    service = EngineeringRunService(tmp_path)
    _create_run(service, "unresolved", status="running")
    service.store.append_event("unresolved", kind="role_started", role="director", payload={})
    with sqlite3.connect(service.store.database_path) as connection:
        connection.execute(
            "INSERT INTO tool_calls(session_id,turn_seq,call_id,tool,arguments,status,started_at) "
            "VALUES('unresolved',1,'unknown-effect','write_file','{}','started',1)"
        )
    cold = EngineeringRunService(tmp_path)
    before = _dump(cold)
    snapshot = cold.current_snapshot(workspace=tmp_path)
    assert snapshot is not None
    assert snapshot["session_id"] == "unresolved" and snapshot["status"] == "running"
    assert snapshot["last_seq"] == 1
    assert snapshot["events"][0]["kind"] == "role_started"
    assert _dump(cold) == before
    assert not cold._tasks and not cold._cancellations and cold._client is None
    assert not (tmp_path / "unknown-effect").exists()


def test_current_snapshot_reuses_current_source_verification_without_durable_rewrite(tmp_path: Path) -> None:
    service = EngineeringRunService(tmp_path)
    _create_run(service, "stale", status="completed")
    with sqlite3.connect(service.store.database_path) as connection:
        connection.execute("UPDATE runs SET verification='passed' WHERE session_id='stale'")
    before = _dump(service)
    snapshot = service.current_snapshot(workspace=tmp_path)
    assert snapshot is not None
    assert snapshot["verification"] == "unknown" and snapshot["project_outcome"] == "unknown"
    assert _dump(service) == before
