"""Model-selected checks use real sandbox execution and the existing journal.

Only the remote model is replaced by scripted native HTTP responses. These
fixtures do not prove remote-model judgment or a delivered Bench project.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)


def _response(*calls: tuple[str, str, dict[str, Any]], text: str = "Ready.") -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = [
            {"id": identity, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
            for identity, name, arguments in calls
        ]
    return {"choices": [{"message": message, "finish_reason": "tool_calls" if calls else "stop"}]}


def _scope(workspace: Path) -> PermissionScope:
    return PermissionScope(
        (workspace,),
        (workspace,),
        frozenset({"read_file", "write_file", "execute_command", "verify_project", "report_review"}),
        False,
    )


def _service(workspace: Path, provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch) -> EngineeringRunService:
    provider.client("openai", workspace, monkeypatch)
    return EngineeringRunService(workspace, require_qa=True, permission_resolver=lambda _row: _scope(workspace))


async def test_goal_only_model_check_runs_once_then_current_independent_qa(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing the native verification path must lose genuine acceptance."""
    (isolated_workspace / "artifact.txt").write_text("2", encoding="utf-8")
    command_text = (
        'python3 -c "from pathlib import Path; '
        "assert Path('artifact.txt').read_text(encoding='utf-8') == '2'; print('CHECKED_ARTIFACT2')\""
    )
    for response in (
        _response(("verify", "verify_project", {"commands": [command_text]})),
        _response(text="Actual check succeeded; independently review the artifact."),
        _response(("read", "read_file", {"file": "artifact.txt"})),
        _response(("review", "report_review", {"approved": True, "feedback": "Read actual value2 and check output."})),
    ):
        scripted_provider.enqueue_json(response)
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    command = RoleRunCommand("goal-only", "director", "Deliver the artifact with value2.", isolated_workspace)
    await service.start(
        command, scope=_scope(isolated_workspace), limits=RunLimits(time.time() + 45, 1_000_000, 64_000)
    )
    await service.wait(command.session_id, workspace=isolated_workspace)
    row = service.snapshot(command.session_id, workspace=isolated_workspace)

    assert row["project_outcome"] == "accepted", row
    assert row["verification"] == "passed" and row["qa_verdict"] == "approved"
    assert row["required_checks"] == []
    assert row["verification_commands"] == [command_text]
    assert len(row["checks"]) == 1 and row["checks"][0]["exit_code"] == 0
    assert "CHECKED_ARTIFACT2" in row["checks"][0]["content"]
    events = service.store.load_events(command.session_id)
    child_dispatches = [
        event for event in events if event["kind"] == "tool_started" and event["payload"]["tool"] == "execute_command"
    ]
    assert len(child_dispatches) == 1  # Finalization must reuse the actual current verification.
    assert len([event for event in events if event["kind"] == "verification_completed"]) == 1
    assert [event["role"] for event in events if event["kind"] == "role_started"] == ["director", "qa"]
    qa_request = scripted_provider.requests[2]["body"]
    assert json.dumps(command_text, ensure_ascii=False) in qa_request["messages"][1]["content"]
    cold = _service(isolated_workspace, scripted_provider, monkeypatch).snapshot(
        command.session_id, workspace=isolated_workspace
    )
    assert cold["project_outcome"] == "accepted"
    assert cold["required_checks"] == [] and cold["verification_commands"] == [command_text]


async def test_text_completion_without_checks_returns_feedback_to_same_director(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finishing a reply must not end an authorized goal before real verification."""
    (isolated_workspace / "artifact.txt").write_text("2", encoding="utf-8")
    actual = "python3 -c \"from pathlib import Path; assert Path('artifact.txt').read_text(encoding='utf-8')=='2'\""
    for response in (
        _response(text="Implementation is done."),
        _response(("verify-later", "verify_project", {"commands": [actual]})),
        _response(),
        _response(("read-later", "read_file", {"file": "artifact.txt"})),
        _response(("review-later", "report_review", {"approved": True, "feedback": "Read actual artifact2."})),
    ):
        scripted_provider.enqueue_json(response)
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    command = RoleRunCommand("not-just-text", "director", "Deliver and verify artifact2.", isolated_workspace)
    await service.start(
        command, scope=_scope(isolated_workspace), limits=RunLimits(time.time() + 45, 1_000_000, 64_000)
    )
    await service.wait(command.session_id, workspace=isolated_workspace)
    row = service.snapshot(command.session_id, workspace=isolated_workspace)
    assert row["project_outcome"] == "accepted", row
    assert "No current project verification" in json.dumps(scripted_provider.requests[1]["body"], ensure_ascii=False)
    roles = [
        event["role"] for event in service.store.load_events(command.session_id) if event["kind"] == "role_started"
    ]
    assert roles == ["director", "director", "qa"]


async def test_cold_read_preserves_qa_binding_from_before_optional_check_selection(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replace only the prior QA binding serialization; real checks and review remain.

    This fixture proves compatibility of the old binding shape, not execution
    under an old remote provider or the qualification of a historical project.
    """
    from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification

    (isolated_workspace / "artifact.txt").write_text("2", encoding="utf-8")
    actual = "python3 -c \"from pathlib import Path; assert Path('artifact.txt').read_text(encoding='utf-8')=='2'\""
    for response in (
        _response(),
        _response(("legacy-read", "read_file", {"file": "artifact.txt"})),
        _response(("legacy-review", "report_review", {"approved": True, "feedback": "Read current artifact2."})),
    ):
        scripted_provider.enqueue_json(response)
    original = EngineeringVerification.review_binding

    def legacy_binding(verifier: EngineeringVerification, session_id: str) -> dict[str, object]:
        binding = original(verifier, session_id)
        binding.pop("verification_commands", None)
        return binding

    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    command = RoleRunCommand(
        "legacy-binding", "director", "Deliver artifact2.", isolated_workspace, required_checks=(actual,)
    )
    with monkeypatch.context() as legacy:
        legacy.setattr(EngineeringVerification, "review_binding", legacy_binding)
        await service.start(
            command, scope=_scope(isolated_workspace), limits=RunLimits(time.time() + 45, 1_000_000, 64_000)
        )
        await service.wait(command.session_id, workspace=isolated_workspace)
        assert service.snapshot(command.session_id, workspace=isolated_workspace)["project_outcome"] == "accepted"
    history = service.store.load_events(command.session_id)
    cold = _service(isolated_workspace, scripted_provider, monkeypatch)
    row = cold.snapshot(command.session_id, workspace=isolated_workspace)
    assert row["project_outcome"] == "accepted" and row["qa_verdict"] == "approved", row
    assert cold.store.load_events(command.session_id) == history


def test_live_selected_command_labels_are_complete_beyond_preview_list_limit() -> None:
    """A shortened command list must never label a complete authoritative check list."""
    from polaris.delivery.ws.engineering_events import compact_engineering_payload

    commands = [f"check-{index}" for index in range(80)]
    checks = [
        {"call_id": str(index), "tool": "execute_command", "ok": True, "content": "ok", "exit_code": 0}
        for index in range(80)
    ]
    result = compact_engineering_payload(
        "verification_completed",
        {"verification": "passed", "required_checks": [], "verification_commands": commands, "checks": checks},
    )
    assert result["verification_commands"] == commands
    assert len(result["checks"]) == 80


def test_live_oversize_selected_command_requires_complete_snapshot() -> None:
    """An unrepresentable actual command cannot become a short passing preview."""
    from polaris.delivery.ws.engineering_events import compact_engineering_payload

    result = compact_engineering_payload(
        "verification_completed", {"verification": "passed", "verification_commands": ["x" * 65_000], "checks": []}
    )
    assert result.get("snapshot_required") is True
    assert "verification" not in result and "verification_commands" not in result


def test_acceptance_observer_consumes_selected_checks_without_rewriting_user_checks(isolated_workspace: Path) -> None:
    """The internal observer must understand a real result beyond an empty user recipe.

    This is synthetic projection input, not authoritative delivery evidence.
    """
    from polaris.delivery.cli.engineering_acceptance import _observed_result

    class NoHTTP:
        def request(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("Synthetic projection has no materialized provider request.")

        def close(self) -> None:
            pass

    (isolated_workspace / "artifact.txt").write_text("2", encoding="utf-8")
    binding = {
        "plan": {
            "workspace": str(isolated_workspace),
            "evidence_dir": str(isolated_workspace / "audit"),
            "required_checks": [],
        },
        "run_id": "fixture",
        "started_at_epoch": time.time(),
    }
    snapshot = {
        "engine_version": "engineering_v2",
        "workspace": str(isolated_workspace),
        "session_id": "fixture",
        "run_id": "fixture",
        "required_checks": [],
        "verification_commands": ["python3 check.py"],
        "checks": [
            {
                "call_id": "fixture-check",
                "tool": "execute_command",
                "content": "fixture result",
                "ok": True,
                "exit_code": 0,
                "interrupted": False,
            }
        ],
        "status": "completed",
        "verification": "passed",
        "require_qa": True,
        "qa_verdict": "approved",
        "project_outcome": "accepted",
        "acceptance_basis": "independent_qa",
        "events": [],
        "last_seq": 1,
    }
    result = _observed_result(binding, snapshot, NoHTTP(), time.time())
    assert result["harness_status"] == "OBSERVED_ACCEPTED", result
    assert result["platform"]["verification_commands"] == ["python3 check.py"]
    assert result["platform"]["required_checks"] == []


async def test_default_host_profile_can_verify_a_plain_goal(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default new grants must actually expose verification, rather than registry-only support."""
    from polaris.bootstrap.config import Settings
    from polaris.bootstrap.engineering_runtime import resolve_engineering_profile

    (isolated_workspace / "artifact.txt").write_text("2", encoding="utf-8")
    settings = Settings(workspace=isolated_workspace)
    scope = resolve_engineering_profile(settings, isolated_workspace, "workspace")
    scripted_provider.client("openai", isolated_workspace, monkeypatch)
    service = EngineeringRunService(isolated_workspace, permission_resolver=lambda _row: scope)
    for response in (
        _response(("default-verify", "verify_project", {"commands": ['test "$(cat artifact.txt)" = 2']})),
        _response(),
    ):
        scripted_provider.enqueue_json(response)
    command = RoleRunCommand("default-goal", "director", "Deliver and check value2.", isolated_workspace)
    await service.start(command, scope=scope, limits=RunLimits(time.time() + 45, 1_000_000, 64_000))
    await service.wait(command.session_id, workspace=isolated_workspace)
    row = service.snapshot(command.session_id, workspace=isolated_workspace)
    assert row["project_outcome"] == "accepted", row
    assert row["required_checks"] == []
    assert row["verification"] == "passed" and row["acceptance_basis"] == "checks_only"


@pytest.mark.parametrize(
    ("role", "enabled"),
    [
        ("director", frozenset()),
        ("director", frozenset({"verify_project"})),
        ("director", frozenset({"execute_command"})),
        ("pm", frozenset({"verify_project", "execute_command"})),
        ("chief_engineer", frozenset({"verify_project", "execute_command"})),
        ("qa", frozenset({"verify_project", "execute_command"})),
    ],
)
async def test_verification_cannot_acquire_role_or_command_authority(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    role: Any,
    enabled: frozenset[str],
) -> None:
    """A decoded request still needs both host grants in an actual Director frame."""
    scope = replace(_scope(isolated_workspace), enabled_tools=enabled)
    scripted_provider.client("openai", isolated_workspace, monkeypatch)
    service = EngineeringRunService(isolated_workspace, permission_resolver=lambda _row: scope)
    scripted_provider.enqueue_json(_response(("denied", "verify_project", {"commands": ["echo forbidden"]})))
    scripted_provider.enqueue_json(_response(text="Cannot claim verification without authority."))
    command = RoleRunCommand("denied-goal", role, "Inspect the authorized goal.", isolated_workspace)
    await service.start(command, scope=scope, limits=RunLimits(time.time() + 45, 1_000_000, 64_000))
    await service.wait(command.session_id, workspace=isolated_workspace)
    events = service.store.load_events(command.session_id)
    assert not any(event["kind"] == "verification_started" for event in events)
    assert not any(
        event["kind"] == "tool_started" and event["payload"]["tool"] == "execute_command" for event in events
    )
    rejected = [event for event in events if event["kind"] == "tool_observed"]
    assert len(rejected) == 1 and rejected[0]["payload"]["ok"] is False
    schemas = scripted_provider.requests[0]["body"].get("tools", [])
    assert "verify_project" not in {tool["function"]["name"] for tool in schemas}
    assert service.snapshot(command.session_id, workspace=isolated_workspace)["project_outcome"] != "accepted"


@pytest.mark.parametrize("invalid", [[], [""], [" \t"], ["true\0"], [1], "true", None])
async def test_bad_check_selection_does_not_erase_a_real_completed_attempt(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch, invalid: Any
) -> None:
    """Rejecting a malformed tool request must not replace or replay valid checks."""
    scripted_provider.client("openai", isolated_workspace, monkeypatch)
    scope = _scope(isolated_workspace)
    service = EngineeringRunService(isolated_workspace, permission_resolver=lambda _row: scope)
    (isolated_workspace / "artifact.txt").write_text("2", encoding="utf-8")
    actual = "python3 -c \"from pathlib import Path; assert Path('artifact.txt').read_text(encoding='utf-8')=='2'\""
    for response in (
        _response(("valid", "verify_project", {"commands": [actual]})),
        _response(("invalid", "verify_project", {"commands": invalid})),
        _response(),
    ):
        scripted_provider.enqueue_json(response)
    command = RoleRunCommand("invalid-selection", "director", "Verify actual artifact2.", isolated_workspace)
    await service.start(command, scope=scope, limits=RunLimits(time.time() + 45, 1_000_000, 64_000))
    await service.wait(command.session_id, workspace=isolated_workspace)
    row = service.snapshot(command.session_id, workspace=isolated_workspace)
    assert row["project_outcome"] == "accepted", row
    assert row["verification_commands"] == [actual]
    events = service.store.load_events(command.session_id)
    assert len([event for event in events if event["kind"] == "verification_started"]) == 1
    rejected = next(
        event for event in events if event["kind"] == "tool_observed" and event["payload"]["call_id"] == "invalid"
    )
    assert rejected["payload"]["ok"] is False


async def test_failed_selected_check_returns_to_same_director_for_real_repair(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed actual check is feedback, with no repair contract or planning restart."""
    (isolated_workspace / "artifact.txt").write_text("1", encoding="utf-8")
    actual = "python3 -c \"from pathlib import Path; assert Path('artifact.txt').read_text(encoding='utf-8')=='2'\""
    for response in (
        _response(("before", "verify_project", {"commands": [actual]})),
        _response(("repair", "write_file", {"file": "artifact.txt", "content": "2"})),
        _response(("after", "verify_project", {"commands": [actual]})),
        _response(),
        _response(("read", "read_file", {"file": "artifact.txt"})),
        _response(("review", "report_review", {"approved": True, "feedback": "Current actual artifact2 is correct."})),
    ):
        scripted_provider.enqueue_json(response)
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    command = RoleRunCommand("actual-repair", "director", "Deliver artifact2.", isolated_workspace)
    await service.start(
        command, scope=_scope(isolated_workspace), limits=RunLimits(time.time() + 45, 1_000_000, 64_000)
    )
    await service.wait(command.session_id, workspace=isolated_workspace)
    row = service.snapshot(command.session_id, workspace=isolated_workspace)
    assert row["project_outcome"] == "accepted", row
    assert (isolated_workspace / "artifact.txt").read_text(encoding="utf-8") == "2"
    events = service.store.load_events(command.session_id)
    assert [event["payload"]["verification"] for event in events if event["kind"] == "verification_completed"] == [
        "failed",
        "passed",
    ]
    assert [event["role"] for event in events if event["kind"] == "role_started"] == ["director", "qa"]
    assert "AssertionError" in json.dumps(scripted_provider.requests[1]["body"], ensure_ascii=False)


async def test_writing_after_verified_candidate_requires_actual_fresh_check(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A prior pass cannot certify the later candidate, even within the same role."""
    (isolated_workspace / "artifact.txt").write_text("1", encoding="utf-8")
    actual = "python3 -c \"from pathlib import Path; value=int(Path('artifact.txt').read_text(encoding='utf-8')); assert value>0; print(value)\""
    for response in (
        _response(("verify-one", "verify_project", {"commands": [actual]})),
        _response(("write-two", "write_file", {"file": "artifact.txt", "content": "2"})),
        _response(),
        _response(("verify-current", "verify_project", {"commands": [actual]})),
        _response(),
        _response(("read", "read_file", {"file": "artifact.txt"})),
        _response(
            ("review", "report_review", {"approved": True, "feedback": "Read current artifact2, not old value1."})
        ),
    ):
        scripted_provider.enqueue_json(response)
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    command = RoleRunCommand("write-invalidates", "director", "Deliver artifact2.", isolated_workspace)
    await service.start(
        command, scope=_scope(isolated_workspace), limits=RunLimits(time.time() + 45, 1_000_000, 64_000)
    )
    await service.wait(command.session_id, workspace=isolated_workspace)
    row = service.snapshot(command.session_id, workspace=isolated_workspace)
    assert row["project_outcome"] == "accepted", row
    history = [
        event for event in service.store.load_events(command.session_id) if event["kind"] == "verification_completed"
    ]
    assert (
        len(history) == 2 and history[0]["payload"]["workspace_revision"] < history[1]["payload"]["workspace_revision"]
    )
    assert "stdout:\n1\n" in history[0]["payload"]["checks"][0]["content"]
    assert "stdout:\n2\n" in row["checks"][0]["content"]
    assert all(event["payload"]["input_binding"]["requested_by"] is not None for event in history)


@pytest.mark.parametrize("cut", ["before_outer", "after_outer", "before_completion", "before_child_observation"])
async def test_real_process_restart_closes_only_durable_verification_and_never_replays(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch, cut: str
) -> None:
    """Actual process death at four journal cuts cannot execute a check twice."""
    (isolated_workspace / "artifact.txt").write_text("2", encoding="utf-8")
    output = isolated_workspace / "dist"
    output.mkdir()
    counter = output / "count.txt"
    counter.write_text("0", encoding="utf-8")
    actual = (
        "python3 -c \"from pathlib import Path; p=Path('dist/count.txt'); "
        "p.write_text(str(int(p.read_text(encoding='utf-8'))+1),encoding='utf-8'); "
        "assert Path('artifact.txt').read_text(encoding='utf-8')=='2'; print('ACTUAL_COUNT',p.read_text(encoding='utf-8'))\""
    )
    scripted_provider.enqueue_json(_response(("crash-verify", "verify_project", {"commands": [actual]})))
    child = r"""
import asyncio, json, os, sys, time
from pathlib import Path
import pytest
from polaris.cells.roles.runtime.tests.engineering_support import ScriptedProvider
from polaris.cells.roles.runtime.tests.test_engineering_autonomous_verification import _scope
from polaris.cells.roles.runtime.public.contracts.engineering_run import RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
workspace, endpoint, cut = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
ScriptedProvider(base_url=endpoint).client('openai', workspace, pytest.MonkeyPatch())
async def main():
    service = EngineeringRunService(workspace, require_qa=True,
        permission_resolver=lambda row: _scope(workspace), writable_output_roots=(workspace/'dist',))
    original_observation = service.store.record_tool_observation
    def observation(session_id, **kwargs):
        tool = kwargs['observation'].tool
        if (cut == 'before_outer' and tool == 'verify_project') or (cut == 'before_child_observation' and tool == 'execute_command'):
            os._exit(73)
        original_observation(session_id, **kwargs)
        if cut == 'after_outer' and tool == 'verify_project':
            os._exit(73)
    service.store.record_tool_observation = observation
    original_completion = service.store.record_verification
    def completion(session_id, **kwargs):
        if cut == 'before_completion':
            os._exit(73)
        return original_completion(session_id, **kwargs)
    service.store.record_verification = completion
    command = RoleRunCommand('cold-verify', 'director', 'Deliver actual artifact2.', workspace)
    await service.start(command, scope=_scope(workspace), limits=RunLimits(time.time()+45,1000000,64000))
    result = await service.wait(command.session_id, workspace=workspace)
    fields = {
        'provider_response': ('turn_seq', 'ok', 'error'),
        'role_error': ('error_type', 'error'),
        'role_finished': ('status', 'text'),
        'run_ended': ('status', 'verification', 'release_allowed', 'drain_reporting_error'),
        'tool_started': ('turn_seq', 'call_id', 'tool'),
        'llm_request_observed': ('turn_seq', 'call_id', 'role', 'phase', 'status',
            'context_snapshot_ref', 'physical_send_count'),
    }
    events = service.store.load_events(command.session_id, kinds=tuple(fields))
    print(json.dumps({
        'diagnostic': 'expected_crash_hook_not_reached',
        'cut': cut,
        'wait_result': {key: getattr(result, key) for key in
            ('session_id', 'role', 'status', 'verification', 'text')},
        'events': [
            {'seq': event['seq'], 'kind': event['kind'], 'role': event['role'],
             'payload': {key: event['payload'][key] for key in fields[event['kind']]
                 if key in event['payload']}}
            for event in events[-16:]
        ],
    }, ensure_ascii=False), flush=True)
asyncio.run(main())
"""
    stopped = await asyncio.to_thread(
        subprocess.run,
        ["rtk", "proxy", sys.executable, "-c", child, str(isolated_workspace), scripted_provider.base_url, cut],
        env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=25,
    )
    assert stopped.returncode == 73, stopped.stdout + stopped.stderr
    assert counter.read_text(encoding="utf-8") == "1"

    scripted_provider.client("openai", isolated_workspace, monkeypatch)
    cold = EngineeringRunService(
        isolated_workspace,
        require_qa=True,
        permission_resolver=lambda _row: _scope(isolated_workspace),
        writable_output_roots=(output,),
    )
    if cut in {"before_completion", "before_child_observation"}:
        for _ in range(2):
            with pytest.raises(RuntimeError, match="unknown"):
                await cold.resume("cold-verify", workspace=isolated_workspace)
        row = cold.snapshot("cold-verify", workspace=isolated_workspace)
        assert row["status"] == "interrupted" and row["verification"] == "unknown"
        assert row["project_outcome"] != "accepted"
        assert len(scripted_provider.requests) == 1
    else:
        for response in (
            _response(text="Retain the actual completed verification; ready for QA."),
            _response(("read-after-crash", "read_file", {"file": "artifact.txt"})),
            _response(
                ("review-after-crash", "report_review", {"approved": True, "feedback": "Current artifact2 verified."})
            ),
        ):
            scripted_provider.enqueue_json(response)
        await cold.resume("cold-verify", workspace=isolated_workspace)
        await cold.wait("cold-verify", workspace=isolated_workspace)
        await cold.resume("cold-verify", workspace=isolated_workspace)
        row = cold.snapshot("cold-verify", workspace=isolated_workspace)
        assert row["project_outcome"] == "accepted", row
        assert row["required_checks"] == [] and row["verification_commands"] == [actual]
        history = scripted_provider.requests[1]["body"]["messages"]
        recovered = [message for message in history if message.get("tool_call_id") == "crash-verify"]
        assert len(recovered) == 1 and "ACTUAL_COUNT 1" in recovered[0]["content"]
    events = cold.store.load_events("cold-verify")
    assert (
        len(
            [
                event
                for event in events
                if event["kind"] == "tool_started" and event["payload"]["tool"] == "execute_command"
            ]
        )
        == 1
    )
    assert counter.read_text(encoding="utf-8") == "1"


@pytest.mark.parametrize("new_check", [False, True])
@pytest.mark.parametrize("intervening", ["none", "write", "command"])
async def test_resumed_text_cannot_automatically_repeat_an_interrupted_verification(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    new_check: bool,
    intervening: str,
) -> None:
    """A drained cancellation still leaves unknown effects, not an implicit retry grant."""
    output = isolated_workspace / "dist"
    output.mkdir()
    counter = output / "count.txt"
    counter.write_text("0", encoding="utf-8")
    actual = (
        "python3 -c \"import time; from pathlib import Path; p=Path('dist/count.txt'); "
        "p.write_text(str(int(p.read_text(encoding='utf-8'))+1),encoding='utf-8'); time.sleep(60)\""
    )
    scripted_provider.client("openai", isolated_workspace, monkeypatch)
    scope = _scope(isolated_workspace)
    service = EngineeringRunService(
        isolated_workspace, permission_resolver=lambda _row: scope, writable_output_roots=(output,)
    )
    scripted_provider.enqueue_json(_response(("cancelled-verify", "verify_project", {"commands": [actual]})))
    command = RoleRunCommand("cancelled-check", "director", "Check the current project.", isolated_workspace)
    await service.start(command, scope=scope, limits=RunLimits(time.time() + 45, 1_000_000, 64_000))
    async with asyncio.timeout(10):
        while counter.read_text(encoding="utf-8") != "1":
            await asyncio.sleep(0.01)
    await service.cancel(command.session_id, workspace=isolated_workspace)
    before = service.snapshot(command.session_id, workspace=isolated_workspace)
    assert before["verification"] == "unknown"
    if intervening == "write":
        scripted_provider.enqueue_json(
            _response(("new-source", "write_file", {"file": "note.txt", "content": "changed"}))
        )
    elif intervening == "command":
        scripted_provider.enqueue_json(
            _response(("ordinary-command", "execute_command", {"command": "printf changed > note.txt"}))
        )
    if new_check:
        scripted_provider.enqueue_json(_response(("inspect-count", "read_file", {"file": "dist/count.txt"})))
        scripted_provider.enqueue_json(
            _response(
                (
                    "new-readonly-check",
                    "verify_project",
                    {
                        "commands": [
                            "python3 -c \"from pathlib import Path; assert Path('dist/count.txt').read_text(encoding='utf-8')=='1'\""
                        ]
                    },
                )
            )
        )
    scripted_provider.enqueue_json(_response(text="Retain the prior observed results."))
    await service.resume(command.session_id, workspace=isolated_workspace)
    try:
        async with asyncio.timeout(10 if new_check else 3):
            await service.wait(command.session_id, workspace=isolated_workspace)
    except TimeoutError:
        await service.cancel(command.session_id, workspace=isolated_workspace)
    assert counter.read_text(encoding="utf-8") == "1", "Finalizer repeated an interrupted physical check."
    row = service.snapshot(command.session_id, workspace=isolated_workspace)
    if new_check:
        assert row["project_outcome"] == "accepted" and row["verification"] == "passed", row
        history = service.store.load_events(command.session_id)
        assert [event["payload"]["verification"] for event in history if event["kind"] == "verification_completed"] == [
            "unknown",
            "passed",
        ]
    else:
        assert row["status"] == "interrupted" and row["verification"] == "unknown"
        assert row["project_outcome"] != "accepted"
