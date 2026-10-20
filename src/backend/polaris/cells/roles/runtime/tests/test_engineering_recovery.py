"""Final checks and cold recovery: synthetic HTTP, actual files/processes/SQLite."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)


def _scope(workspace: Path) -> PermissionScope:
    return PermissionScope((workspace,), (workspace,), frozenset({"read_file", "write_file", "execute_command"}), False)


def _limits() -> RunLimits:
    return RunLimits(time.time() + 30, 1_000_000, 64_000)


def _reply(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}


def _write(identity: str, content: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": identity,
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps({"file": "artifact.txt", "content": content}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


async def _terminal(service: Any, session_id: str, workspace: Path) -> dict[str, Any]:
    async with asyncio.timeout(15):
        while True:
            row = service.observe(session_id, workspace=workspace)
            if row["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                return row
            await asyncio.sleep(0.01)


async def test_final_required_check_executes_after_reply(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    (isolated_workspace / "artifact.txt").write_text("correct", encoding="utf-8")
    scripted_provider.enqueue_json(_reply("Implementation ready."))
    service = EngineeringRunService(
        isolated_workspace, client=scripted_provider.client("openai", isolated_workspace, monkeypatch)
    )
    command = RoleRunCommand(
        "final-pass",
        "director",
        "Complete artifact.",
        isolated_workspace,
        required_checks=(
            "python3 -c \"from pathlib import Path; assert Path('artifact.txt').read_text(encoding='utf-8') == 'correct'\"",
        ),
    )
    await service.start(command, scope=_scope(isolated_workspace), limits=_limits())
    row = await _terminal(service, command.session_id, isolated_workspace)
    assert row["status"] == "completed", row
    assert row["verification"] == "passed", row
    assert len(row["checks"]) == 1 and row["checks"][0]["exit_code"] == 0
    events = service.store.load_events(command.session_id)
    assert len([event for event in events if event["kind"] == "run_ended"]) == 1
    assert not any(event["role"] == "qa" for event in events)


async def test_failed_final_check_returns_same_director_without_replanning(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    (isolated_workspace / "artifact.txt").write_text("wrong", encoding="utf-8")
    for response in (
        _reply("Keep the user's requirement."),
        _reply("Read artifact and use exact content."),
        _reply("Ready."),
        _write("repair", "correct"),
        _reply("Repaired."),
    ):
        scripted_provider.enqueue_json(response)
    service = EngineeringRunService(
        isolated_workspace, client=scripted_provider.client("openai", isolated_workspace, monkeypatch)
    )
    command = RoleRunCommand(
        "final-repair",
        "director",
        "Complete artifact.",
        isolated_workspace,
        workflow="collaborative",
        required_checks=(
            "python3 -c \"from pathlib import Path; assert Path('artifact.txt').read_text(encoding='utf-8') == 'correct'\"",
        ),
    )
    await service.start(command, scope=_scope(isolated_workspace), limits=_limits())
    row = await _terminal(service, command.session_id, isolated_workspace)
    assert row["status"] == "completed" and row["verification"] == "passed", row
    assert (isolated_workspace / "artifact.txt").read_text(encoding="utf-8") == "correct"
    events = service.store.load_events(command.session_id)
    assert len([e for e in events if e["kind"] == "role_started" and e["role"] == "pm"]) == 1
    assert len([e for e in events if e["kind"] == "role_started" and e["role"] == "chief_engineer"]) == 1
    repair_request = scripted_provider.requests[3]["body"]
    rendered = json.dumps(repair_request, ensure_ascii=False)
    assert "Read artifact and use exact content." in rendered and "AssertionError" in rendered
    assert "python3 -c" in rendered and "Ready." in rendered
    assert len([e for e in events if e["kind"] == "run_ended"]) == 1


async def test_standalone_review_never_runs_project_checks(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    scripted_provider.enqueue_json(_reply("Looks acceptable; checks still required."))
    service = EngineeringRunService(
        isolated_workspace, client=scripted_provider.client("openai", isolated_workspace, monkeypatch)
    )
    command = RoleRunCommand("review-only", "qa", "Review artifact.", isolated_workspace, required_checks=("exit 0",))
    result = await service.run_role(command, scope=_scope(isolated_workspace), limits=_limits(), cancel=asyncio.Event())
    assert result.status == "completed" and result.verification == "not_run" and result.checks == ()
    assert not any(e["kind"] == "verification_started" for e in service.store.load_events(command.session_id))


_CHILD = r"""
import asyncio, json, sys, time
from pathlib import Path
import pytest
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import ScriptedProvider
workspace, base_url, phase = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
scope = PermissionScope((workspace,), (workspace,), frozenset({'read_file', 'write_file', 'execute_command'}), False)
client = ScriptedProvider(base_url=base_url).client('openai', workspace, pytest.MonkeyPatch())
service = EngineeringRunService(workspace, client=client, permission_resolver=lambda row: scope)
async def main():
    if phase in {'crash', 'crash-command'}:
        original = service.store.record_tool_observation
        def checkpoint(session_id, *, turn_seq, observation, role):
            if observation.ok and ((phase == 'crash' and observation.tool == 'write_file') or (phase == 'crash-command' and observation.tool == 'execute_command')):
                print('WRITE_EFFECT_BEFORE_OBSERVATION' if phase == 'crash' else 'COMMAND_EFFECT_BEFORE_OBSERVATION', flush=True)
                time.sleep(60)
            return original(session_id, turn_seq=turn_seq, observation=observation, role=role)
        service.store.record_tool_observation = checkpoint
        command = RoleRunCommand('cold-write', 'director', 'Create artifact with correct content.', workspace, workflow='collaborative', required_checks=("python3 -c \"from pathlib import Path; assert Path('artifact.txt').read_text(encoding='utf-8') == 'correct'\"",))
        await service.start(command, scope=scope, limits=RunLimits(time.time()+90, 1000000, 64000))
        await asyncio.Event().wait()
    else:
        await service.resume('cold-write', workspace=workspace)
        result = await service._await_result('cold-write', asyncio.Event())
        row = service.observe('cold-write', workspace=workspace)
        print(json.dumps({'row':row, 'events':service.store.load_events('cold-write')}, ensure_ascii=False), flush=True)
asyncio.run(main())
"""


async def test_fresh_process_reads_back_unknown_write_without_replay(
    isolated_workspace: Path, scripted_provider: ScriptedProvider
) -> None:
    from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore, WorkspaceBusyError

    for response in (_reply("PM valid advice."), _reply("CE valid advice."), _write("native-crash-write", "correct")):
        scripted_provider.enqueue_json(response)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[5]), "NO_PROXY": "*", "no_proxy": "*"}
    first = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _CHILD,
        str(isolated_workspace),
        scripted_provider.base_url,
        "crash",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        assert first.stdout is not None
        marker = await asyncio.wait_for(first.stdout.readline(), timeout=20)
        assert marker == b"WRITE_EFFECT_BEFORE_OBSERVATION\n", marker
        store = EngineeringSessionStore(isolated_workspace)
        with pytest.raises(WorkspaceBusyError), store.acquire_workspace(isolated_workspace):
            pytest.fail("live foreign writer lost lock")
        pending = store.pending_tool_calls("cold-write")
        assert len(pending) == 1 and pending[0]["status"] == "started"
        before = (isolated_workspace / "artifact.txt").stat()
    finally:
        if first.returncode is None:
            first.kill()
        first_out, first_err = await first.communicate()
    assert first.returncode != 0, (first_out, first_err)
    scripted_provider.enqueue_json(_reply("Observed existing artifact, no repeated write."))
    second = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _CHILD,
        str(isolated_workspace),
        scripted_provider.base_url,
        "resume",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    out, err = await asyncio.wait_for(second.communicate(), timeout=25)
    assert second.returncode == 0, (out.decode("utf-8"), err.decode("utf-8"))
    evidence = json.loads(out.decode("utf-8").splitlines()[-1])
    row, events = evidence["row"], evidence["events"]
    assert row["workflow"] == "collaborative" and len(row["required_checks"]) == 1
    assert row["status"] == "completed" and row["verification"] == "passed", row
    after = (isolated_workspace / "artifact.txt").stat()
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)
    assert len(scripted_provider.requests) == 4
    restored = json.dumps(scripted_provider.requests[-1]["body"], ensure_ascii=False)
    assert "native-crash-write" in restored and "PM valid advice." in restored and "CE valid advice." in restored
    assert '"tool_call_id": "native-crash-write"' in restored and "reconciliation" in restored.lower()
    assert any(
        call.get("id") == "native-crash-write"
        for message in scripted_provider.requests[-1]["body"]["messages"]
        for call in message.get("tool_calls", [])
    )
    assert len([e for e in events if e["kind"] == "tool_started" and e["payload"]["tool"] == "write_file"]) == 1
    assert not any(e["kind"] == "tool_observed" and e["payload"].get("call_id") == "native-crash-write" for e in events)
    assert any(e["kind"] == "tool_reconciled" and e["payload"].get("original_effect") == "unknown" for e in events)


async def test_unobserved_command_is_never_replayed(isolated_workspace: Path) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    scope = _scope(isolated_workspace)
    service = EngineeringRunService(isolated_workspace, permission_resolver=lambda row: scope)
    command = RoleRunCommand("unknown-command", "director", "Install an external dependency.", isolated_workspace)
    service.store.create_run(command, scope=scope, limits=_limits(), engine_version="engineering_v2", request_key=None)
    service.store.record_tool_dispatch(
        command.session_id,
        turn_seq=1,
        call_id="unknown-install",
        tool="execute_command",
        arguments={"command": "exit 0"},
        role="director",
    )
    service.store.update_run(command.session_id, status="running", verification="unknown")
    with pytest.raises(RuntimeError, match="unknown"):
        await service.resume(command.session_id, workspace=isolated_workspace)
    row = service.observe(command.session_id, workspace=isolated_workspace)
    assert row["status"] == "interrupted" and row["verification"] == "unknown"
    assert len(service.store.pending_tool_calls(command.session_id)) == 1


async def test_reconciliation_cannot_waive_unknown_command(isolated_workspace: Path) -> None:
    from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
    from polaris.cells.roles.runtime.public.contracts.engineering_run import ToolObservation

    store = EngineeringSessionStore(isolated_workspace)
    command = RoleRunCommand("no-command-waiver", "director", "Do work.", isolated_workspace)
    store.create_run(
        command, scope=_scope(isolated_workspace), limits=_limits(), engine_version="engineering_v2", request_key=None
    )
    store.record_tool_dispatch(
        command.session_id,
        turn_seq=1,
        call_id="unknown",
        tool="execute_command",
        arguments={"command": "exit 0"},
        role="director",
    )
    store.record_tool_dispatch(
        command.session_id,
        turn_seq=2,
        call_id="read",
        tool="read_file",
        arguments={"file": "artifact.txt"},
        role="director",
    )
    observed = ToolObservation("read", "read_file", True, "correct", None, False)
    store.record_tool_observation(command.session_id, turn_seq=2, observation=observed, role="director")
    with pytest.raises(ValueError, match="write"):
        store.record_write_reconciliation(
            command.session_id, turn_seq=1, call_id="unknown", role="director", readback=observed
        )
    assert store.pending_tool_calls(command.session_id)[0]["call_id"] == "unknown"


async def test_final_checks_keep_run_running_and_lock_until_physical_cancel(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.internal.engineering_session import WorkspaceBusyError
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    output = isolated_workspace / "dist"
    output.mkdir()
    scripted_provider.enqueue_json(_reply("Ready for final check."))
    service = EngineeringRunService(
        isolated_workspace,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
        writable_output_roots=(output,),
    )
    command = RoleRunCommand(
        "cancel-final",
        "director",
        "Complete artifact.",
        isolated_workspace,
        required_checks=(
            "python3 -c \"from pathlib import Path; import time; Path('dist/started').write_text('started',encoding='utf-8'); time.sleep(25)\"",
        ),
    )
    await service.start(command, scope=_scope(isolated_workspace), limits=_limits())
    try:
        async with asyncio.timeout(8):
            while not (output / "started").exists():
                await asyncio.sleep(0.01)
        assert service.observe(command.session_id, workspace=isolated_workspace)["status"] == "running"
        with pytest.raises(WorkspaceBusyError), service.store.acquire_workspace(isolated_workspace):
            pytest.fail("final check released workspace writer before cleanup")
    finally:
        await service.cancel(command.session_id, workspace=isolated_workspace)
    row = service.observe(command.session_id, workspace=isolated_workspace)
    assert row["status"] == "cancelled" and row["verification"] == "unknown", row
    assert row["checks"][0]["interrupted"] and row["checks"][0]["exit_code"] is None
    with service.store.acquire_workspace(isolated_workspace):
        pass
    assert len(scripted_provider.requests) == 1


async def test_required_checks_cannot_obtain_execute_authority_from_empty_grant(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    scripted_provider.enqueue_json(_reply("All done."))
    service = EngineeringRunService(
        isolated_workspace, client=scripted_provider.client("openai", isolated_workspace, monkeypatch)
    )
    command = RoleRunCommand(
        "no-exec-final", "director", "Complete artifact.", isolated_workspace, required_checks=("exit 0",)
    )
    empty = PermissionScope((isolated_workspace,), (), frozenset(), False)
    await service.start(command, scope=empty, limits=_limits())
    row = await _terminal(service, command.session_id, isolated_workspace)
    assert row["status"] == "interrupted" and row["verification"] == "unknown", row
    assert len(row["checks"]) == 1 and row["checks"][0]["exit_code"] is None
    assert len(scripted_provider.requests) == 1


async def test_resume_retains_completed_tool_history_after_provider_failure(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    scripted_provider.enqueue_json(_write("already-written", "correct"))
    scripted_provider.enqueue_json({"error": "synthetic failure"}, status=500)
    client = scripted_provider.client("openai", isolated_workspace, monkeypatch)
    service = EngineeringRunService(isolated_workspace, client=client)
    command = RoleRunCommand("resume-history", "director", "Complete artifact.", isolated_workspace)
    await service.start(command, scope=_scope(isolated_workspace), limits=_limits())
    failed = await _terminal(service, command.session_id, isolated_workspace)
    assert failed["status"] == "failed"
    before = (isolated_workspace / "artifact.txt").stat()
    scripted_provider.enqueue_json(_reply("Read prior observations and continue without repeating effects."))
    resumed = EngineeringRunService(
        isolated_workspace, client=client, permission_resolver=lambda row: _scope(isolated_workspace)
    )
    await resumed.resume(command.session_id, workspace=isolated_workspace)
    row = await _terminal(resumed, command.session_id, isolated_workspace)
    assert row["status"] == "completed", row
    native = scripted_provider.requests[-1]["body"]["messages"]
    assert any(message.get("tool_call_id") == "already-written" for message in native)
    assert any(
        message.get("tool_calls", [{}])[0].get("id") == "already-written"
        for message in native
        if message.get("tool_calls")
    )
    after = (isolated_workspace / "artifact.txt").stat()
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)


async def test_fresh_process_never_replays_unobserved_real_command(
    isolated_workspace: Path, scripted_provider: ScriptedProvider
) -> None:
    command_reply = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "unknown-command-effect",
                            "type": "function",
                            "function": {
                                "name": "execute_command",
                                "arguments": json.dumps({"command": "printf once >> effect-count"}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    for response in (_reply("PM valid advice."), _reply("CE valid advice."), command_reply):
        scripted_provider.enqueue_json(response)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[5]), "NO_PROXY": "*", "no_proxy": "*"}
    first = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _CHILD,
        str(isolated_workspace),
        scripted_provider.base_url,
        "crash-command",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        assert first.stdout is not None
        marker = await asyncio.wait_for(first.stdout.readline(), timeout=20)
        assert marker == b"COMMAND_EFFECT_BEFORE_OBSERVATION\n", marker
        assert (isolated_workspace / "effect-count").read_text(encoding="utf-8") == "once"
    finally:
        if first.returncode is None:
            first.kill()
        await first.communicate()
    second = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _CHILD,
        str(isolated_workspace),
        scripted_provider.base_url,
        "resume",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    out, err = await asyncio.wait_for(second.communicate(), timeout=20)
    assert second.returncode != 0 and "effect is unknown" in err.decode("utf-8"), (out, err)
    assert (isolated_workspace / "effect-count").read_text(encoding="utf-8") == "once"
    assert len(scripted_provider.requests) == 3
    from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore

    row = EngineeringSessionStore(isolated_workspace).load_run("cold-write", workspace=isolated_workspace)
    assert row["status"] == "interrupted" and row["verification"] == "unknown", row


async def test_observe_projects_stale_pass_without_rewriting_historical_evidence(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    (isolated_workspace / "artifact.txt").write_text("correct", encoding="utf-8")
    scripted_provider.enqueue_json(_reply("Ready."))
    service = EngineeringRunService(
        isolated_workspace, client=scripted_provider.client("openai", isolated_workspace, monkeypatch)
    )
    command = RoleRunCommand(
        "current-projection",
        "director",
        "Complete artifact.",
        isolated_workspace,
        required_checks=(
            "python3 -c \"from pathlib import Path; assert Path('artifact.txt').read_text(encoding='utf-8') == 'correct'\"",
        ),
    )
    await service.start(command, scope=_scope(isolated_workspace), limits=_limits())
    assert (await _terminal(service, command.session_id, isolated_workspace))["verification"] == "passed"
    historical = service.store.load_run(command.session_id, workspace=isolated_workspace)
    events = service.store.load_events(command.session_id)
    (isolated_workspace / "artifact.txt").write_text("outside edit", encoding="utf-8")
    current = service.observe(command.session_id, workspace=isolated_workspace)
    assert current["verification"] == "unknown"
    assert service.store.load_run(command.session_id, workspace=isolated_workspace) == historical
    assert service.store.load_events(command.session_id) == events
