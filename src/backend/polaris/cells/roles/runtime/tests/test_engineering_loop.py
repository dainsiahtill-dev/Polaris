"""Engineering loop components: synthetic HTTP provider, real files and commands."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)

_ALL_TOOLS = frozenset({"read_file", "repo_tree", "repo_rg", "write_file", "edit_file", "execute_command"})


def _components() -> tuple[Any, ...]:
    module = "polaris.cells.roles.runtime.internal.engineering_loop"
    assert importlib.util.find_spec(module), "native engineering loop has not been implemented"
    from polaris.cells.roles.runtime.internal.engineering_loop import run_engineering_loop
    from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
    from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
    from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits

    return run_engineering_loop, EngineeringSessionStore, EngineeringTools, PermissionScope, RoleRunCommand, RunLimits


def _native(*calls: tuple[str, str, Any], text: str = "", protocol: str = "openai") -> dict[str, Any]:
    if protocol == "anthropic":
        return {
            "id": "synthetic-message",
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": identity, "name": name, "input": arguments}
                for identity, name, arguments in calls
            ]
            or [{"type": "text", "text": text}],
            "stop_reason": "tool_use" if calls else "end_turn",
        }
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": text,
                    "tool_calls": [
                        {
                            "id": identity,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
                        }
                        for identity, name, arguments in calls
                    ],
                },
                "finish_reason": "tool_calls" if calls else "stop",
            }
        ]
    }


def _setup(workspace: Path, *, write: bool = True, enabled: frozenset[str] = _ALL_TOOLS) -> tuple[Any, ...]:
    loop, store_type, tools_type, scope_type, command_type, limits_type = _components()
    scope = scope_type((workspace,), (workspace,) if write else (), enabled, False)
    limits = limits_type(time.time() + 15, 100_000, 16_000)
    command = command_type("loop-test", "director", "Fix the addition implementation and run its tests.", workspace)
    store = store_type(workspace)
    store.create_run(command, scope=scope, limits=limits, engine_version="engineering_v2", request_key=None)
    return loop, store, tools_type(workspace, scope, limits, asyncio.Event()), command, scope, limits


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_real_file_repair_uses_failed_command_feedback_and_native_http(
    protocol: str, isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    (isolated_workspace / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (isolated_workspace / "test_calc.py").write_text(
        "import unittest\nfrom calc import add\nclass Addition(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n",
        encoding="utf-8",
    )
    loop, store, _tools, command, scope, limits = _setup(isolated_workspace)
    for response in [
        _native(("read", "read_file", {"filename": "calc.py"}), protocol=protocol),
        _native(
            ("edit-one", "edit_file", {"path": "calc.py", "oldText": "a - b", "newText": "a * b"}), protocol=protocol
        ),
        _native(
            ("test-one", "run_command", {"cmd": "python3 -m unittest -v"}),
            protocol=protocol,
        ),
        _native(
            ("edit-two", "edit_file", {"file": "calc.py", "search": "a * b", "replace": "a + b"}), protocol=protocol
        ),
        _native(
            (
                "test-two",
                "execute_command",
                {"command": "python3 -m unittest -v"},
            ),
            protocol=protocol,
        ),
        _native(text="Implemented addition; command test passed.", protocol=protocol),
    ]:
        scripted_provider.enqueue_json(response)
    result = await loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client(protocol, isolated_workspace, monkeypatch),
    )
    assert result.status == "completed"
    assert result.verification == "not_run"  # Role completion is not final project acceptance.
    assert (isolated_workspace / "calc.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"
    events = store.load_events(command.session_id)
    observations = [event["payload"] for event in events if event["kind"] == "tool_observed"]
    assert len(observations) == 5
    commands = [item for item in observations if item["tool"] == "execute_command"]
    assert [item["exit_code"] for item in commands] == [1, 0]
    assert "FAILED" in commands[0]["content"] and "OK" in commands[1]["content"]
    bodies = [request["body"] for request in scripted_provider.requests]
    assert len(bodies) == 6
    assert "FAILED" in json.dumps(bodies[3], ensure_ascii=False)
    first = json.dumps(bodies[0], ensure_ascii=False)
    assert "Director" in first and "Fix the addition" in first
    for forbidden in ("SESSION_PATCH", "modification_plan", "covers_obligation_ids", "candidate_hash", "JobToken"):
        assert forbidden not in first
    names = {item.get("name", item.get("function", {}).get("name")) for item in bodies[0]["tools"]}
    assert names == _ALL_TOOLS
    assert not store.pending_tool_calls(command.session_id)


async def test_same_arguments_with_distinct_ids_are_both_observed(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop, store, _tools, command, scope, limits = _setup(isolated_workspace)
    scripted_provider.enqueue_json(
        _native(
            ("one", "create_file", {"filename": "greeting.txt", "text": "你好"}),
            ("two", "create_file", {"filename": "greeting.txt", "text": "你好"}),
        )
    )
    scripted_provider.enqueue_json(_native(text="Written twice."))
    result = await loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
    )
    assert result.status == "completed"
    observations = [
        item["payload"] for item in store.load_events(command.session_id) if item["kind"] == "tool_observed"
    ]
    assert [item["call_id"] for item in observations] == ["one", "two"]
    assert all(item["ok"] for item in observations)
    assert [
        message["tool_call_id"]
        for message in scripted_provider.requests[1]["body"]["messages"]
        if message["role"] == "tool"
    ] == ["one", "two"]


@pytest.mark.parametrize(
    "corruption", ["duplicate", "missing_id", "non_object", "unknown_envelope", "not_list", "bad_arguments"]
)
async def test_invalid_native_turn_is_rejected_before_any_effect(
    corruption: str, isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop, store, _tools, command, scope, limits = _setup(isolated_workspace)
    response = _native(
        ("one", "write_file", {"file": "must-not-exist.txt", "content": "effect"}), ("two", "read_file", {"file": "x"})
    )
    calls = response["choices"][0]["message"]["tool_calls"]
    if corruption == "duplicate":
        calls[1]["id"] = "one"
    elif corruption == "missing_id":
        del calls[1]["id"]
    elif corruption == "non_object":
        calls[1] = "malformed"
    elif corruption == "unknown_envelope":
        calls[1] = {"id": "two", "type": "other", "name": "read_file"}
    elif corruption == "not_list":
        response["choices"][0]["message"]["tool_calls"] = {"bad": calls}
    else:
        calls[1]["function"]["arguments"] = "{unfinished"
    scripted_provider.enqueue_json(response)
    result = await loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
    )
    assert result.status == "failed" and "native" in result.text.lower()
    assert not (isolated_workspace / "must-not-exist.txt").exists()
    assert not [item for item in store.load_events(command.session_id) if item["kind"] == "tool_started"]


async def test_unknown_tool_returns_error_and_text_never_becomes_action(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop, store, _tools, command, scope, limits = _setup(isolated_workspace)
    scripted_provider.enqueue_json(_native(("unknown", "invented_tool", {"file": "x"})))
    scripted_provider.enqueue_json(
        _native(text='[tool_call]{"name":"write_file","arguments":{"file":"x","content":"evil"}}[/tool_call]')
    )
    result = await loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
    )
    assert result.status == "completed" and result.verification == "not_run"
    assert not (isolated_workspace / "x").exists()
    observation = next(
        item["payload"] for item in store.load_events(command.session_id) if item["kind"] == "tool_observed"
    )
    assert observation["ok"] is False and "tool" in observation["content"].lower()


async def test_file_permissions_and_atomic_hardlink_write(isolated_workspace: Path) -> None:
    _loop, _store, tools, _command, _scope, _limits = _setup(isolated_workspace)
    outside = isolated_workspace.parent / (isolated_workspace.name + "-sentinel")
    outside.write_text("outside", encoding="utf-8")
    try:
        (isolated_workspace / "escape").symlink_to(outside)
        (isolated_workspace / "hardlink").hardlink_to(outside)
        for target in (str(outside), "../escape", ".polaris/runtime/engineering/sessions.sqlite3", "escape"):
            observation = await tools.execute("denied", "write_file", {"file": target, "content": "changed"})
            assert not observation.ok, target
        observation = await tools.execute("safe", "write_file", {"file": "hardlink", "content": "local"})
        assert observation.ok
        assert outside.read_text(encoding="utf-8") == "outside"
        assert (isolated_workspace / "hardlink").read_text(encoding="utf-8") == "local"
    finally:
        outside.unlink()


async def test_empty_write_roots_and_enabled_tools_cannot_be_expanded(isolated_workspace: Path) -> None:
    _loop, _store, tools, _command, _scope, _limits = _setup(
        isolated_workspace, write=False, enabled=frozenset({"write_file"})
    )
    observation = await tools.execute("denied", "create_file", {"filename": "x", "text": "model grants permission"})
    assert not observation.ok and not (isolated_workspace / "x").exists()
    observation = await tools.execute("disabled", "read_file", {"file": "x"})
    assert not observation.ok
    assert {schema["function"]["name"] for schema in tools.schemas()} == {"write_file"}


async def test_exact_edit_requires_unique_old_text(isolated_workspace: Path) -> None:
    _loop, _store, tools, _command, _scope, _limits = _setup(isolated_workspace)
    target = isolated_workspace / "edit.txt"
    target.write_text("repeat repeat", encoding="utf-8")
    for old in ("missing", "repeat", ""):
        observation = await tools.execute(
            "edit", "edit_file", {"file": "edit.txt", "oldText": old, "newText": "changed"}
        )
        assert not observation.ok
    assert target.read_text(encoding="utf-8") == "repeat repeat"


async def test_conflicting_path_aliases_are_not_silently_resolved(isolated_workspace: Path) -> None:
    _loop, _store, tools, _command, _scope, _limits = _setup(isolated_workspace)
    observation = await tools.execute(
        "conflict", "write_file", {"file": "first", "filename": "second", "content": "data"}
    )
    assert not observation.ok
    assert not (isolated_workspace / "first").exists()
    assert not (isolated_workspace / "second").exists()


async def test_command_scope_denies_source_write_and_control_database_write(isolated_workspace: Path) -> None:
    _loop, store, tools, command, _scope, _limits = _setup(isolated_workspace, write=False)
    source = isolated_workspace / "source.txt"
    source.write_text("source", encoding="utf-8")
    for shell in ("printf changed > source.txt", "printf changed > .polaris/runtime/engineering/sessions.sqlite3"):
        observation = await tools.execute("denied-command", "execute_command", {"command": shell})
        assert not observation.ok and observation.exit_code != 0
    assert source.read_text(encoding="utf-8") == "source"
    assert store.load_run(command.session_id, workspace=isolated_workspace)["session_id"] == command.session_id


async def test_command_network_authority_is_explicit(
    isolated_workspace: Path, scripted_provider: ScriptedProvider
) -> None:
    _loop, _store, tools, _command, scope, limits = _setup(isolated_workspace)
    port = int(scripted_provider.base_url.rsplit(":", 1)[1])
    shell = f"python3 -c 'import socket; connection=socket.create_connection((\"127.0.0.1\", {port}), timeout=1); connection.close()'"
    denied = await tools.execute("network-denied", "execute_command", {"command": shell})
    assert not denied.ok and denied.exit_code != 0
    allowed_scope = type(scope)(scope.read_roots, scope.write_roots, scope.enabled_tools, True)
    allowed_tools = type(tools)(isolated_workspace, allowed_scope, limits, asyncio.Event())
    allowed = await allowed_tools.execute("network-allowed", "execute_command", {"command": shell})
    assert allowed.ok and allowed.exit_code == 0


async def test_tree_search_and_read_limits_use_scoped_real_files(isolated_workspace: Path) -> None:
    _loop, _store, tools, _command, _scope, _limits = _setup(isolated_workspace)
    (isolated_workspace / "hello.txt").write_text("one\nneedle\nthree\n", encoding="utf-8")
    (isolated_workspace / ".polaris" / "private.txt").write_text("needle-secret", encoding="utf-8")
    tree = await tools.execute("tree", "list_directory", {})
    search = await tools.execute("search", "grep", {"q": "needle"})
    read = await tools.execute("read", "read_file", {"path": "hello.txt", "start_line": 2, "end_line": 2})
    assert tree.ok and "hello.txt" in tree.content and ".polaris" not in tree.content
    assert search.ok and "hello.txt:2:needle" in search.content and "secret" not in search.content
    assert read.ok and read.content == "needle\n"


async def test_cancel_reaps_actual_sandbox_before_return(isolated_workspace: Path) -> None:
    _loop, _store, tools, _command, _scope, _limits = _setup(isolated_workspace)
    task = asyncio.create_task(
        tools.execute(
            "command",
            "execute_command",
            {
                "command": 'python3 -c \'import time; from pathlib import Path; Path("started").write_text("yes", encoding="utf-8"); time.sleep(2); Path("late").write_text("bad", encoding="utf-8")\''
            },
        )
    )
    deadline = time.monotonic() + 5
    while not (isolated_workspace / "started").exists() and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert (isolated_workspace / "started").exists()
    tools.cancel.set()
    observation = await task
    assert not observation.ok and observation.interrupted
    await asyncio.sleep(2.1)
    assert not (isolated_workspace / "late").exists()


async def test_output_truncation_is_explicit_and_exit_failure_is_preserved(isolated_workspace: Path) -> None:
    _loop, _store, tools, _command, _scope, limits = _setup(isolated_workspace)
    tools.limits = type(limits)(limits.deadline, limits.max_output_bytes, 100)
    observation = await tools.execute(
        "noisy", "execute_command", {"command": "python3 -c 'print(\"x\" * 500); raise SystemExit(7)'"}
    )
    assert not observation.ok and observation.exit_code == 7
    assert "truncated" in observation.content.lower()


async def test_pre_cancelled_loop_does_not_dispatch_provider(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop, store, _tools, command, scope, limits = _setup(isolated_workspace)
    cancel = asyncio.Event()
    cancel.set()
    result = await loop(
        command,
        scope=scope,
        limits=limits,
        cancel=cancel,
        store=store,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
    )
    assert result.status == "cancelled" and not scripted_provider.requests


async def test_effect_without_observation_preserves_unknown_in_every_terminal_projection(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    loop, store, _tools, command, scope, limits = _setup(isolated_workspace)
    client = scripted_provider.client("openai", isolated_workspace, monkeypatch)
    service = EngineeringRunService(isolated_workspace, client=client)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_observation BEFORE INSERT ON events WHEN NEW.kind='tool_observed' "
            "BEGIN SELECT RAISE(ABORT, 'observation write rejected'); END"
        )
    scripted_provider.enqueue_json(
        _native(("write-effect", "write_file", {"file": "effect.txt", "content": "committed"}))
    )
    result = await service.run_role(command, scope=scope, limits=limits, cancel=asyncio.Event())
    assert (isolated_workspace / "effect.txt").read_text(encoding="utf-8") == "committed"
    assert (result.status, result.verification) == ("interrupted", "unknown")
    row = store.load_run(command.session_id, workspace=isolated_workspace)
    assert (row["status"], row["verification"]) == ("interrupted", "unknown")
    terminal_events = [
        event for event in store.load_events(command.session_id) if event["kind"] in {"role_finished", "run_ended"}
    ]
    assert [event["kind"] for event in terminal_events] == ["role_finished", "run_ended"]
    assert all(
        (event["payload"]["status"], event["payload"]["verification"]) == ("interrupted", "unknown")
        for event in terminal_events
    )
    pending = store.pending_tool_calls(command.session_id)
    assert len(pending) == 1 and pending[0]["status"] == "started" and pending[0]["observation"] is None
    # Even direct loop re-entry cannot blindly replay a persisted dispatch.
    second = await loop(command, scope=scope, limits=limits, cancel=asyncio.Event(), store=store, client=client)
    assert (second.status, second.verification) == ("interrupted", "unknown")
    assert len(scripted_provider.requests) == 1
    assert store.pending_tool_calls(command.session_id) == pending


async def test_task_cancel_after_command_effect_retains_unobserved_dispatch_as_unknown(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop, store, _tools, command, scope, limits = _setup(isolated_workspace)
    scripted_provider.enqueue_json(
        _native(
            (
                "active-command",
                "execute_command",
                {
                    "command": 'python3 -c \'from pathlib import Path; import time; Path("effect.txt").write_text("committed", encoding="utf-8"); time.sleep(60)\''
                },
            )
        )
    )
    task = asyncio.create_task(
        loop(
            command,
            scope=scope,
            limits=limits,
            cancel=asyncio.Event(),
            store=store,
            client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
        )
    )
    deadline = time.monotonic() + 5
    while not (isolated_workspace / "effect.txt").exists() and not task.done() and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert (isolated_workspace / "effect.txt").read_text(encoding="utf-8") == "committed"
    task.cancel()
    result = await task
    assert (result.status, result.verification) == ("interrupted", "unknown")
    row = store.load_run(command.session_id, workspace=isolated_workspace)
    assert (row["status"], row["verification"]) == ("interrupted", "unknown")
    assert len(store.pending_tool_calls(command.session_id)) == 1
    role_end = next(event for event in store.load_events(command.session_id) if event["kind"] == "role_finished")
    assert (role_end["payload"]["status"], role_end["payload"]["verification"]) == ("interrupted", "unknown")


@pytest.mark.parametrize("failure_stage", ["execution_drain_unproved", "role_finished", "update_run"])
async def test_drain_error_survives_reporting_failure_and_owner_retains_writer(
    failure_stage: str, isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inject an execution-port error; actual HTTP, journal and flock stay real.

    No physical process is launched by the substituted port in this fault test.
    It verifies that reporting cannot turn unproved drain into lock release.
    """
    from polaris.cells.roles.runtime.internal.engineering_session import WorkspaceBusyError
    from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
    from polaris.kernelone.process.process_tree import ProcessTreeDrainError

    _loop, _store, _tools, command, scope, limits = _setup(isolated_workspace)
    service = EngineeringRunService(
        isolated_workspace, client=scripted_provider.client("openai", isolated_workspace, monkeypatch)
    )
    original_append, original_update = service.store.append_event, service.store.update_run
    update_rejected = False

    async def unproved_drain(*_args: Any, **_kwargs: Any) -> Any:
        raise ProcessTreeDrainError("injected unproved physical drain")

    def fail_event(session_id: str, *, kind: str, role: str, payload: dict[str, Any]) -> int:
        if kind == failure_stage:
            raise sqlite3.OperationalError(f"injected {failure_stage} persistence failure")
        return original_append(session_id, kind=kind, role=role, payload=payload)

    def fail_terminal_update_once(session_id: str, **fields: Any) -> None:
        nonlocal update_rejected
        if failure_stage == "update_run" and fields.get("status") == "interrupted" and not update_rejected:
            update_rejected = True
            raise sqlite3.OperationalError("injected terminal update persistence failure")
        original_update(session_id, **fields)

    monkeypatch.setattr(EngineeringTools, "execute", unproved_drain)
    monkeypatch.setattr(service.store, "append_event", fail_event)
    monkeypatch.setattr(service.store, "update_run", fail_terminal_update_once)
    scripted_provider.enqueue_json(_native(("unproved-command", "execute_command", {"command": "true"})))
    try:
        result = await service.run_role(command, scope=scope, limits=limits, cancel=asyncio.Event())
        assert (result.status, result.verification) == ("interrupted", "unknown")
        assert "injected unproved physical drain" in result.text
        assert command.session_id in service._retained_locks
        with pytest.raises(WorkspaceBusyError), service.store.acquire_workspace(isolated_workspace):
            pytest.fail("writer became available without physical drain proof")
        ended = next(event for event in service.store.load_events(command.session_id) if event["kind"] == "run_ended")
        assert ended["payload"]["release_allowed"] is False
        pending = service.store.pending_tool_calls(command.session_id)
        assert len(pending) == 1 and pending[0]["observation"] is None
    finally:
        # The execution port launched nothing; release the intentionally retained
        # real OS lock only as test teardown after the policy assertions.
        for lock in service._retained_locks.values():
            lock.__exit__(None, None, None)
        service._retained_locks.clear()


def test_provider_connection_failure_is_recoverable_transport_failure() -> None:
    """A provider that cannot be reached must not end the engineering run.

    The transport clients raise their own connection errors (aiohttp's
    ClientConnectorError is not a builtin ConnectionError), and such an
    error used to classify as unknown: neither retryable nor recoverable by
    the same role, so one transient connection blip ended the run.  A network
    failure is a transport failure and the same role continues its goal.
    """
    from polaris.cells.roles.runtime.internal.engineering_loop import _is_transport_failure
    from polaris.kernelone.errors import ErrorCategory
    from polaris.kernelone.llm.engine.contracts import AIResponse

    network_failure = AIResponse(
        ok=False,
        output="",
        error="All providers exhausted. Last error: Cannot connect to host apihub.agnes-ai.com:443",
        error_category=ErrorCategory.NETWORK_ERROR,
    )
    assert _is_transport_failure(network_failure) is True

    timeout_failure = AIResponse(
        ok=False,
        output="",
        error="All providers exhausted. Last error: provider_physical_http_total_timeout",
        error_category=ErrorCategory.TIMEOUT,
    )
    assert _is_transport_failure(timeout_failure) is True

    semantic_failure = AIResponse(
        ok=False,
        output="",
        error="invalid response payload",
        error_category=ErrorCategory.INVALID_RESPONSE,
    )
    assert _is_transport_failure(semantic_failure) is False


async def test_loop_detection_injects_recovery_guidance(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model repeating the same probe gets host recovery guidance, not a silent loop."""
    (isolated_workspace / "note.txt").write_text("seed\n", encoding="utf-8")
    loop, store, _tools, command, scope, limits = _setup(isolated_workspace)
    for _ in range(5):
        scripted_provider.enqueue_json(_native(("probe", "repo_tree", {"depth": 5})))
    scripted_provider.enqueue_json(_native(text="Understood; proceeding to implementation."))
    result = await loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
    )
    assert result.status == "completed"
    events = store.load_events(command.session_id)
    detections = [event for event in events if event["kind"] == "loop_detected"]
    assert detections, "expected a loop_detected runtime event"
    assert detections[0]["payload"]["consecutive_identical_turns"] == 4
    assert detections[0]["payload"]["guidance_injected"] is True
    # The guidance must reach a later provider request as a host message.
    bodies = [json.dumps(request["body"], ensure_ascii=False) for request in scripted_provider.requests]
    assert any("Host loop detection" in body for body in bodies)
    assert any("do not re-list the workspace" in body for body in bodies)
    # A changed call signature resets the counter: no second detection.
    assert len(detections) == 1


async def test_stage_gate_blocks_writes_until_verification(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stage gate forces a build -> verify -> fix rhythm instead of endless writes."""
    monkeypatch.setenv("KERNELONE_ENGINEERING_STAGE_GATE_WRITES", "2")
    # Isolate the stage gate from the wrap-up gate (tested separately).
    monkeypatch.setenv("KERNELONE_ENGINEERING_WRAP_UP_GATE", "0")
    loop, store, _tools, command, scope, limits = _setup(
        isolated_workspace, enabled=_ALL_TOOLS | frozenset({"verify_project"})
    )
    scripted_provider.enqueue_json(_native(("w1", "write_file", {"file": "a.py", "content": "x = 1\n"})))
    scripted_provider.enqueue_json(_native(("w2", "write_file", {"file": "b.py", "content": "x = 2\n"})))
    # Third write must be stage-gated (synthetic observation, no file effect).
    scripted_provider.enqueue_json(_native(("w3", "write_file", {"file": "c.py", "content": "x = 3\n"})))
    # Model then verifies; the gate opens for the next stage.
    scripted_provider.enqueue_json(_native(("v1", "verify_project", {"commands": ["python3 -m py_compile a.py b.py"]})))
    scripted_provider.enqueue_json(_native(("w4", "write_file", {"file": "c.py", "content": "x = 3\n"})))
    scripted_provider.enqueue_json(_native(text="Stage complete."))
    from polaris.cells.roles.runtime.public.contracts.engineering_run import ToolObservation

    async def fake_verify(commands: tuple[str, ...], turn_seq: int, call_id: str) -> ToolObservation:
        return ToolObservation(
            call_id=call_id, tool="verify_project", ok=True, content="checks ran", exit_code=0, interrupted=False
        )

    result = await loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
        verify=fake_verify,
    )
    assert result.status == "completed"
    events = store.load_events(command.session_id)
    gated = [event for event in events if event["kind"] == "stage_gate_enforced"]
    assert gated, "expected a stage_gate_enforced event"
    observations = [event["payload"] for event in events if event["kind"] == "tool_observed"]
    blocked = next(obs for obs in observations if obs["call_id"] == "w3")
    assert blocked["ok"] is False
    assert "Stage gate" in blocked["content"]
    # The gated write had no effect; the post-verification write succeeded.
    assert (isolated_workspace / "c.py").read_text(encoding="utf-8") == "x = 3\n"


async def test_wrap_up_gate_blocks_post_pass_edits(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A passing verification must not be invalidated by endless post-pass edits.

    Weak models pass verify_project and then keep polishing, advancing the
    candidate revision and forcing an endless re-verify cycle that never reaches
    independent QA. Explicit source writes after a current pass are intercepted;
    two blocked attempts end the Director frame so QA reviews the candidate that
    actually passed.
    """
    monkeypatch.setenv("KERNELONE_ENGINEERING_STAGE_GATE_WRITES", "0")
    loop, store, _tools, command, scope, limits = _setup(
        isolated_workspace, enabled=_ALL_TOOLS | frozenset({"verify_project"})
    )
    (isolated_workspace / "a.py").write_text("x = 1\n", encoding="utf-8")
    scripted_provider.enqueue_json(_native(("v1", "verify_project", {"commands": ["python3 -m py_compile a.py"]})))
    # One legitimate change-then-reverify cycle is allowed.
    scripted_provider.enqueue_json(
        _native(("w1", "edit_file", {"file": "a.py", "search": "x = 1", "replace": "x = 2"}))
    )
    scripted_provider.enqueue_json(_native(("v2", "verify_project", {"commands": ["python3 -m py_compile a.py"]})))
    # The second pass -> write cycle is thrashing; both edits are intercepted.
    scripted_provider.enqueue_json(
        _native(("w2", "edit_file", {"file": "a.py", "search": "x = 2", "replace": "x = 3"}))
    )
    scripted_provider.enqueue_json(
        _native(("w3", "edit_file", {"file": "a.py", "search": "x = 2", "replace": "x = 4"}))
    )
    from polaris.cells.roles.runtime.public.contracts.engineering_run import ToolObservation

    async def fake_verify(commands: tuple[str, ...], turn_seq: int, call_id: str) -> ToolObservation:
        return ToolObservation(
            call_id=call_id, tool="verify_project", ok=True, content="checks ran", exit_code=0, interrupted=False
        )

    result = await loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
        verify=fake_verify,
    )
    assert result.status == "completed", result.text
    events = store.load_events(command.session_id)
    blocked = [event for event in events if event["kind"] == "wrap_up_enforced"]
    assert [event["payload"]["blocks"] for event in blocked] == [1, 2]
    assert [event["payload"]["call_id"] for event in blocked] == ["w2", "w3"]
    observations = [event["payload"] for event in events if event["kind"] == "tool_observed"]
    assert all(
        obs["ok"] is False and "Wrap-up gate" in obs["content"]
        for obs in observations
        if obs["call_id"] in {"w2", "w3"}
    )
    # The allowed cycle applied; the blocked edits never executed.
    assert (isolated_workspace / "a.py").read_text(encoding="utf-8") == "x = 2\n"


async def test_wrap_up_gate_can_be_disabled(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KERNELONE_ENGINEERING_STAGE_GATE_WRITES", "0")
    monkeypatch.setenv("KERNELONE_ENGINEERING_WRAP_UP_GATE", "0")
    loop, store, _tools, command, scope, limits = _setup(
        isolated_workspace, enabled=_ALL_TOOLS | frozenset({"verify_project"})
    )
    (isolated_workspace / "a.py").write_text("x = 1\n", encoding="utf-8")
    scripted_provider.enqueue_json(_native(("v1", "verify_project", {"commands": ["python3 -m py_compile a.py"]})))
    scripted_provider.enqueue_json(
        _native(("w1", "edit_file", {"file": "a.py", "search": "x = 1", "replace": "x = 2"}))
    )
    scripted_provider.enqueue_json(_native(text="Done."))
    from polaris.cells.roles.runtime.public.contracts.engineering_run import ToolObservation

    async def fake_verify(commands: tuple[str, ...], turn_seq: int, call_id: str) -> ToolObservation:
        return ToolObservation(
            call_id=call_id, tool="verify_project", ok=True, content="checks ran", exit_code=0, interrupted=False
        )

    result = await loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
        verify=fake_verify,
    )
    assert result.status == "completed", result.text
    events = store.load_events(command.session_id)
    assert not [event for event in events if event["kind"] == "wrap_up_enforced"]
    assert (isolated_workspace / "a.py").read_text(encoding="utf-8") == "x = 2\n"
