"""Scripted model, real QA command and journal failure across fresh processes.

Removing QA execution authority must lose the physical command witness. Replaying
a pending command during recovery must produce another observed process entry.
Only the journal observation port fails; the process wrapper always delegates.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_qa import _calls, _text

_COMMAND = (
    'python3 -c "from pathlib import Path; '
    "print('QA_COMPUTED_' + str(int(Path('value.txt').read_text(encoding='utf-8')) ** 2))\""
)

_CHILD = r"""
import asyncio, json, sys, time
from pathlib import Path
import pytest
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import ScriptedProvider

workspace, base_url, phase = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
scope = PermissionScope((workspace,), (workspace,), frozenset({'read_file', 'execute_command', 'report_review'}), False)
patch = pytest.MonkeyPatch()
ScriptedProvider(base_url=base_url).client('openai', workspace, patch)
service = EngineeringRunService(workspace, require_qa=True, permission_resolver=lambda row: scope)
process_entries = []
real_process = EngineeringTools._process
async def observe_process(self, argv, *, timeout, writes, command_cache_write=False):
    process_entries.append({'argv': argv, 'writes': [str(path) for path in writes]})
    return await real_process(self, argv, timeout=timeout, writes=writes, command_cache_write=command_cache_write)
EngineeringTools._process = observe_process

async def main():
    if phase == 'first':
        original = service.store.record_tool_observation
        def fail_qa_observation(session_id, *, turn_seq, observation, role):
            if role == 'qa' and observation.call_id == 'qa-uncommitted-command' and observation.ok and observation.exit_code == 0:
                print(json.dumps({'phase': 'physical_command_returned', 'call_id': observation.call_id,
                                  'exit_code': observation.exit_code, 'content': observation.content}), flush=True)
                raise OSError('injected QA observation persistence failure after physical exit')
            return original(session_id, turn_seq=turn_seq, observation=observation, role=role)
        service.store.record_tool_observation = fail_qa_observation
        command = RoleRunCommand('qa-command-unknown', 'director', 'Independently check the square of value7.', workspace,
            required_checks=("python3 -c \"from pathlib import Path; assert Path('value.txt').read_text(encoding='utf-8') == '7'\"",))
        await service.start(command, scope=scope, limits=RunLimits(time.time()+45, 1000000, 64000))
        try:
            await service.wait(command.session_id, workspace=workspace)
        except OSError as error:
            print(json.dumps({'phase': 'persistence_error', 'error': str(error)}), flush=True)
    else:
        try:
            await service.resume('qa-command-unknown', workspace=workspace)
            await service.wait('qa-command-unknown', workspace=workspace)
        except RuntimeError as error:
            print(json.dumps({'phase': 'resume_error', 'error': str(error)}), flush=True)
    row = service.snapshot('qa-command-unknown', workspace=workspace)
    print(json.dumps({'phase': 'projection', **{key: row[key] for key in
        ('status', 'verification', 'qa_verdict', 'project_outcome')}}), flush=True)
    print(json.dumps({'phase': 'process_entries', 'entries': process_entries}), flush=True)

asyncio.run(main())
"""


def _records(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line.startswith('{"phase":')]


@pytest.mark.asyncio
async def test_fresh_process_pending_qa_readonly_command_is_unknown_and_never_replayed(
    isolated_workspace: Path, scripted_provider: ScriptedProvider
) -> None:
    artifact = isolated_workspace / "value.txt"
    artifact.write_text("7", encoding="utf-8")
    original_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
    for response in (
        _text("Director finished; the required check still needs actual execution."),
        _calls(("qa-read", "read_file", {"file": "value.txt"})),
        _calls(("qa-uncommitted-command", "execute_command", {"command": _COMMAND})),
        _text("No review decision without a completed observation."),
    ):
        scripted_provider.enqueue_json(response)

    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[5]), "PYTHONUTF8": "1"}

    async def child(phase: str) -> subprocess.CompletedProcess[str]:
        return await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", _CHILD, str(isolated_workspace), scripted_provider.base_url, phase],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=25,
        )

    first = await child("first")
    assert first.returncode == 0, first.stderr
    records = _records(first.stdout)
    witnessed = [row for row in records if row["phase"] == "physical_command_returned"]
    assert witnessed, {
        "failure": "QA command did not physically exit successfully before the injected journal fault",
        "real_process_entries": records,
        "actual_request_tool_sets": [
            [tool["function"]["name"] for tool in request["body"].get("tools", [])]
            for request in scripted_provider.requests
        ],
    }
    assert witnessed[0]["exit_code"] == 0 and "QA_COMPUTED_49" in witnessed[0]["content"]
    entries = next(row["entries"] for row in records if row["phase"] == "process_entries")
    qa_entries = [row for row in entries if _COMMAND in row["argv"]]
    assert len(qa_entries) == 1 and qa_entries[0]["writes"] == []

    store = EngineeringSessionStore(isolated_workspace)
    before_events = store.load_events("qa-command-unknown")
    pending = store.pending_tool_calls("qa-command-unknown")
    assert len(pending) == 1 and pending[0]["call_id"] == "qa-uncommitted-command"
    assert not any(
        event["kind"] == "tool_observed" and event["payload"]["call_id"] == "qa-uncommitted-command"
        for event in before_events
    )
    assert any(event["kind"] == "verification_completed" for event in before_events)
    request_count = len(scripted_provider.requests)
    assert request_count == 3

    second = await child("resume")
    assert second.returncode == 0, second.stderr
    recovered = _records(second.stdout)
    errors = [row["error"] for row in recovered if row["phase"] == "resume_error"]
    assert errors and "unknown" in errors[0].lower()
    assert next(row["entries"] for row in recovered if row["phase"] == "process_entries") == []
    assert len(scripted_provider.requests) == request_count
    assert store.pending_tool_calls("qa-command-unknown") == pending
    after_events = store.load_events("qa-command-unknown")
    assert sum(event["kind"] == "tool_started" for event in after_events) == sum(
        event["kind"] == "tool_started" for event in before_events
    )
    review_events = [event for event in after_events if event["kind"] == "qa_review_completed"]
    assert review_events and all(event["payload"]["verdict"] == "unknown" for event in review_events)
    assert all(event["payload"]["report_call_id"] is None for event in review_events)
    row = store.load_run("qa-command-unknown", workspace=isolated_workspace)
    assert row["status"] == "interrupted" and row["verification"] == "unknown"
    projection = next(row for row in recovered if row["phase"] == "projection")
    assert projection["qa_verdict"] == "unknown" and projection["project_outcome"] != "accepted"
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == original_hash
    print(
        json.dumps(
            {
                "physical_qa_command": witnessed[0],
                "qa_process_write_roots": qa_entries[0]["writes"],
                "cold_projection": projection,
                "pending_command_ids": [call["call_id"] for call in pending],
                "cold_process_entries": [],
                "provider_requests_before_and_after_resume": request_count,
                "qa_review_verdicts": [event["payload"]["verdict"] for event in review_events],
                "candidate_unchanged": True,
            },
            ensure_ascii=False,
        )
    )
