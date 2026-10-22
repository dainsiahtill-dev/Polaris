"""Budget input proof with real loop/store/native HTTP and synthetic model replies.

Only configuration/model responses and wall-clock observations are controlled.
The loopback port is temporary; no production model, browser or service runs.
These tests do not establish model convergence, delivery quality or cost benefit.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest
from polaris.cells.roles.runtime.internal.engineering_loop import run_engineering_loop
from polaris.cells.roles.runtime.internal.engineering_roles import role_instruction
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.kernelone.storage.io_paths import resolve_storage_roots

_IMAGE = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
_MARKER = "Host runtime budget observation"
_BUDGET = re.compile(
    r"deadline_epoch_seconds=([0-9]+\.[0-9]+); observed_at_epoch_seconds=([0-9]+\.[0-9]+); "
    r"approximate_remaining_seconds=([0-9]+\.[0-9]+)"
)


def _clock(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    # Replace this module's clock reference, not the process-wide time module.
    clock = SimpleNamespace(now=time.time())
    module = importlib.import_module("polaris.cells.roles.runtime.internal.engineering_loop")
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: clock.now))
    return clock


def _scope(workspace: Path) -> PermissionScope:
    return PermissionScope((workspace,), (), frozenset({"read_file", "repo_tree"}), False)


def _reply(protocol: str, *, read: bool = False) -> dict[str, Any]:
    if protocol == "anthropic":
        return {
            "id": "synthetic-budget-reply",
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "inspect", "name": "read_file", "input": {"file": "source.txt"}}
                if read
                else {"type": "text", "text": "Read complete."}
            ],
            "stop_reason": "tool_use" if read else "end_turn",
        }
    message: dict[str, Any] = {"role": "assistant", "content": "" if read else "Read complete."}
    if read:
        message["tool_calls"] = [
            {
                "id": "inspect",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"file":"source.txt"}'},
            }
        ]
    return {"choices": [{"message": message, "finish_reason": "tool_calls" if read else "stop"}]}


def _system(body: dict[str, Any], protocol: str) -> str:
    return body["system"] if protocol == "anthropic" else body["messages"][0]["content"]


def _tail_observation(body: dict[str, Any]) -> str:
    last = body["messages"][-1]
    assert last["role"] == "user"
    content = last["content"]
    # Native codecs may merge consecutive users as text or content lists.
    text = content if isinstance(content, str) else content[-1]["text"]
    observation = text.rsplit("\n\n", 1)[-1]
    assert observation.startswith(_MARKER), "budget observation belongs after all history/results/images"
    assert json.dumps(body, ensure_ascii=False).count(_MARKER) == 1
    return observation


def _budget(observation: str) -> tuple[float, float, float]:
    matches = _BUDGET.findall(observation)
    assert len(matches) == 1, "each request needs one current host deadline observation"
    deadline, observed_at, remaining = matches[0]
    return float(deadline), float(observed_at), float(remaining)


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_two_real_turns_refresh_budget_without_mutating_history_or_native_content(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch, protocol: str
) -> None:
    clock = _clock(monkeypatch)
    deadline = clock.now + 600
    limits = RunLimits(deadline, 100_000, 16_000)
    scope = _scope(isolated_workspace)
    command = RoleRunCommand("budget-two-turns", "director", "Inspect the image and source.", isolated_workspace)
    (isolated_workspace / "source.txt").write_text("observed UTF-8: 你好", encoding="utf-8")
    store = EngineeringSessionStore(isolated_workspace)
    store.create_run(command, scope=scope, limits=limits, engine_version="engineering_v2", request_key=None)
    store.append_event(
        command.session_id,
        kind="role_started",
        role="director",
        payload={"instruction": command.instruction, "purpose": "role"},
    )
    original: list[dict[str, Any]] = [
        {"role": "system", "content": role_instruction("director")},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": command.instruction},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _IMAGE}},
            ],
        },
    ]
    before = copy.deepcopy(original)
    for message in original:
        store.append_message(command.session_id, role="director", message=message)
    scripted_provider.enqueue_json(_reply(protocol, read=True))
    scripted_provider.enqueue_json(_reply(protocol))
    client = scripted_provider.client(protocol, isolated_workspace, monkeypatch)
    invoke = client.invoke

    async def advance_after_real_response(request: Any) -> Any:
        response = await invoke(request)
        clock.now += 7.25
        return response

    monkeypatch.setattr(client, "invoke", advance_after_real_response)
    result = await run_engineering_loop(
        command, scope=scope, limits=limits, cancel=asyncio.Event(), store=store, client=client
    )
    assert result.status == "completed" and result.verification == "not_run"
    bodies = [item["body"] for item in scripted_provider.requests]
    assert len(bodies) == 2
    assert all(_system(body, protocol) == before[0]["content"] for body in bodies)
    observations = [_budget(_tail_observation(body)) for body in bodies]
    assert observations[0] == pytest.approx((deadline, deadline - 600, 600), rel=0, abs=0.001)
    assert observations[1] == pytest.approx((deadline, deadline - 592.75, 592.75), rel=0, abs=0.001)
    for body in bodies:
        assert "same run deadline" in _tail_observation(body)
        names = {tool.get("name", tool.get("function", {}).get("name")) for tool in body["tools"]}
        assert names == {"read_file", "repo_tree"}
        assert body["tool_choice"] == ("auto" if protocol == "openai" else {"type": "auto"})
        assert body["max_tokens"] == 512  # Fixture cap, unchanged by the new observation.
        encoded = json.dumps(body, ensure_ascii=False)
        assert _IMAGE in encoded and command.instruction in encoded
    assert "observed UTF-8: 你好" in json.dumps(bodies[1], ensure_ascii=False)
    assert bodies[0]["tools"] == bodies[1]["tools"]
    second = bodies[1]["messages"]
    if protocol == "openai":
        assert [message["role"] for message in second] == ["system", "user", "assistant", "tool", "user"]
        assert bodies[0]["messages"][0] == second[0]
        assert bodies[0]["messages"][1]["content"][:-1] == second[1]["content"]
        assert second[2]["tool_calls"][0]["id"] == second[3]["tool_call_id"] == "inspect"
        assert second[2]["tool_calls"][0]["function"]["name"] == "read_file"
        assert second[1]["content"][-1] == {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64," + _IMAGE},
        }
    else:
        blocks = [(message["role"], block) for message in second for block in message["content"]]
        assert [(role, block["type"]) for role, block in blocks] == [
            ("user", "text"),
            ("user", "image"),
            ("assistant", "tool_use"),
            ("user", "tool_result"),
            ("user", "text"),
        ]
        assert blocks[2][1]["id"] == blocks[3][1]["tool_use_id"] == "inspect"
        assert blocks[2][1]["name"] == "read_file"
        assert blocks[1][1]["source"]["data"] == _IMAGE
        first_content = bodies[0]["messages"][0]["content"]
        assert [block for block in first_content if not block.get("text", "").startswith(_MARKER)] == second[0][
            "content"
        ]
    durable = store.load_message_history(command.session_id, role="director")
    assert original == before and durable[:2] == before
    assert all(_MARKER not in json.dumps(message, ensure_ascii=False) for message in durable)
    saved = store.load_run(command.session_id, workspace=isolated_workspace)
    assert saved["limits"]["deadline"] == limits.deadline
    assert saved["scope"]["enabled_tools"] == ["read_file", "repo_tree"] and saved["scope"]["write_roots"] == []
    # Inspect ContextOS's actual final transport body, not request_prepared prose.
    runtime = Path(resolve_storage_roots(str(isolated_workspace)).runtime_root)
    snapshots = [json.loads(path.read_text(encoding="utf-8")) for path in (runtime / "contexts").glob("*/*")]
    captured = [
        snapshot["provider_request"]["final_physical_request"]["body"]
        for snapshot in snapshots
        if "final_physical_request" in snapshot.get("provider_request", {})
    ]
    assert len(captured) >= 2
    assert all(body in captured for body in bodies)


@pytest.mark.parametrize(
    "role,with_deadline", [("director", False), ("pm", True), ("chief_engineer", True), ("qa", True)]
)
async def test_absent_deadline_and_other_roles_keep_original_system(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    role: Literal["pm", "chief_engineer", "director", "qa"],
    with_deadline: bool,
) -> None:
    command = RoleRunCommand("budget-not-applicable", role, "Inspect source.", isolated_workspace)
    scope = _scope(isolated_workspace)
    limits = RunLimits(time.time() + 600 if with_deadline else None, 100_000, 16_000)
    store = EngineeringSessionStore(isolated_workspace)
    store.create_run(command, scope=scope, limits=limits, engine_version="engineering_v2", request_key=None)
    scripted_provider.enqueue_json(_reply("openai"))
    result = await run_engineering_loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
    )
    assert result.status == "completed"
    body = scripted_provider.requests[0]["body"]
    assert _system(body, "openai") == role_instruction(role)
    assert _MARKER not in json.dumps(body, ensure_ascii=False)


async def test_new_service_resume_uses_saved_deadline_and_fresh_observation(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    clock = _clock(monkeypatch)
    deadline = clock.now + 600
    limits = RunLimits(deadline, 100_000, 16_000)
    scope = _scope(isolated_workspace)
    command = RoleRunCommand("budget-resume", "director", "Inspect source.", isolated_workspace)
    store = EngineeringSessionStore(isolated_workspace)
    store.create_run(command, scope=scope, limits=limits, engine_version="engineering_v2", request_key=None)
    scripted_provider.enqueue_json({"error": "synthetic provider failure"}, status=500)
    client = scripted_provider.client("openai", isolated_workspace, monkeypatch)
    failed = await run_engineering_loop(
        command, scope=scope, limits=limits, cancel=asyncio.Event(), store=store, client=client
    )
    assert failed.status == "failed"
    prior_history = store.load_message_history(command.session_id, role="director")
    clock.now += 125
    scripted_provider.enqueue_json(_reply("openai"))
    # A fresh service/store rehydrates the original persisted run via real resume.
    # This is not a new interpreter or a proof of arbitrary crash recovery.
    resumed = EngineeringRunService(isolated_workspace, client=client, permission_resolver=lambda _row: scope)
    await resumed.resume(command.session_id, workspace=isolated_workspace)
    await asyncio.wait_for(resumed._await_result(command.session_id, asyncio.Event()), timeout=10)
    row = resumed.observe(command.session_id, workspace=isolated_workspace)
    assert row["status"] == "completed" and row["verification"] == "not_run"
    assert row["limits"]["deadline"] == limits.deadline
    last_body = scripted_provider.requests[-1]["body"]
    assert _system(last_body, "openai") == role_instruction("director")
    assert _budget(_tail_observation(last_body)) == pytest.approx((deadline, deadline - 475, 475), rel=0, abs=0.001)
    durable = resumed.store.load_message_history(command.session_id, role="director")
    assert durable[: len(prior_history)] == prior_history
    assert all(_MARKER not in json.dumps(message, ensure_ascii=False) for message in durable)
    assert len(scripted_provider.requests) == 2
    assert len(resumed.store.load_events(command.session_id, kinds=("run_resumed",))) == 1
