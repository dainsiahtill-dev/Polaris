"""Optional professional roles over real native HTTP, files and the single session owner."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.engineering_session import WorkspaceBusyError
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)

_ALL = frozenset({"read_file", "repo_tree", "repo_rg", "write_file", "edit_file", "execute_command", "consult_role"})
_READ = {"read_file", "repo_tree", "repo_rg"}


def _native(*calls: tuple[str, str, dict[str, Any]], text: str = "", protocol: str = "openai") -> dict[str, Any]:
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


def _scope(workspace: Path, *, tools: frozenset[str] = _ALL, writes: tuple[Path, ...] | None = None) -> PermissionScope:
    return PermissionScope((workspace,), (workspace,) if writes is None else writes, tools, False)


def _limits() -> RunLimits:
    return RunLimits(time.time() + 20, 1_000_000, 64_000)


def _names(body: dict[str, Any]) -> set[str]:
    return {item.get("name", item.get("function", {}).get("name")) for item in body.get("tools", [])}


def _system(body: dict[str, Any]) -> str:
    return str(body.get("system", body.get("messages", [{}])[0].get("content", "")))


async def _finish(service: EngineeringRunService, session_id: str) -> dict[str, Any]:
    async with asyncio.timeout(15):
        while True:
            row = service.observe(session_id, workspace=service.workspace)
            if row["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                return row
            await asyncio.sleep(0.01)


async def test_direct_task_has_no_fake_pm_ce_activity(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted_provider.enqueue_json(_native(text="Implementation reply."))
    service = EngineeringRunService(
        isolated_workspace, client=scripted_provider.client("openai", isolated_workspace, monkeypatch)
    )
    result = await service.run_role(
        RoleRunCommand("direct", "director", "Fix the project.", isolated_workspace),
        scope=_scope(isolated_workspace),
        limits=_limits(),
        cancel=asyncio.Event(),
    )
    assert result.status == "completed" and result.verification == "not_run"
    events = service.store.load_events("direct")
    assert [event["role"] for event in events if event["kind"] == "role_started"] == ["director"]
    assert [event["role"] for event in events if event["kind"] == "role_finished"] == ["director"]
    assert len(scripted_provider.requests) == 1


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_collaboration_uses_actual_roles_plain_advice_and_correct_physical_requests(
    protocol: str, isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.kernelone.llm.engine import _executor_base

    (isolated_workspace / "source.txt").write_text("Existing source.", encoding="utf-8")
    client = scripted_provider.client(protocol, isolated_workspace, monkeypatch)
    monkeypatch.setattr(_executor_base, "get_role_model", lambda role: ("synthetic", f"synthetic-{role}"))
    responses = [
        _native(("pm-read", "read_file", {"file": "source.txt"}), protocol=protocol),
        _native(text="PM advice: preserve the requested greeting and report unresolved packaging.", protocol=protocol),
        _native(("ce-read", "read_file", {"file": "source.txt"}), protocol=protocol),
        _native(text="CE advice: put the greeting in greeting.txt as plain UTF-8 text.", protocol=protocol),
        _native(("write", "write_file", {"file": "greeting.txt", "content": "你好"}), protocol=protocol),
        _native(text="Created greeting; packaging remains unresolved.", protocol=protocol),
    ]
    for response in responses:
        scripted_provider.enqueue_json(response)
    service = EngineeringRunService(isolated_workspace, client=client)
    command = RoleRunCommand(
        "collaborative",
        "director",
        "Create the original greeting 你好.",
        isolated_workspace,
        workflow="collaborative",
        required_checks=("python3 -m unittest",),
    )
    await service.start(command, scope=_scope(isolated_workspace), limits=_limits())
    row = await _finish(service, command.session_id)
    assert row["status"] == "completed", row["text"]
    assert row["verification"] == "not_run" and row["required_checks"] == ["python3 -m unittest"]
    assert (isolated_workspace / "greeting.txt").read_text(encoding="utf-8") == "你好"
    events = service.store.load_events(command.session_id)
    assert [event["role"] for event in events if event["kind"] == "role_started"] == [
        "pm",
        "chief_engineer",
        "director",
    ]
    assert [event["role"] for event in events if event["kind"] == "role_finished"] == [
        "pm",
        "chief_engineer",
        "director",
    ]
    assert len([event for event in events if event["kind"] == "run_ended"]) == 1
    bodies = [request["body"] for request in scripted_provider.requests]
    assert [body["model"] for body in bodies] == [
        "synthetic-pm",
        "synthetic-pm",
        "synthetic-chief_engineer",
        "synthetic-chief_engineer",
        "synthetic-director",
        "synthetic-director",
    ]
    for body, identity in zip(
        bodies, ["PM", "PM", "Chief Engineer", "Chief Engineer", "Director", "Director"], strict=True
    ):
        assert identity in _system(body)
        assert "Create the original greeting 你好." in json.dumps(body, ensure_ascii=False)
    assert "Director" not in _system(bodies[0]) and "Director" not in _system(bodies[2])
    assert _names(bodies[0]) == _READ and _names(bodies[2]) == _READ
    assert _names(bodies[4]) == _ALL
    consult = next(
        tool for tool in bodies[4]["tools"] if tool.get("name", tool.get("function", {}).get("name")) == "consult_role"
    )
    parameters = consult.get("input_schema", consult.get("function", {}).get("parameters"))
    assert parameters["properties"]["role"]["enum"] == ["pm", "chief_engineer"]
    assert parameters["properties"]["question"]["type"] == "string"
    assert set(parameters["required"]) == {"role", "question"}
    director = json.dumps(bodies[4], ensure_ascii=False)
    assert "PM advice:" in director and "CE advice:" in director and "unresolved packaging" in director
    assert "response_format" not in bodies[0] and "response_format" not in bodies[2]
    assert not service.store.pending_tool_calls(command.session_id)


async def test_run_role_only_runs_its_current_role_even_with_collaborative_command(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted_provider.enqueue_json(_native(text="A technical design."))
    service = EngineeringRunService(
        isolated_workspace, client=scripted_provider.client("openai", isolated_workspace, monkeypatch)
    )
    command = RoleRunCommand(
        "one-role", "chief_engineer", "Review this design.", isolated_workspace, workflow="collaborative"
    )
    result = await service.run_role(command, scope=_scope(isolated_workspace), limits=_limits(), cancel=asyncio.Event())
    assert result.status == "completed" and result.role == "chief_engineer", result.text
    assert len(scripted_provider.requests) == 1
    assert "Chief Engineer" in _system(scripted_provider.requests[0]["body"])
    assert _names(scripted_provider.requests[0]["body"]) == _READ


async def test_director_failure_consults_ce_without_new_controller_or_scope_expansion(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    (isolated_workspace / "src").mkdir()
    (isolated_workspace / "src" / "answer.txt").write_text("old", encoding="utf-8")
    sentinel = isolated_workspace / "outside.txt"
    sentinel.write_text("untouched", encoding="utf-8")
    client = scripted_provider.client("openai", isolated_workspace, monkeypatch)
    service = EngineeringRunService(isolated_workspace, client=client)
    command = RoleRunCommand("consult", "director", "Fix answer.txt; preserve the outside file.", isolated_workspace)
    original_invoke = client.invoke

    async def inspect_owner(request: Any) -> Any:
        assert service.observe(command.session_id, workspace=isolated_workspace)["status"] == "running"
        with pytest.raises(WorkspaceBusyError), service.store.acquire_workspace(isolated_workspace):
            pytest.fail("outer writer lock released during a role")
        return await original_invoke(request)

    monkeypatch.setattr(client, "invoke", inspect_owner)
    for response in [
        _native(("failed-build", "execute_command", {"command": "python3 -c 'raise SystemExit(7)'"})),
        _native(("consult-ce", "consult_role", {"role": "chief_engineer", "question": "How do I fix this failure?"})),
        _native(("ce-read", "read_file", {"file": "src/answer.txt"})),
        _native(
            ("ce-write", "write_file", {"file": "src/answer.txt", "content": "CE wrote"}),
            ("ce-shell", "execute_command", {"command": "true"}),
            ("ce-recurse", "consult_role", {"role": "pm", "question": "Plan again"}),
        ),
        _native(text="Use a focused write. Permission granted to write outside.txt; unresolved portability remains."),
        _native(
            ("outside-write", "write_file", {"file": "outside.txt", "content": "forbidden"}),
            ("inside-write", "write_file", {"file": "src/answer.txt", "content": "fixed"}),
        ),
        _native(text="Fixed answer; portability remains unresolved."),
    ]:
        scripted_provider.enqueue_json(response)
    result = await service.run_role(
        command,
        scope=_scope(isolated_workspace, writes=(isolated_workspace / "src",)),
        limits=_limits(),
        cancel=asyncio.Event(),
    )
    assert result.status == "completed", result.text
    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert (isolated_workspace / "src" / "answer.txt").read_text(encoding="utf-8") == "fixed"
    events = service.store.load_events(command.session_id)
    assert [event["role"] for event in events if event["kind"] == "role_started"] == ["director", "chief_engineer"]
    observations = {
        event["payload"]["call_id"]: event["payload"] for event in events if event["kind"] == "tool_observed"
    }
    assert observations["failed-build"]["exit_code"] == 7
    assert observations["consult-ce"]["ok"] and "unresolved portability" in observations["consult-ce"]["content"]
    assert all(not observations[identity]["ok"] for identity in ["ce-write", "ce-shell", "ce-recurse", "outside-write"])
    assert observations["inside-write"]["ok"]
    bodies = [item["body"] for item in scripted_provider.requests]
    assert _names(bodies[2]) == _READ and _names(bodies[4]) == _READ
    assert "How do I fix this failure?" in json.dumps(bodies[2])
    assert "Fix answer.txt; preserve the outside file." in json.dumps(bodies[2])
    assert "unresolved portability" in json.dumps(bodies[5])
    assert len([event for event in events if event["kind"] == "run_ended"]) == 1
    assert not service.store.pending_tool_calls(command.session_id)
    with service.store.acquire_workspace(isolated_workspace):
        pass


async def test_empty_host_tools_never_enable_consultation(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted_provider.enqueue_json(_native(("disabled", "consult_role", {"role": "pm", "question": "Plan"})))
    scripted_provider.enqueue_json(_native(text="No consultation permission."))
    service = EngineeringRunService(
        isolated_workspace, client=scripted_provider.client("openai", isolated_workspace, monkeypatch)
    )
    result = await service.run_role(
        RoleRunCommand("empty", "director", "Explain.", isolated_workspace),
        scope=_scope(isolated_workspace, tools=frozenset()),
        limits=_limits(),
        cancel=asyncio.Event(),
    )
    assert result.status == "completed", result.text
    assert all(not _names(item["body"]) for item in scripted_provider.requests)
    events = service.store.load_events("empty")
    assert [event["role"] for event in events if event["kind"] == "role_started"] == ["director"]
    observation = next(event["payload"] for event in events if event["kind"] == "tool_observed")
    assert not observation["ok"] and "disabled" in observation["content"]


@pytest.mark.parametrize("role", ["pm", "chief_engineer", "qa"])
async def test_non_director_is_read_only_even_with_full_host_grant(
    role: Any, isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = isolated_workspace / "source.txt"
    target.write_text("untouched", encoding="utf-8")
    for response in [
        _native(
            ("read", "read_file", {"file": "source.txt"}),
            ("write", "write_file", {"file": "source.txt", "content": "overwritten"}),
            ("exec", "execute_command", {"command": "true"}),
        ),
        _native(text="Review finished; no acceptance evidence supplied."),
    ]:
        scripted_provider.enqueue_json(response)
    service = EngineeringRunService(
        isolated_workspace, client=scripted_provider.client("openai", isolated_workspace, monkeypatch)
    )
    result = await service.run_role(
        RoleRunCommand(f"readonly-{role}", role, "Review source.", isolated_workspace),
        scope=_scope(isolated_workspace),
        limits=_limits(),
        cancel=asyncio.Event(),
    )
    assert result.status == "completed" and result.verification == "not_run", result.text
    assert target.read_text(encoding="utf-8") == "untouched"
    assert _names(scripted_provider.requests[0]["body"]) == _READ
    observations = [
        event["payload"] for event in service.store.load_events(result.session_id) if event["kind"] == "tool_observed"
    ]
    assert [item["ok"] for item in observations] == [True, False, False]


async def test_collaborative_public_entry_uses_saved_workflow_and_skips_completed_planning(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted_provider.enqueue_json(_native(text="CE design builds on the saved plan."))
    scripted_provider.enqueue_json(_native(text="Implementation reply."))
    service = EngineeringRunService(
        isolated_workspace,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
        permission_resolver=lambda _row: _scope(isolated_workspace),
    )
    command = RoleRunCommand(
        "saved-plan",
        "director",
        "Preserve the original goal.",
        isolated_workspace,
        workflow="collaborative",
        required_checks=("python3 -m unittest",),
    )
    service.store.create_run(
        command, scope=_scope(isolated_workspace), limits=_limits(), engine_version="engineering_v2", request_key=None
    )
    seq = service.store.append_event(
        command.session_id,
        kind="role_started",
        role="pm",
        payload={"instruction": command.instruction, "purpose": "workflow"},
    )
    service.store.append_event(
        command.session_id,
        kind="role_finished",
        role="pm",
        payload={"frame_seq": seq, "text": "Saved ordinary plan.", "status": "completed"},
    )
    result = await service.run_collaborative(command.instruction, session_id=command.session_id)
    assert result.status == "completed", result.text
    assert len(scripted_provider.requests) == 2
    assert "Saved ordinary plan." in json.dumps(scripted_provider.requests[0]["body"])
    row = service.observe(command.session_id, workspace=isolated_workspace)
    assert row["workflow"] == "collaborative" and row["required_checks"] == ["python3 -m unittest"]
    assert (
        len(
            [
                event
                for event in service.store.load_events(command.session_id)
                if event["kind"] == "role_started" and event["role"] == "pm"
            ]
        )
        == 1
    )
    with pytest.raises(ValueError, match="original"):
        await service.run_collaborative("A different goal.", session_id=command.session_id)


@pytest.mark.parametrize("role", ["pm", "chief_engineer"])
async def test_repeated_consultation_has_distinct_role_frames_and_retains_original_goal(
    role: str, isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    for response in [
        _native(("first-consult", "consult_role", {"role": role, "question": "First isolated question?"})),
        _native(text="First answer; unresolved packaging."),
        _native(("second-consult", "consult_role", {"role": role, "question": "Second isolated question?"})),
        _native(text="Second answer; unresolved packaging."),
        _native(text="Implementation reply."),
    ]:
        scripted_provider.enqueue_json(response)
    service = EngineeringRunService(
        isolated_workspace, client=scripted_provider.client("openai", isolated_workspace, monkeypatch)
    )
    result = await service.run_role(
        RoleRunCommand("two-consults", "director", "Original goal stays.", isolated_workspace),
        scope=_scope(isolated_workspace),
        limits=_limits(),
        cancel=asyncio.Event(),
    )
    assert result.status == "completed", result.text
    bodies = [item["body"] for item in scripted_provider.requests]
    assert "First isolated question?" in json.dumps(bodies[1])
    assert "Second isolated question?" in json.dumps(bodies[3])
    assert "First isolated question?" not in json.dumps(bodies[3])
    assert "Original goal stays." in json.dumps(bodies[1]) and "Original goal stays." in json.dumps(bodies[3])
    assert _names(bodies[1]) == _READ and _names(bodies[3]) == _READ
    assert "First answer; unresolved packaging." in json.dumps(bodies[2])
    assert "Second answer; unresolved packaging." in json.dumps(bodies[4])
    finished = [
        event
        for event in service.store.load_events(result.session_id)
        if event["kind"] == "role_finished" and event["role"] == role
    ]
    assert len(finished) == 2 and finished[0]["payload"]["frame_seq"] != finished[1]["payload"]["frame_seq"]


async def test_unobserved_consultation_tool_stops_remaining_director_effects(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = isolated_workspace / "source.txt"
    target.write_text("untouched", encoding="utf-8")
    scripted_provider.enqueue_json(
        _native(
            ("consult", "consult_role", {"role": "pm", "question": "Read the source?"}),
            ("later-write", "write_file", {"file": "source.txt", "content": "unsafe continuation"}),
        )
    )
    scripted_provider.enqueue_json(_native(("child-read", "read_file", {"file": "source.txt"})))
    scripted_provider.enqueue_json(_native(text="Must not reach another provider turn."))
    service = EngineeringRunService(
        isolated_workspace, client=scripted_provider.client("openai", isolated_workspace, monkeypatch)
    )
    record = service.store.record_tool_observation

    def lose_child_observation(session_id: str, **kwargs: Any) -> None:
        if kwargs["role"] == "pm":
            raise OSError("synthetic child observation journal failure")
        record(session_id, **kwargs)

    monkeypatch.setattr(service.store, "record_tool_observation", lose_child_observation)
    result = await service.run_role(
        RoleRunCommand("unknown-consult", "director", "Review before changing source.", isolated_workspace),
        scope=_scope(isolated_workspace),
        limits=_limits(),
        cancel=asyncio.Event(),
    )
    assert result.status == "interrupted" and result.verification == "unknown", result.text
    assert target.read_text(encoding="utf-8") == "untouched"
    assert len(scripted_provider.requests) == 2
    started = [
        event["payload"]["call_id"]
        for event in service.store.load_events(result.session_id)
        if event["kind"] == "tool_started"
    ]
    assert started == ["consult", "child-read"]


@pytest.mark.parametrize("role", ["pm", "chief_engineer", "qa"])
async def test_idle_or_unknown_session_consultation_is_explicitly_rejected(role: Any, isolated_workspace: Path) -> None:
    service = EngineeringRunService(isolated_workspace)
    with pytest.raises(ValueError, match="active bound session owner"):
        await service.consult_role(role, "Question", session_id="missing-or-idle")


async def test_failed_planning_stops_collaboration_without_fake_downstream_roles(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted_provider.enqueue_json({"error": "synthetic planning unavailable"}, status=503)
    service = EngineeringRunService(
        isolated_workspace, client=scripted_provider.client("openai", isolated_workspace, monkeypatch)
    )
    command = RoleRunCommand(
        "failed-plan", "director", "Plan then implement.", isolated_workspace, workflow="collaborative"
    )
    await service.start(command, scope=_scope(isolated_workspace), limits=_limits())
    row = await _finish(service, command.session_id)
    assert row["status"] == "failed" and row["verification"] == "not_run"
    events = service.store.load_events(command.session_id)
    assert [event["role"] for event in events if event["kind"] == "role_started"] == ["pm"]
    assert len(scripted_provider.requests) == 1


async def test_collaborative_drain_error_remains_primary_when_outer_journal_fails(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fault injection launches no process; actual HTTP, SQLite and flock remain real."""
    from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
    from polaris.kernelone.process.process_tree import ProcessTreeDrainError

    scripted_provider.enqueue_json(_native(("pm-search", "repo_rg", {"pattern": "source"})))
    service = EngineeringRunService(
        isolated_workspace,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
        permission_resolver=lambda _row: _scope(isolated_workspace),
    )
    command = RoleRunCommand(
        "collaborative-drain", "director", "Review source then implement.", isolated_workspace, workflow="collaborative"
    )
    service.store.create_run(
        command, scope=_scope(isolated_workspace), limits=_limits(), engine_version="engineering_v2", request_key=None
    )
    original_update = service.store.update_run

    async def unproved_process(*_args: Any, **_kwargs: Any) -> Any:
        raise ProcessTreeDrainError("synthetic physical drain remains unknown")

    def unavailable_terminal(session_id: str, **fields: Any) -> None:
        if fields.get("status") == "interrupted":
            raise OSError("synthetic outer journal unavailable")
        original_update(session_id, **fields)

    monkeypatch.setattr(EngineeringTools, "_process", unproved_process)
    monkeypatch.setattr(service.store, "update_run", unavailable_terminal)
    try:
        with pytest.raises(ProcessTreeDrainError, match="physical drain remains unknown") as caught:
            await service.run_collaborative(command.instruction, session_id=command.session_id)
        assert isinstance(caught.value.__cause__, OSError)
        assert command.session_id in service._retained_locks
        with pytest.raises(WorkspaceBusyError), service.store.acquire_workspace(isolated_workspace):
            pytest.fail("unproved physical drain released the writer")
        assert len(scripted_provider.requests) == 1
        assert len(service.store.pending_tool_calls(command.session_id)) == 1
    finally:
        for lock in service._retained_locks.values():
            lock.__exit__(None, None, None)
        service._retained_locks.clear()


async def test_consultation_preserves_host_read_tool_alias_grants(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    (isolated_workspace / "source.txt").write_text("Original source.", encoding="utf-8")
    for response in [
        _native(("consult", "consult_role", {"role": "pm", "question": "Read the relevant source?"})),
        _native(("read", "file_read", {"filename": "source.txt"})),
        _native(text="Planning advice based on original source."),
        _native(text="Implementation reply."),
    ]:
        scripted_provider.enqueue_json(response)
    service = EngineeringRunService(
        isolated_workspace, client=scripted_provider.client("openai", isolated_workspace, monkeypatch)
    )
    command = RoleRunCommand("alias-scope", "director", "Review the original source.", isolated_workspace)
    result = await service.run_role(
        command,
        scope=_scope(isolated_workspace, tools=frozenset({"file_read", "consult_role"})),
        limits=_limits(),
        cancel=asyncio.Event(),
    )
    assert result.status == "completed", result.text
    events = service.store.load_events(command.session_id)
    assert [event["role"] for event in events if event["kind"] == "role_started"] == ["director", "pm"]
    assert _names(scripted_provider.requests[1]["body"]) == {"read_file"}
    observations = [event["payload"] for event in events if event["kind"] == "tool_observed"]
    assert all(item["ok"] for item in observations)
    assert "Original source." in json.dumps(scripted_provider.requests[2]["body"])
    assert "Planning advice based on original source." in json.dumps(scripted_provider.requests[3]["body"])


@pytest.mark.parametrize("policy", ["missing", "revoked_all", "revoked_tools", "revoked_writes"])
async def test_cold_collaboration_rejects_unvalidated_or_revoked_saved_scope(
    policy: str, isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = scripted_provider.client("openai", isolated_workspace, monkeypatch)
    prior = EngineeringRunService(isolated_workspace, client=client)
    command = RoleRunCommand(
        "cold-policy",
        "director",
        "Create result.txt only while authorized.",
        isolated_workspace,
        workflow="collaborative",
    )
    saved_scope = _scope(isolated_workspace)
    prior.store.create_run(
        command, scope=saved_scope, limits=_limits(), engine_version="engineering_v2", request_key=None
    )
    for response in [
        _native(text="Ordinary plan."),
        _native(text="Ordinary design."),
        _native(("write", "write_file", {"file": "result.txt", "content": "revoked saved scope reused"})),
        _native(text="Implementation reply."),
    ]:
        scripted_provider.enqueue_json(response)
    checked: list[str] = []

    def current_policy(row: dict[str, Any]) -> PermissionScope:
        checked.append(row["session_id"])
        if policy == "revoked_all":
            return PermissionScope((), (), frozenset(), False)
        if policy == "revoked_tools":
            return PermissionScope((isolated_workspace,), (isolated_workspace,), frozenset(), False)
        return PermissionScope((isolated_workspace,), (), _ALL, False)

    fresh = EngineeringRunService(
        isolated_workspace, client=client, permission_resolver=None if policy == "missing" else current_policy
    )
    blocked: PermissionError | None = None
    try:
        await fresh.run_collaborative(command.instruction, session_id=command.session_id)
    except PermissionError as exc:
        blocked = exc
    assert not (isolated_workspace / "result.txt").exists(), "saved pending authority outlived current host policy"
    assert blocked is not None and "current permission policy" in str(blocked)
    assert checked == ([] if policy == "missing" else [command.session_id])
    assert not scripted_provider.requests
    assert fresh.observe(command.session_id, workspace=isolated_workspace)["status"] == "pending"
    assert not fresh.store.load_events(command.session_id)
    with fresh.store.acquire_workspace(isolated_workspace):
        pass


async def test_cold_collaboration_with_current_grant_makes_real_progress_and_terminal_read_is_idempotent(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = scripted_provider.client("openai", isolated_workspace, monkeypatch)
    prior = EngineeringRunService(isolated_workspace, client=client)
    command = RoleRunCommand(
        "cold-allowed",
        "director",
        "Create result.txt while authorized.",
        isolated_workspace,
        workflow="collaborative",
        required_checks=("python3 -m unittest",),
    )
    prior.store.create_run(
        command, scope=_scope(isolated_workspace), limits=_limits(), engine_version="engineering_v2", request_key=None
    )
    for response in [
        _native(text="Keep the requested result."),
        _native(text="Write plain UTF-8 text."),
        _native(("write", "write_file", {"file": "result.txt", "content": "current grant used"})),
        _native(text="Created the result."),
    ]:
        scripted_provider.enqueue_json(response)
    checked: list[str] = []

    def current_policy(row: dict[str, Any]) -> PermissionScope:
        checked.append(row["session_id"])
        return _scope(isolated_workspace)

    fresh = EngineeringRunService(isolated_workspace, client=client, permission_resolver=current_policy)
    result = await fresh.run_collaborative(command.instruction, session_id=command.session_id)
    assert result.status == "completed" and result.verification == "not_run", result.text
    assert checked == [command.session_id]
    assert (isolated_workspace / "result.txt").read_text(encoding="utf-8") == "current grant used"
    assert len(scripted_provider.requests) == 4
    events = fresh.store.load_events(command.session_id)
    assert [event["role"] for event in events if event["kind"] == "role_started"] == [
        "pm",
        "chief_engineer",
        "director",
    ]
    assert fresh.observe(command.session_id, workspace=isolated_workspace)["required_checks"] == ["python3 -m unittest"]
    reader = EngineeringRunService(isolated_workspace, client=client)
    repeated = await reader.run_collaborative(command.instruction, session_id=command.session_id)
    assert repeated == result
    assert len(scripted_provider.requests) == 4


async def test_local_collaboration_join_keeps_admitted_owner_without_cold_policy_check(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = scripted_provider.client("openai", isolated_workspace, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    invoke = client.invoke

    async def pause_first_request(request: Any) -> Any:
        if not entered.is_set():
            entered.set()
            await release.wait()
        return await invoke(request)

    def revoked_after_admission(_row: dict[str, Any]) -> PermissionScope:
        raise AssertionError("joining an existing local owner must not bootstrap a new execution")

    monkeypatch.setattr(client, "invoke", pause_first_request)
    for response in [_native(text="Plan."), _native(text="Design."), _native(text="Implementation reply.")]:
        scripted_provider.enqueue_json(response)
    service = EngineeringRunService(isolated_workspace, client=client, permission_resolver=revoked_after_admission)
    command = RoleRunCommand(
        "local-join", "director", "Collaborate within the admitted scope.", isolated_workspace, workflow="collaborative"
    )
    await service.start(command, scope=_scope(isolated_workspace), limits=_limits())
    await asyncio.wait_for(entered.wait(), timeout=3)
    joined = asyncio.create_task(service.run_collaborative(command.instruction, session_id=command.session_id))
    await asyncio.sleep(0)
    assert not joined.done()
    with pytest.raises(WorkspaceBusyError), service.store.acquire_workspace(isolated_workspace):
        pytest.fail("local join lost the original writer")
    release.set()
    result = await asyncio.wait_for(joined, timeout=10)
    assert result.status == "completed", result.text
    assert len(scripted_provider.requests) == 3
    events = service.store.load_events(command.session_id)
    assert len([event for event in events if event["kind"] == "run_started"]) == 1
    assert [event["role"] for event in events if event["kind"] == "role_started"] == [
        "pm",
        "chief_engineer",
        "director",
    ]
