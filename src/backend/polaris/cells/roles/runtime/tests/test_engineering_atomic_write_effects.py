"""Real engineering service and SQLite recovery after a native rename fault.

Provider responses are finite synthetic HTTP. KFS, native writes, the existing
service/loop/session, and resume admission remain real. No target commands run.
"""

from __future__ import annotations

import asyncio
import errno
import json
import time
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    scripted_provider as scripted_provider,
)
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem, guarded_atomic_write as native


def _response(tool: str | None = None, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": "Continue the same goal."}
    if tool:
        message["tool_calls"] = [
            {
                "id": "original-write",
                "type": "function",
                "function": {
                    "name": tool,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        ]
    return {"choices": [{"message": message, "finish_reason": "tool_calls" if tool else "stop"}]}


async def _settled(service: Any, session_id: str, workspace: Path) -> dict[str, Any]:
    async with asyncio.timeout(20):
        while session_id in service._tasks:
            await asyncio.sleep(0.01)
    return service.observe(session_id, workspace=workspace)


async def test_real_postrename_unknown_can_resume_without_replaying_original_write(
    tmp_path: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    content = "完整 new bytes\n"
    scope = PermissionScope((tmp_path,), (tmp_path,), frozenset({"read_file", "write_file"}), False)
    client = scripted_provider.client("openai", tmp_path, monkeypatch)
    service = EngineeringRunService(tmp_path, client=client, permission_resolver=lambda _row: scope)
    command = RoleRunCommand("native-postrename", "director", "Write artifact.txt with the requested text.", tmp_path)
    scripted_provider.enqueue_json(_response("write_file", {"file": "artifact.txt", "content": content}))
    original_sync = native._sync_directories
    sync_calls = 0

    def fail_after_native_rename(directories: list[native._DirectoryWitness]) -> None:
        nonlocal sync_calls
        sync_calls += 1
        if sync_calls == 2:
            raise OSError(errno.EIO, "synthetic postrename directory fsync failure")
        original_sync(directories)

    monkeypatch.setattr(native, "_sync_directories", fail_after_native_rename)
    await service.start(command, scope=scope, limits=RunLimits(time.time() + 60, 1_000_000, 64_000))
    first = await _settled(service, command.session_id, tmp_path)
    assert first["status"] == "interrupted" and first["verification"] == "unknown", first
    assert (tmp_path / "artifact.txt").read_text(encoding="utf-8") == content
    before = (tmp_path / "artifact.txt").stat()
    pending = service.store.pending_tool_calls(command.session_id)
    assert len(pending) == 1 and pending[0]["call_id"] == "original-write", pending
    events = service.store.load_events(command.session_id)
    assert {"tool_started", "role_error", "role_finished", "run_ended"} <= {event["kind"] for event in events}
    assert not any(
        event["kind"] == "tool_observed" and event["payload"].get("call_id") == "original-write" for event in events
    )

    # A fresh reply proves same-service lawful continuation. No original write
    # is issued again; existing reconciliation dispatches a separate real read.
    scripted_provider.enqueue_json(_response())
    await service.resume(command.session_id, workspace=tmp_path)
    later = await _settled(service, command.session_id, tmp_path)
    assert later["status"] == "completed", later
    after = (tmp_path / "artifact.txt").stat()
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)
    assert not service.store.pending_tool_calls(command.session_id)
    events = service.store.load_events(command.session_id)
    reconciled = [event for event in events if event["kind"] == "tool_reconciled"]
    assert len(reconciled) == 1 and reconciled[0]["payload"]["original_effect"] == "unknown"
    assert (
        len([event for event in events if event["kind"] == "tool_started" and event["payload"]["tool"] == "write_file"])
        == 1
    )
    with service.store._connection() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT call_id,tool,status,observation FROM tool_calls WHERE session_id=? ORDER BY started_at",
                (command.session_id,),
            )
        ]
    assert rows[0]["call_id"] == "original-write" and rows[0]["status"] == "started"
    assert rows[0]["observation"] is None
    assert any(row["tool"] == "read_file" and row["status"] == "observed" for row in rows)
    assert len(scripted_provider.requests) == 2
    final_request = scripted_provider.requests[0]["body"]
    assert final_request["messages"][0]["role"] == "system"
    assert "Director" in final_request["messages"][0]["content"]
    assert {tool["function"]["name"] for tool in final_request["tools"]} == {"read_file", "write_file"}
    fs = KernelFileSystem(str(tmp_path), LocalFileSystemAdapter())
    fs.workspace_write_text(
        ".polaris/t5-evidence.json",
        json.dumps(
            {
                "first": first,
                "later": later,
                "raw_tool_rows": rows,
                "events": events,
                "final_provider_requests": scripted_provider.requests,
                "original_effect": "unknown",
                "no_original_replay": True,
                "scope": "synthetic-provider real native KFS/service/SQLite; no command verifier",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("edit_file", {"file": "artifact.txt", "old_text": "old", "new_text": "new"}),
        ("execute_command", {"command": "must not execute"}),
    ],
)
async def test_unsupported_unknown_effect_is_not_waived_or_replayed(
    tmp_path: Path, tool: str, arguments: dict[str, Any]
) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    scope = PermissionScope(
        (tmp_path,), (tmp_path,), frozenset({"read_file", "write_file", "edit_file", "execute_command"}), False
    )
    service = EngineeringRunService(tmp_path, permission_resolver=lambda _row: scope)
    command = RoleRunCommand("unsupported-unknown", "director", "Continue the same goal.", tmp_path)
    service.store.create_run(
        command,
        scope=scope,
        limits=RunLimits(time.time() + 60, 1_000_000, 64_000),
        engine_version="engineering_v2",
        request_key=None,
    )
    service.store.record_tool_dispatch(
        command.session_id, turn_seq=1, call_id="unknown", tool=tool, arguments=arguments, role="director"
    )
    service.store.update_run(command.session_id, status="interrupted", verification="unknown")
    with pytest.raises(RuntimeError, match="unknown"):
        await service.resume(command.session_id, workspace=tmp_path)
    assert service.observe(command.session_id, workspace=tmp_path)["verification"] == "unknown"
    assert service.store.pending_tool_calls(command.session_id)[0]["call_id"] == "unknown"
    events = service.store.load_events(command.session_id)
    assert any(event["kind"] == "resume_blocked" for event in events)
    assert not any(event["kind"] in {"tool_observed", "tool_reconciled"} for event in events)
