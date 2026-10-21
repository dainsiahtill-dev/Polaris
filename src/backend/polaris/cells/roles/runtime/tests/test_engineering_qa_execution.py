"""Independent checks use real native HTTP, K1 containment and current-candidate facts.

Only the model/configuration port is scripted. These are synthetic component
experiments, not proof of remote-model judgment or unattended delivery.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.engineering_loop import run_engineering_loop
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)


def _calls(*values: tuple[str, str, dict[str, Any]], text: str = "") -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": text,
                    "tool_calls": [
                        {
                            "id": identity,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                        for identity, name, arguments in values
                    ],
                },
                "finish_reason": "tool_calls" if values else "stop",
            }
        ]
    }


def _python(code: str) -> str:
    return shlex.join(["/usr/bin/python3", "-I", "-c", code])


def _scope(workspace: Path) -> PermissionScope:
    return PermissionScope(
        (workspace,),
        (workspace,),
        frozenset({"read_file", "write_file", "execute_command", "verify_project", "report_review"}),
        True,
    )


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_collaborative_qa_runs_independent_checks_without_changing_verified_candidate(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch, protocol: str
) -> None:
    """Breaks if QA loses execute, gains writes/network, or invalidates its own review."""
    workspace = isolated_workspace
    source = workspace / "values.json"
    source.write_text("[3,5,8]\n", encoding="utf-8")
    scope = _scope(workspace)
    client = scripted_provider.client(protocol, workspace, monkeypatch)
    service = EngineeringRunService(workspace, client=client, permission_resolver=lambda _row: scope, require_qa=True)
    private_check = _python(
        "import json; from pathlib import Path; "
        "p=Path('/tmp/independent-check.py'); "
        "p.write_text('result=sum(values)\\n',encoding='utf-8'); "
        "s={'values':json.loads(Path('values.json').read_text(encoding='utf-8'))}; "
        "exec(compile(p.read_text(encoding='utf-8'),str(p),'exec'),s); print(s['result'])"
    )
    forbidden_write = _python("from pathlib import Path; Path('values.json').write_text('corrupted',encoding='utf-8')")
    for packet in [
        _calls(text="PM: preserve the input and check its sum."),
        _calls(text="CE: use a local independent calculation."),
        _calls(text="Director: the existing candidate is ready for required verification."),
        _calls(("qa-source", "read_file", {"file": "values.json"})),
        _calls(("qa-independent", "run_command", {"cmd": private_check, "timeout": 10})),
        _calls(("qa-write-attempt", "execute_command", {"command": forbidden_write, "timeout": 10})),
        _calls(
            ("qa-review", "report_review", {"approved": True, "feedback": "Computed 16; candidate write was refused."})
        ),
    ]:
        if protocol == "anthropic":
            message = packet["choices"][0]["message"]
            content = [
                {
                    "type": "tool_use",
                    "id": call["id"],
                    "name": call["function"]["name"],
                    "input": json.loads(call["function"]["arguments"]),
                }
                for call in message["tool_calls"]
            ]
            packet = {
                "content": content or [{"type": "text", "text": message["content"]}],
                "stop_reason": "tool_use" if content else "end_turn",
            }
        scripted_provider.enqueue_json(packet)
    check = _python(
        "import json; from pathlib import Path; assert json.loads(Path('values.json').read_text(encoding='utf-8'))==[3,5,8]"
    )
    command = RoleRunCommand(
        "qa-compute",
        "director",
        "Check the existing values.",
        workspace,
        workflow="collaborative",
        required_checks=(check,),
    )
    await service.start(command, scope=scope, limits=RunLimits(time.time() + 45, 1_000_000, 64000))
    await service.wait(command.session_id, workspace=workspace)
    events = service.store.load_events(command.session_id)
    observed = {event["payload"]["call_id"]: event["payload"] for event in events if event["kind"] == "tool_observed"}
    assert observed["qa-independent"]["ok"] is True, observed["qa-independent"]
    assert observed["qa-independent"]["exit_code"] == 0
    assert "stdout:\n16\n" in observed["qa-independent"]["content"]
    assert observed["qa-write-attempt"]["ok"] is False
    assert observed["qa-write-attempt"]["exit_code"] != 0
    assert "Read-only file system" in observed["qa-write-attempt"]["content"]
    assert source.read_text(encoding="utf-8") == "[3,5,8]\n"
    assert not (workspace / "independent-check.py").exists()
    row = service.snapshot(command.session_id, workspace=workspace)
    assert row["project_outcome"] == "accepted", row
    assert row["workspace_revision"] == 0
    assert row["verification"] == "passed" and row["qa_verdict"] == "approved"
    assert len(row["checks"]) == 1 and row["required_checks"] == [check]
    qa_wire = scripted_provider.requests[3]["body"]
    functions = [tool.get("function", tool) for tool in qa_wire["tools"]]
    execute = next(tool for tool in functions if tool["name"] == "execute_command")
    assert "No workspace writes are authorized" in execute["description"]
    assert "Network access is disabled" in execute["description"]
    assert all(tool["name"] != "write_file" for tool in functions)
    assert [event["role"] for event in events if event["kind"] == "role_started"] == [
        "pm",
        "chief_engineer",
        "director",
        "qa",
    ]
    assert not service.store.pending_tool_calls(command.session_id)
    cold = EngineeringRunService(workspace, permission_resolver=lambda _row: scope, require_qa=True)
    assert cold.snapshot(command.session_id, workspace=workspace)["project_outcome"] == "accepted"


@pytest.mark.parametrize(
    "network,expected_revision,expected_verification", [(False, 0, "passed"), (True, 1, "not_run")]
)
async def test_only_physically_readonly_offline_commands_preserve_current_verification(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    network: bool,
    expected_revision: int,
    expected_verification: str,
) -> None:
    """Breaks if command text/role replaces the effective sandbox authority test."""
    workspace = isolated_workspace
    (workspace / "input.txt").write_text("candidate", encoding="utf-8")
    scope = PermissionScope((workspace,), (), frozenset({"execute_command", "read_file"}), network)
    limits = RunLimits(time.time() + 30, 1_000_000, 64000)
    check = _python("from pathlib import Path; assert Path('input.txt').read_text(encoding='utf-8')=='candidate'")
    command = RoleRunCommand(
        "readonly-exec", "director", "Inspect the prepared candidate.", workspace, required_checks=(check,)
    )
    store = EngineeringSessionStore(workspace)
    store.create_run(command, scope=scope, limits=limits, engine_version="engineering_v2", request_key=None)
    verifier = EngineeringVerification(workspace, store=store, scope=scope, limits=limits, cancel=asyncio.Event())
    assert (await verifier.run_verification((check,), session_id=command.session_id, workspace_revision=0))[0].ok
    before = verifier.review_binding(command.session_id)
    scripted_provider.enqueue_json(_calls(("inspect", "execute_command", {"command": _python("print(16)")})))
    scripted_provider.enqueue_json(_calls(text="Observed the calculation."))
    result = await run_engineering_loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client("openai", workspace, monkeypatch),
        project_run=False,
    )
    assert result.status == "completed"
    actual = next(
        event["payload"]
        for event in store.load_events(command.session_id)
        if event["kind"] == "tool_observed" and event["payload"].get("call_id") == "inspect"
    )
    assert actual["ok"] and actual["exit_code"] == 0
    row = store.load_run(command.session_id, workspace=workspace)
    assert row["workspace_revision"] == expected_revision
    assert row["verification"] == expected_verification
    if not network:
        assert verifier.review_binding(command.session_id) == before
        assert verifier.current_verification(command.session_id) == "passed"


async def test_current_host_revocation_blocks_effect_after_model_response(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Breaks if tools retain saved authority after the current host revokes it."""
    workspace = isolated_workspace
    scope = _scope(workspace)
    client = scripted_provider.client("openai", workspace, monkeypatch)
    revoked = replace(scope, enabled_tools=frozenset({"read_file"}))
    service = EngineeringRunService(
        workspace, client=client, permission_resolver=lambda _row: revoked if scripted_provider.requests else scope
    )
    scripted_provider.enqueue_json(
        _calls(
            (
                "revoked-command",
                "execute_command",
                {
                    "command": _python(
                        "from pathlib import Path; Path('forbidden-effect.txt').write_text('effect',encoding='utf-8')"
                    )
                },
            )
        )
    )
    scripted_provider.enqueue_json(_calls(text="Host permission is no longer available."))
    command = RoleRunCommand(
        "revoked-before-effect",
        "director",
        "Use only current authorization.",
        workspace,
        required_checks=(_python("assert True"),),
    )
    await service.run_role(
        command, scope=scope, limits=RunLimits(time.time() + 30, 1_000_000, 64000), cancel=asyncio.Event()
    )
    assert not (workspace / "forbidden-effect.txt").exists()
    observed = next(
        event["payload"]
        for event in service.store.load_events(command.session_id)
        if event["kind"] == "tool_observed" and event["payload"].get("call_id") == "revoked-command"
    )
    assert observed["ok"] is False and observed["exit_code"] is None
    assert "permission policy" in observed["content"]
    assert not service.store.pending_tool_calls(command.session_id)


async def test_revocation_after_first_final_check_blocks_second_check(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Breaks if implicit verification bypasses the owner's current permission policy."""
    workspace = isolated_workspace
    scope = _scope(workspace)
    client = scripted_provider.client("openai", workspace, monkeypatch)

    def policy(row: dict[str, Any]) -> PermissionScope:
        observed = [event for event in service.store.load_events(row["session_id"]) if event["kind"] == "tool_observed"]
        return replace(scope, enabled_tools=frozenset({"read_file"})) if observed else scope

    service = EngineeringRunService(workspace, client=client, permission_resolver=policy)
    scripted_provider.enqueue_json(_calls(text="The candidate is ready for the required checks."))
    command = RoleRunCommand(
        "revoked-between-checks",
        "director",
        "Run both required checks under current authority.",
        workspace,
        required_checks=(_python("print('first-real-check')"), _python("print('second-must-not-run')")),
    )
    await service.start(command, scope=scope, limits=RunLimits(time.time() + 30, 1_000_000, 64000))
    await service.wait(command.session_id, workspace=workspace)
    checks = [
        event["payload"] for event in service.store.load_events(command.session_id) if event["kind"] == "tool_observed"
    ]
    assert len(checks) == 2
    assert checks[0]["ok"] and checks[0]["exit_code"] == 0
    assert "first-real-check" in checks[0]["content"]
    assert checks[1]["ok"] is False and checks[1]["exit_code"] is None
    assert "permission policy" in checks[1]["content"]
    assert "stdout:\nsecond-must-not-run" not in checks[1]["content"]
    assert service.snapshot(command.session_id, workspace=workspace)["project_outcome"] != "accepted"
