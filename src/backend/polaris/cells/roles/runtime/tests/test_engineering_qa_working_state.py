"""QA request-only facts; scripted peers are not convergence or delivery evidence."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Literal

import pytest
from polaris.cells.roles.runtime.internal.engineering_loop import run_engineering_loop
from polaris.cells.roles.runtime.internal.engineering_roles import role_instruction
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    ImageObservation,
    PermissionScope,
    RoleRunCommand,
    RunLimits,
    ToolObservation,
)
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_budget_visibility import _IMAGE, _clock, _reply, _system
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.llm.engine.provider_native_request import (
    build_anthropic_native_messages,
    build_openai_native_messages,
)

MARKER = "Host QA working-state observation (request preparation): "


def _facts(body: dict[str, Any]) -> dict[str, Any]:
    content = body["messages"][-1]["content"]
    text = (
        content
        if isinstance(content, str)
        else "\n".join(block.get("text", "") for block in content if isinstance(block, dict))
    )
    assert MARKER in text, "each QA native body needs one current request-only fact projection"
    assert json.dumps(body, ensure_ascii=False).count(MARKER) == 1
    return json.loads(text.rsplit(MARKER, 1)[1])


def _setup(
    tmp_path: Path, *, deadline: float | None = None, role: Literal["pm", "chief_engineer", "director", "qa"] = "qa"
) -> tuple[RoleRunCommand, PermissionScope, RunLimits, EngineeringSessionStore]:
    workspace = tmp_path / "workspace"
    fs = KernelFileSystem(str(tmp_path), LocalFileSystemAdapter(), read_only_paths=True)
    fs.workspace_mkdir("workspace", parents=True, exist_ok=True)
    fs.workspace_write_text_atomic("workspace/source.txt", "Actual synthetic UTF-8 source 你好", encoding="utf-8")
    command = RoleRunCommand("working-state-fixture", role, "Inspect the synthetic source and image.", workspace)
    scope = PermissionScope((workspace,), (), frozenset({"read_file", "repo_tree"}), False)
    limits = RunLimits(deadline, 100000, 16000)
    store = EngineeringSessionStore(workspace)
    store.create_run(command, scope=scope, limits=limits, engine_version="engineering_v2", request_key=None)
    store.append_event(
        command.session_id,
        kind="role_started",
        role=role,
        payload={"instruction": command.instruction, "purpose": "role"},
    )
    for message in [
        {"role": "system", "content": role_instruction(role)},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": command.instruction},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _IMAGE}},
            ],
        },
    ]:
        store.append_message(command.session_id, role=role, message=message)
    return command, scope, limits, store


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("has_deadline", [False, True])
async def test_native_qa_facts_refresh_once_without_history_or_pixel_changes(
    tmp_path: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    protocol: str,
    has_deadline: bool,
) -> None:
    clock = _clock(monkeypatch)
    deadline = clock.now + 600 if has_deadline else None
    command, scope, limits, store = _setup(tmp_path, deadline=deadline)
    original = store.load_message_history(command.session_id, role="qa")
    scripted_provider.enqueue_json(_reply(protocol, read=True))
    scripted_provider.enqueue_json(_reply(protocol))
    client = scripted_provider.client(protocol, command.workspace, monkeypatch)
    native_invoke = client.invoke

    async def advance(request: Any) -> Any:
        result = await native_invoke(request)
        clock.now += 7.25
        return result

    monkeypatch.setattr(client, "invoke", advance)
    result = await run_engineering_loop(
        command, scope=scope, limits=limits, cancel=asyncio.Event(), store=store, client=client
    )
    assert result.status == "completed"
    bodies = [entry["body"] for entry in scripted_provider.requests]
    assert len(bodies) == 2
    facts = [_facts(body) for body in bodies]
    assert [value["qa_frame"]["successful_source_read_count"] for value in facts] == [0, 1]
    assert facts[1]["last_probe"]["exact_arguments_sha256"] == hashlib.sha256(b'{"file":"source.txt"}').hexdigest()
    actual_read = store.load_events(command.session_id, kinds=("tool_observed",), role="qa")[0]["payload"]
    assert (
        facts[1]["last_probe"]["result_content_sha256"]
        == hashlib.sha256(actual_read["content"].encode("utf-8")).hexdigest()
    )
    assert "Actual synthetic UTF-8 source" not in json.dumps(facts[1], ensure_ascii=False)
    assert "arguments" not in facts[1]["last_probe"] and "result" not in facts[1]["last_probe"]
    assert facts[1]["observed_at_epoch_seconds"] - facts[0]["observed_at_epoch_seconds"] == pytest.approx(7.25)
    for index, (body, value) in enumerate(zip(bodies, facts, strict=True)):
        assert value["original_deadline_epoch_seconds"] == deadline
        assert value["approximate_remaining_seconds"] == (pytest.approx(600 - index * 7.25) if has_deadline else None)
        assert value["run_id"] == command.session_id
        assert value["workspace"] == str(command.workspace)
        assert value["source_candidate"]["workspace_revision"] == 0
        assert value["source_candidate"]["verification"] == "not_run"
        assert value["qa_frame"]["own_browser_capture_count"] == 0
        assert value["qa_frame"]["report_review_count"] == 0
        assert _system(body, protocol) == original[0]["content"]
        assert _IMAGE in json.dumps(body)
        assert body["max_tokens"] == 512
        assert body["tool_choice"] == ("auto" if protocol == "openai" else {"type": "auto"})
    assert bodies[0]["tools"] == bodies[1]["tools"]
    assert '"inspect"' in json.dumps(bodies[1])
    assert (
        "tool_result" in json.dumps(bodies[1]) if protocol == "anthropic" else "tool_call_id" in json.dumps(bodies[1])
    )
    durable = store.load_message_history(command.session_id, role="qa")
    assert durable[:2] == original
    assert all(MARKER not in json.dumps(message, ensure_ascii=False) for message in durable)
    prepared = store.load_events(command.session_id, kinds=("request_prepared",), role="qa")
    assert all(
        sum(MARKER in str(m.get("content")) for m in e["payload"]["request"]["options"]["messages"]) == 1
        for e in prepared
    )
    assert prepared[0]["payload"]["request"]["options"]["messages"][:-1] == original
    assert prepared[1]["payload"]["request"]["options"]["messages"][:-1] == durable[:-1]
    for body, event in zip(bodies, prepared, strict=True):
        canonical = event["payload"]["request"]["options"]["messages"]
        if protocol == "anthropic":
            expected_messages, expected_system = build_anthropic_native_messages(canonical)
            assert body["messages"] == expected_messages
            assert body["system"] == expected_system
        else:
            assert body["messages"] == build_openai_native_messages(canonical)
        assert json.dumps(body).count(_IMAGE) == 1


class SimulatedProcessLoss(BaseException):
    """Test-only process-loss boundary after durable read, before another send."""


async def test_same_sqlite_cold_reopen_retains_frame_and_refreshes_native_facts(
    tmp_path: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _clock(monkeypatch)
    command, scope, limits, store = _setup(tmp_path, deadline=clock.now + 600)
    scripted_provider.enqueue_json(_reply("openai", read=True))
    scripted_provider.enqueue_json(_reply("openai"))
    client = scripted_provider.client("openai", command.workspace, monkeypatch)
    native_invoke = client.invoke
    invocations = 0

    async def lose_process(request: Any) -> Any:
        nonlocal invocations
        invocations += 1
        if invocations == 2:
            raise SimulatedProcessLoss
        return await native_invoke(request)

    monkeypatch.setattr(client, "invoke", lose_process)
    with pytest.raises(SimulatedProcessLoss):
        await run_engineering_loop(
            command, scope=scope, limits=limits, cancel=asyncio.Event(), store=store, client=client
        )
    history = store.load_message_history(command.session_id, role="qa")
    starts = store.load_events(command.session_id, kinds=("role_started",), role="qa")
    assert len(starts) == 1 and not store.pending_tool_calls(command.session_id)
    clock.now += 50
    reopened = EngineeringSessionStore(command.workspace)
    restored_client = scripted_provider.client("openai", command.workspace, monkeypatch)
    result = await run_engineering_loop(
        command, scope=scope, limits=limits, cancel=asyncio.Event(), store=reopened, client=restored_client
    )
    assert result.status == "completed"
    assert len(scripted_provider.requests) == 2
    facts = _facts(scripted_provider.requests[-1]["body"])
    assert facts["qa_frame"]["started_seq"] == starts[0]["seq"]
    assert facts["qa_frame"]["successful_source_read_count"] == 1
    assert facts["original_deadline_epoch_seconds"] == limits.deadline
    assert facts["approximate_remaining_seconds"] == pytest.approx(550)
    assert facts["last_probe"]["exact_arguments_sha256"] == hashlib.sha256(b'{"file":"source.txt"}').hexdigest()
    assert reopened.load_message_history(command.session_id, role="qa")[: len(history)] == history
    assert len(reopened.load_events(command.session_id, kinds=("role_started",), role="qa")) == 1
    assert all(MARKER not in json.dumps(m) for m in reopened.load_message_history(command.session_id))


def test_journal_scope_success_candidate_and_exact_visible_result_counts(tmp_path: Path) -> None:
    from polaris.cells.roles.runtime.internal.engineering_working_state import qa_working_state_message

    command, _scope, limits, store = _setup(tmp_path)

    def observed(
        identity: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        ok: bool = True,
        role: Literal["pm", "chief_engineer", "director", "qa"] = "qa",
        content: str = "same",
        interrupted: bool = False,
        timed_out: bool = False,
        image: bool = False,
    ) -> None:
        turn = store.append_event(command.session_id, kind="turn_started", role=role, payload={})
        store.record_tool_dispatch(
            command.session_id, turn_seq=turn, call_id=identity, tool=tool, arguments=arguments, role=role
        )
        images = (
            (
                ImageObservation(
                    "image/png",
                    _IMAGE,
                    1,
                    1,
                    hashlib.sha256(base64.b64decode(_IMAGE)).hexdigest(),
                    "Synthetic journal capture",
                ),
            )
            if image
            else ()
        )
        store.record_tool_observation(
            command.session_id,
            turn_seq=turn,
            observation=ToolObservation(
                identity, tool, ok, content, None, interrupted, images=images, timed_out=timed_out
            ),
            role=role,
        )

    observed("old-frame", "read_file", {"file": "old"})
    store.append_event(command.session_id, kind="role_finished", role="qa", payload={"status": "completed"})
    frame = store.append_event(
        command.session_id,
        kind="role_started",
        role="qa",
        payload={"instruction": command.instruction, "purpose": "role"},
    )
    observed("old-candidate", "read_file", {"file": "old"})
    store.update_run(command.session_id, workspace_revision=1, verification="not_run")
    observed("foreign", "read_file", {"file": "other"}, role="director")
    observed("read-ok", "read_file", {"file": "source.txt"})
    observed("read-failed", "read_file", {"file": "bad"}, ok=False)
    observed("read-interrupted", "read_file", {"file": "partial"}, interrupted=True)
    observed("read-timeout", "read_file", {"file": "timeout"}, timed_out=True)
    observed("capture", "browser_observe", {"entry": "synthetic.html"}, image=True)
    observed("capture-failed", "browser_observe", {"entry": "bad.html"}, ok=False, image=True)
    observed("revisit", "browser_observe", {"revisit_call_ids": ["capture"]}, image=True)
    observed("report-failed", "report_review", {"approved": False, "feedback": "incomplete"}, ok=False)
    for identity, command_text in [("a", "python a"), ("b", "python b"), ("c", "python b")]:
        observed(identity, "execute_command", {"command": command_text})
    turn = store.append_event(command.session_id, kind="turn_started", role="qa", payload={})
    store.record_tool_dispatch(
        command.session_id,
        turn_seq=turn,
        call_id="unknown-effect",
        tool="execute_command",
        arguments={"command": "unknown"},
        role="qa",
    )

    def project() -> dict[str, Any]:
        message = qa_working_state_message(
            store.load_run(command.session_id, workspace=command.workspace),
            store.load_events(command.session_id),
            frame_seq=frame,
            observed_at=time.time(),
            deadline=limits.deadline,
        )
        return json.loads(message["content"].removeprefix(MARKER))

    value = project()
    assert value["qa_frame"]["successful_source_read_count"] == 1
    assert value["qa_frame"]["own_browser_capture_count"] == 1
    assert value["qa_frame"]["report_review_count"] == 1
    assert value["qa_frame"]["successful_report_review_count"] == 0
    assert value["qa_frame"]["unobserved_tool_dispatch_count"] == 1
    assert value["last_probe"]["exact_arguments_sha256"] == hashlib.sha256(b'{"command":"python b"}').hexdigest()
    assert "arguments" not in value["last_probe"] and "result" not in value["last_probe"]
    assert value["last_probe"]["exact_same_arguments_and_result_count"] == 2
    assert value["last_probe"]["same_tool_and_visible_result_count"] == 3
    assert "does not imply" in value["visible_result_count_scope"]
    assert "fields only" in value["visible_result_count_scope"]
    assert "image equality not inferred" in value["visible_result_count_scope"]
    assert value["approximate_remaining_seconds"] is None
    store.update_run(command.session_id, workspace_revision=2, verification="not_run")
    reset = project()
    assert reset["source_candidate"]["workspace_revision"] == 2
    assert reset["last_probe"] is None
    assert reset["qa_frame"]["successful_source_read_count"] == 0
    # Incomplete candidate provenance stays unknown instead of inventing zero
    # successful observations or importing a different revision's successes.
    unknown = qa_working_state_message(
        store.load_run(command.session_id, workspace=command.workspace),
        [e for e in store.load_events(command.session_id) if e["kind"] != "run_updated"],
        frame_seq=frame,
        observed_at=time.time(),
        deadline=None,
    )
    missing_binding = json.loads(unknown["content"].removeprefix(MARKER))
    assert missing_binding["source_candidate"]["candidate_binding_known"] is False
    assert missing_binding["qa_frame"]["successful_source_read_count"] is None
    assert missing_binding["qa_frame"]["own_browser_capture_count"] is None


@pytest.mark.parametrize("role", ["pm", "chief_engineer", "director"])
async def test_non_qa_native_request_does_not_receive_qa_facts(
    tmp_path: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    role: Literal["pm", "chief_engineer", "director", "qa"],
) -> None:
    command, scope, limits, store = _setup(tmp_path, role=role)
    scripted_provider.enqueue_json(_reply("openai"))
    result = await run_engineering_loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client("openai", command.workspace, monkeypatch),
    )
    assert result.status == "completed"
    assert MARKER not in json.dumps(scripted_provider.requests[-1]["body"])
