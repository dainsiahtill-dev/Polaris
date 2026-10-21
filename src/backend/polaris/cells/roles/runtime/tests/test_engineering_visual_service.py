"""Native visual observation uses the same owner, grants and acceptance facts."""

from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    BrowserObservationConfig,
    ImageObservation,
    PermissionScope,
    RoleRunCommand,
    RoleRunResult,
    RunLimits,
    ToolObservation,
)
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.kernelone.llm.engine.client import ResilientLLMClient
from polaris.kernelone.process.process_tree import ProcessTreeDrainError

_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6VRAAAAAASUVORK5CYII="
_HASH = "76082f2d5036598b4b110a39655d79d45263bf8e9283464accac587b9f6d838e"


def _scope(workspace: Path, *, required: bool = True) -> PermissionScope:
    return PermissionScope(
        (workspace,),
        (workspace,),
        frozenset({"read_file", "browser_observe", "write_file", "execute_command", "verify_project", "report_review"}),
        False,
        BrowserObservationConfig("/usr/bin/true", "/usr/bin/python3", (), required),
    )


def _image() -> ImageObservation:
    return ImageObservation("image/png", _PNG, 1, 1, _HASH, "synthetic fixture frame")


def _seed(service: EngineeringRunService, scope: PermissionScope) -> tuple[RoleRunCommand, RunLimits]:
    limits = RunLimits(time.time() + 30, 1_000_000, 64000)
    command = RoleRunCommand("visual-service", "director", "Inspect the actual current page.", service.workspace)
    service.store.create_run(
        command, scope=scope, limits=limits, engine_version=service.engine_version, request_key=None
    )
    return command, limits


def test_saved_browser_scope_survives_cold_service_reconstruction(isolated_workspace: Path) -> None:
    scope = _scope(isolated_workspace)
    service = EngineeringRunService(isolated_workspace)
    command, _limits = _seed(service, scope)
    cold = EngineeringRunService(isolated_workspace)
    assert cold._scope(cold.store.load_run(command.session_id, workspace=isolated_workspace)) == scope


@pytest.mark.parametrize("change", ["removed", "driver", "python", "paths", "requirement"])
def test_browser_host_identity_cannot_silently_change_on_recovery(isolated_workspace: Path, change: str) -> None:
    saved = _scope(isolated_workspace)
    browser = saved.browser
    assert browser is not None
    changed = {
        "removed": None,
        "driver": replace(browser, executable="/usr/bin/false"),
        "python": replace(browser, python_executable="/usr/bin/python3.12"),
        "paths": replace(browser, python_paths=("/usr/local/lib",)),
        "requirement": replace(browser, require_visual_review=False),
    }[change]
    assert not EngineeringRunService._scope_within(saved, replace(saved, browser=changed))


def test_verifier_preserves_browser_identity_without_adding_tool_grants(isolated_workspace: Path) -> None:
    scope = _scope(isolated_workspace)
    service = EngineeringRunService(isolated_workspace)
    _seed(service, scope)
    verifier = EngineeringVerification(
        isolated_workspace,
        store=service.store,
        scope=scope,
        limits=RunLimits(None, 100000, 20000),
        cancel=asyncio.Event(),
    )
    assert verifier.tools.scope.browser == scope.browser
    assert verifier.tools.scope.enabled_tools == frozenset({"execute_command"})


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["director", "qa"])
async def test_capture_dispatch_records_actual_binding_and_uses_readonly_role_scope(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    scope = replace(_scope(isolated_workspace), network_allowed=True)
    service = EngineeringRunService(isolated_workspace, permission_resolver=lambda _row: scope)
    command, limits = _seed(service, scope)
    seen: list[PermissionScope] = []

    async def process(self: EngineeringTools, argv: list[str], *, timeout: float, writes: tuple[Path, ...]):
        seen.append(self.scope)
        assert writes == ()
        assert timeout == 1
        return subprocess.CompletedProcess(argv, 0, "", "")

    async def capture(
        call_id: str, arguments: dict[str, Any], *, workspace: Path, scope: PermissionScope, run_process: Any
    ) -> ToolObservation:
        assert workspace == isolated_workspace
        assert scope.write_roots == () and scope.network_allowed is False
        assert scope.browser == _scope(workspace).browser
        await run_process(["/usr/bin/true"], 1)
        return ToolObservation(call_id, "browser_observe", True, "{}", 0, False, images=(_image(),))

    monkeypatch.setattr(EngineeringTools, "_process", process)
    monkeypatch.setattr("polaris.cells.roles.runtime.internal.engineering_browser.capture_browser_observation", capture)
    owner = service._owner.set((command, scope, limits, asyncio.Event(), cast(ResilientLLMClient, None)))
    active = service._active_role.set(role)
    try:
        result = await service._observe_dispatch({"entry": "index.html"}, 12, "capture")
    finally:
        service._active_role.reset(active)
        service._owner.reset(owner)
    assert result.ok and result.images == (_image(),)
    assert len(seen) == 1 and seen[0].read_roots == scope.read_roots
    event = service.store.load_events(command.session_id)[-1]
    assert event["kind"] == "visual_observation_completed" and event["role"] == role
    assert event["payload"]["requested_by"] == [12, "capture"]
    assert event["payload"]["image_sha256"] == [_HASH]
    assert event["payload"]["input_binding"]["workspace_revision"] == 0


@pytest.mark.asyncio
async def test_capture_source_drift_discards_images_instead_of_approving_stale_candidate(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope(isolated_workspace)
    service = EngineeringRunService(isolated_workspace, permission_resolver=lambda _row: scope)
    command, limits = _seed(service, scope)

    async def capture(call_id: str, *_args: Any, **_kwargs: Any) -> ToolObservation:
        (isolated_workspace / "outside-edit.txt").write_text("concurrent fixture change", encoding="utf-8")
        return ToolObservation(call_id, "browser_observe", True, "{}", 0, False, images=(_image(),))

    monkeypatch.setattr("polaris.cells.roles.runtime.internal.engineering_browser.capture_browser_observation", capture)
    owner = service._owner.set((command, scope, limits, asyncio.Event(), cast(ResilientLLMClient, None)))
    active = service._active_role.set("director")
    try:
        result = await service._observe_dispatch({"entry": "index.html"}, 12, "capture")
    finally:
        service._active_role.reset(active)
        service._owner.reset(owner)
    assert result.ok is False and not result.images
    assert "changed" in result.content.lower()
    event = service.store.load_events(command.session_id)[-1]
    assert event["payload"]["ok"] is False and event["payload"]["image_sha256"] == []


@pytest.mark.asyncio
async def test_unknown_capture_drain_keeps_dispatch_pending_without_replay(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = _scope(isolated_workspace)
    service = EngineeringRunService(isolated_workspace)
    command, limits = _seed(service, scope)
    calls = 0

    async def capture(*_args: Any, **_kwargs: Any) -> ToolObservation:
        nonlocal calls
        calls += 1
        raise ProcessTreeDrainError("synthetic unknown browser process")

    monkeypatch.setattr("polaris.cells.roles.runtime.internal.engineering_browser.capture_browser_observation", capture)
    turn = service.store.append_event(command.session_id, kind="turn_started", role="director", payload={})
    service.store.record_tool_dispatch(
        command.session_id,
        turn_seq=turn,
        call_id="unknown",
        tool="browser_observe",
        arguments={"entry": "index.html"},
        role="director",
    )
    owner = service._owner.set((command, scope, limits, asyncio.Event(), cast(ResilientLLMClient, None)))
    active = service._active_role.set("director")
    try:
        with pytest.raises(ProcessTreeDrainError):
            await service._observe_dispatch({"entry": "index.html"}, turn, "unknown")
    finally:
        service._active_role.reset(active)
        service._owner.reset(owner)
    assert calls == 1
    assert len(service.store.pending_tool_calls(command.session_id)) == 1


def _text(value: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": value}, "finish_reason": "stop"}]}


def _calls(*values: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
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
                            "function": {"name": tool, "arguments": json.dumps(arguments)},
                        }
                        for identity, tool, arguments in values
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


@pytest.mark.asyncio
async def test_native_owner_repairs_missing_visual_input_and_accepts_only_independent_current_qa_images(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the model and image capture are synthetic; HTTP, tools and owner stay real."""
    (isolated_workspace / "index.html").write_text("<html><body>fixture</body></html>", encoding="utf-8")
    scope = _scope(isolated_workspace)
    scripted_provider.client("openai", isolated_workspace, monkeypatch)
    captures: list[str] = []

    async def capture(call_id: str, *_args: Any, **_kwargs: Any) -> ToolObservation:
        captures.append(call_id)
        return ToolObservation(call_id, "browser_observe", True, "{}", 0, False, images=(_image(),))

    monkeypatch.setattr("polaris.cells.roles.runtime.internal.engineering_browser.capture_browser_observation", capture)
    service = EngineeringRunService(isolated_workspace, permission_resolver=lambda _row: scope, require_qa=True)
    command = RoleRunCommand(
        "native-visual-service",
        "director",
        "Deliver the current HTML page.",
        isolated_workspace,
        required_checks=(
            "python3 -B -c \"from pathlib import Path; assert 'fixture' in Path('index.html').read_text(encoding='utf-8')\"",
        ),
    )
    # A text-only completion must return feedback to the same Director before
    # host automatic verification/acceptance can take a shortcut.
    scripted_provider.enqueue_json(_text("Done, without looking."))
    scripted_provider.enqueue_json(_calls(("director-capture", "browser_observe", {"entry": "index.html"})))
    scripted_provider.enqueue_json(_text("Observed the current frame; ready for required checks."))
    # Same-batch capture plus approval must fail first; later consumption may pass.
    scripted_provider.enqueue_json(_calls(("qa-read", "read_file", {"file": "index.html"})))
    scripted_provider.enqueue_json(
        _calls(
            ("qa-capture", "browser_observe", {"entry": "index.html"}),
            ("early-approval", "report_review", {"approved": True, "feedback": "Premature"}),
        )
    )
    scripted_provider.enqueue_json(
        _calls(
            ("current-approval", "report_review", {"approved": True, "feedback": "Current independent image consumed."})
        )
    )
    limits = RunLimits(time.time() + 45, 1_000_000, 64000)
    await service.start(command, scope=scope, limits=limits)
    await service.wait(command.session_id, workspace=isolated_workspace)
    row = service.snapshot(command.session_id, workspace=isolated_workspace)
    assert row["project_outcome"] == "accepted", row
    assert row["verification"] == "passed" and row["qa_verdict"] == "approved"
    assert captures == ["director-capture", "qa-capture"]
    events = service.store.load_events(command.session_id)
    early = next(
        event
        for event in events
        if event["kind"] == "tool_observed" and event["payload"].get("call_id") == "early-approval"
    )
    assert early["payload"]["ok"] is False
    assert {event["role"] for event in events if event["kind"] == "visual_observation_completed"} == {"director", "qa"}
    assert not any(event["role"] in {"pm", "chief_engineer"} for event in events)
    for index in (2, 5):
        content = scripted_provider.requests[index]["body"]["messages"][-1]["content"]
        block = next(item for item in content if item["type"] == "image_url")
        assert base64.b64decode(block["image_url"]["url"].split(",", 1)[1]) == base64.b64decode(_PNG)
    cold = EngineeringRunService(isolated_workspace, permission_resolver=lambda _row: scope, require_qa=True)
    assert cold.snapshot(command.session_id, workspace=isolated_workspace)["project_outcome"] == "accepted"
    closed_host = EngineeringRunService(
        isolated_workspace, permission_resolver=lambda _row: replace(scope, browser=None), require_qa=True
    )
    assert closed_host.snapshot(command.session_id, workspace=isolated_workspace)["project_outcome"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("approved,expected", [(True, "unknown"), (False, "rejected")])
async def test_final_qa_owner_cannot_be_bypassed_by_an_adapter_without_actual_image_input(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, approved: bool, expected: str
) -> None:
    scope = _scope(isolated_workspace)
    service = EngineeringRunService(isolated_workspace, permission_resolver=lambda _row: scope, require_qa=True)
    check = "python3 -B -c 'assert 2 + 2 == 4'"
    command = RoleRunCommand(
        "qa-owner-boundary", "director", "Inspect current output.", isolated_workspace, required_checks=(check,)
    )
    limits = RunLimits(time.time() + 30, 100000, 20000)
    cancel = asyncio.Event()
    service.store.create_run(
        command, scope=scope, limits=limits, engine_version=service.engine_version, request_key=None, require_qa=True
    )
    verifier = service._verification(scope, limits, cancel)
    with service.store.acquire_workspace(isolated_workspace):
        checks = await verifier.run_verification((check,), session_id=command.session_id, workspace_revision=0)
    assert checks[0].ok

    async def adapter_without_images(*_args: Any, **_kwargs: Any) -> RoleRunResult:
        frame = service.store.append_event(command.session_id, kind="role_started", role="qa", payload={})
        turn = service.store.append_event(command.session_id, kind="turn_started", role="qa", payload={})
        service.store.record_tool_dispatch(
            command.session_id,
            turn_seq=turn,
            call_id="adapter-report",
            tool="report_review",
            arguments={"approved": approved},
            role="qa",
        )
        service.store.record_tool_observation(
            command.session_id,
            turn_seq=turn,
            role="qa",
            observation=ToolObservation(
                "adapter-report",
                "report_review",
                True,
                json.dumps({"approved": approved, "feedback": "Adapter judgment"}),
                None,
                False,
            ),
        )
        service.store.append_event(
            command.session_id, kind="role_finished", role="qa", payload={"frame_seq": frame, "status": "completed"}
        )
        return RoleRunResult(command.session_id, "qa", "Adapter judgment", "completed", "not_run", ())

    # Replace only the role adapter. The service still reads real durable
    # report facts and real verification inputs, then enforces its own boundary.
    monkeypatch.setattr(service, "run_role", adapter_without_images)
    verdict, _feedback = await service._review_qa(command, scope=scope, limits=limits, cancel=cancel, verifier=verifier)
    assert verdict == expected
    final = service.store.load_events(command.session_id)[-1]
    assert final["kind"] == "qa_review_completed" and final["payload"]["verdict"] == expected


@pytest.mark.asyncio
async def test_nonvisual_verification_does_not_read_visual_candidate_bindings(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = replace(_scope(isolated_workspace), browser=None)
    service = EngineeringRunService(isolated_workspace)
    command, limits = _seed(service, scope)
    cancel = asyncio.Event()
    verifier = service._verification(scope, limits, cancel)

    def unexpected_visual_binding(*_args: Any) -> dict[str, Any]:
        raise AssertionError("Disabled visual observation cannot introduce an extra candidate scan")

    monkeypatch.setattr(verifier, "review_binding", unexpected_visual_binding)
    monkeypatch.setattr(service, "_verification", lambda *_args, **_kwargs: verifier)
    turn = service.store.append_event(command.session_id, kind="turn_started", role="director", payload={})
    service.store.record_tool_dispatch(
        command.session_id,
        turn_seq=turn,
        call_id="plain-verify",
        tool="verify_project",
        arguments={"commands": ["python3 -B -c 'assert 2 + 2 == 4'"]},
        role="director",
    )
    owner = service._owner.set((command, scope, limits, cancel, cast(ResilientLLMClient, None)))
    active = service._active_role.set("director")
    try:
        result = await service._verify_dispatch(("python3 -B -c 'assert 2 + 2 == 4'",), turn, "plain-verify")
    finally:
        service._active_role.reset(active)
        service._owner.reset(owner)
    assert result.ok
    service.store.record_tool_observation(command.session_id, turn_seq=turn, observation=result, role="director")
    assert not service.store.pending_tool_calls(command.session_id)
