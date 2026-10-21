"""Truthful native command contracts; synthetic model, real HTTP/sandbox/storage.

These fixtures do not establish remote-model or autonomous delivery capability.
"""

from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.engineering_loop import run_engineering_loop
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.kernelone.storage.io_paths import resolve_storage_roots
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry


def _tools(workspace: Path, *, write: bool = True, network: bool = False) -> EngineeringTools:
    (workspace / ".polaris").mkdir(exist_ok=True)
    return EngineeringTools(
        workspace,
        PermissionScope(
            (workspace,),
            (workspace / "allowed",) if write else (),
            frozenset({"execute_command", "read_file"}),
            network,
        ),
        RunLimits(None, 100_000, 16_000),
        asyncio.Event(),
    )


def _function(schema: dict[str, Any]) -> dict[str, Any]:
    return schema.get("function", schema)


def _assert_contract(function: dict[str, Any], workspace: Path, *, write: bool, network: bool) -> None:
    description = function["description"]
    # These are provider-visible execution semantics, not source-string checks.
    for fact in ("/bin/sh", str(workspace), "/tmp", "HOME", "cache", "output", "fresh", "persist"):
        assert fact in description, f"missing execution fact: {fact}"
    assert ("Network access is allowed" if network else "Network access is disabled") in description
    if write:
        assert str(workspace / "allowed") in description
    else:
        assert "No workspace writes are authorized" in description
    parameters = function.get("parameters", function.get("input_schema"))
    assert isinstance(parameters, dict)
    timeout = parameters["properties"]["timeout"]
    assert timeout["type"] == "integer"
    assert (timeout["minimum"], timeout["maximum"], timeout["default"]) == (1, 120, 30)


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("network", [False, True])
@pytest.mark.parametrize("write", [False, True])
async def test_final_native_wire_discloses_actual_command_environment_without_mutating_registry(
    protocol: str,
    network: bool,
    write: bool,
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = isolated_workspace
    (workspace / "allowed").mkdir()
    baseline = ToolSpecRegistry.get_llm_schema("execute_command")
    tools = _tools(workspace, write=write, network=network)
    store = EngineeringSessionStore(workspace)
    command = RoleRunCommand("truthful-command", "director", "Inspect the command environment.", workspace)
    store.create_run(command, scope=tools.scope, limits=tools.limits, engine_version="engineering_v2", request_key=None)
    if protocol == "anthropic":
        scripted_provider.enqueue_json(
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "wire-command",
                        "name": "run_command",
                        "input": {"cmd": "printf legal-wire-command", "timeout": "30"},
                    }
                ],
                "stop_reason": "tool_use",
            }
        )
    else:
        scripted_provider.enqueue_json(
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "wire-command",
                                    "type": "function",
                                    "function": {
                                        "name": "run_command",
                                        "arguments": '{"cmd":"printf legal-wire-command","timeout":"30"}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )
    packet = (
        {"content": [{"type": "text", "text": "Environment inspected."}], "stop_reason": "end_turn"}
        if protocol == "anthropic"
        else {"choices": [{"message": {"content": "Environment inspected."}, "finish_reason": "stop"}]}
    )
    scripted_provider.enqueue_json(packet)
    result = await run_engineering_loop(
        command,
        scope=tools.scope,
        limits=tools.limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client(protocol, workspace, monkeypatch),
    )
    assert result.status == "completed" and result.verification == "not_run"
    assert len(scripted_provider.requests) == 2
    observations = [row["payload"] for row in store.load_events(command.session_id) if row["kind"] == "tool_observed"]
    assert len(observations) == 1
    assert observations[0]["tool"] == "execute_command" and observations[0]["ok"] is True
    assert "legal-wire-command" in observations[0]["content"]
    cold_observations = [
        row["payload"]
        for row in EngineeringSessionStore(workspace).load_events(command.session_id)
        if row["kind"] == "tool_observed"
    ]
    assert cold_observations == observations
    wire = scripted_provider.requests[0]["body"]
    function = next(_function(row) for row in wire["tools"] if _function(row)["name"] == "execute_command")
    _assert_contract(function, workspace, write=write, network=network)
    assert ToolSpecRegistry.get_llm_schema("execute_command") == baseline
    # Match the actual wire to a readable persisted final request, not a projection.
    runtime = Path(resolve_storage_roots(str(workspace)).runtime_root)
    captures = [json.loads(path.read_text(encoding="utf-8")) for path in (runtime / "contexts").glob("*/*")]
    assert any(
        row.get("provider_request", {}).get("final_physical_request", {}).get("body") == wire for row in captures
    )
    cold = _tools(workspace, write=not write, network=not network)
    _assert_contract(
        next(_function(row) for row in cold.schemas() if _function(row)["name"] == "execute_command"),
        workspace,
        write=not write,
        network=not network,
    )
    assert ToolSpecRegistry.get_llm_schema("execute_command") == baseline


async def test_two_real_sandbox_calls_discard_private_state_and_keep_only_authorized_workspace_files(
    isolated_workspace: Path,
) -> None:
    workspace = isolated_workspace
    (workspace / "allowed").mkdir()
    first = _tools(workspace)
    observation = await first.execute(
        "first",
        "run_command",
        {
            "cmd": (
                "set -e; printf 'durable UTF-8: 鹈鹕' > allowed/persist.txt; "
                "printf private > /tmp/command-private; "
                'printf private > "$HOME/command-private"; '
                'printf private > "$XDG_CACHE_HOME/command-private"; '
                'printf private > "$POLARIS_SANDBOX_OUTPUT/command-private"; '
                "export COMMAND_PRIVATE=value; cd allowed; printf first-ok"
            )
        },
    )
    assert observation.ok, observation.content
    # New object uses the same authority, without inheriting any shell state.
    cold = _tools(workspace)
    second = await cold.execute(
        "second",
        "execute_command",
        {
            "command": (
                'test "$PWD" = ' + shlex.quote(str(workspace)) + " && "
                'test -z "${COMMAND_PRIVATE-}" && '
                "test ! -e /tmp/command-private && "
                'test ! -e "$HOME/command-private" && '
                'test ! -e "$XDG_CACHE_HOME/command-private" && '
                'test ! -e "$POLARIS_SANDBOX_OUTPUT/command-private" && cat allowed/persist.txt'
            )
        },
    )
    assert second.ok, second.content
    assert "durable UTF-8: 鹈鹕" in second.content
    assert (workspace / "allowed" / "persist.txt").read_text(encoding="utf-8") == "durable UTF-8: 鹈鹕"
    denied = await cold.execute("outside-write", "execute_command", {"command": "printf denied > outside.txt"})
    assert not denied.ok and not (workspace / "outside.txt").exists()


async def test_explicit_empty_writes_still_allow_commands_but_cannot_persist_workspace_changes(
    isolated_workspace: Path,
) -> None:
    tools = _tools(isolated_workspace, write=False)
    allowed = await tools.execute("read-only", "run_command", {"cmd": "printf legal-command"})
    assert allowed.ok and "legal-command" in allowed.content
    denied = await tools.execute("no-write", "execute_command", {"command": "printf denied > forbidden.txt"})
    assert not denied.ok
    assert not (isolated_workspace / "forbidden.txt").exists()


@pytest.mark.parametrize("timeout", [0, 121, -1, True, "invalid"])
async def test_out_of_contract_timeout_is_rejected_without_command_effect(
    isolated_workspace: Path, timeout: Any
) -> None:
    (isolated_workspace / "allowed").mkdir()
    tools = _tools(isolated_workspace)
    rejected = await tools.execute(
        "invalid-timeout", "execute_command", {"command": "touch allowed/unexpected", "timeout": timeout}
    )
    assert not rejected.ok and "integer in [1, 120]" in rejected.content
    assert not (isolated_workspace / "allowed" / "unexpected").exists()


@pytest.mark.parametrize("arguments", [{"timeout": 1}, {"timeout": 120}, {"timeout": "30"}, {}])
async def test_documented_timeout_boundaries_and_default_execute_legally(
    isolated_workspace: Path, arguments: dict[str, Any]
) -> None:
    tools = _tools(isolated_workspace, write=False)
    observed = await tools.execute("valid-timeout", "execute_command", {"command": "printf bounded", **arguments})
    assert observed.ok and observed.exit_code == 0 and "bounded" in observed.content


def test_provider_timeout_schema_describes_the_enforced_default_and_limits(isolated_workspace: Path) -> None:
    tools = _tools(isolated_workspace, write=False)
    function = next(_function(row) for row in tools.schemas() if _function(row)["name"] == "execute_command")
    timeout = function["parameters"]["properties"]["timeout"]
    assert (timeout.get("minimum"), timeout.get("maximum"), timeout.get("default")) == (1, 120, 30)
