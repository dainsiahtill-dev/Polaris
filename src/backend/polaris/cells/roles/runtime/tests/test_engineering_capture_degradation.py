from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    PermissionScope,
    RoleRunCommand,
    RunLimits,
    ToolObservation,
)


def test_missing_capture_metadata_advances_revision_without_failing(tmp_path: Path) -> None:
    store = EngineeringSessionStore(tmp_path)
    command = RoleRunCommand("run-1", "director", "work", tmp_path, "direct", ())
    scope = PermissionScope((tmp_path,), (tmp_path,), frozenset({"execute_command"}), False)
    limits = RunLimits(time.time() + 60, 10_000, 2_000)
    session_id = store.create_run(
        command, scope=scope, limits=limits, engine_version="engineering_v2", request_key="cap-1"
    )
    turn_seq = store.append_event(session_id, kind="turn_started", role="director", payload={})
    observation = ToolObservation("call-1", "execute_command", True, "done", 0, False)
    store.record_tool_dispatch(
        session_id,
        turn_seq=turn_seq,
        call_id="call-1",
        tool="execute_command",
        arguments={"command": "ls"},
        role="director",
    )

    # No capture completion payload: must not raise, and must advance revision.
    store.record_tool_observation(
        session_id,
        turn_seq=turn_seq,
        observation=observation,
        role="director",
        advance_workspace_revision=True,
        expected_workspace_revision=0,
    )

    row = (
        sqlite3.connect(f"file:{store.database_path}?mode=ro", uri=True)
        .execute("SELECT workspace_revision FROM runs WHERE session_id=?", (session_id,))
        .fetchone()
    )
    assert row[0] == 1
