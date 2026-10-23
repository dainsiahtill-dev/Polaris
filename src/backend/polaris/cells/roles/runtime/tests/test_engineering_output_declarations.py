"""Future output permissions: scripted models, real owner/HTTP/KFS/sandbox effects."""

from __future__ import annotations

import asyncio
import json
import shlex
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_autonomous_verification import _response
from polaris.cells.roles.runtime.tests.test_engineering_browser_complete_flow import _scope as browser_scope
from polaris.kernelone.fs import get_default_adapter


def _python(source: str) -> str:
    return "python3 -B -c " + shlex.quote(source)


def _scope(workspace: Path) -> PermissionScope:
    return PermissionScope(
        (workspace,), (workspace,), frozenset({"execute_command", "read_file", "verify_project"}), False
    )


def _limits() -> RunLimits:
    return RunLimits(time.time() + 30, 1_000_000, 64_000)


def _write(path: Path, content: str) -> None:
    get_default_adapter().write_text(str(path), content, encoding="utf-8")


def _seed(
    workspace: Path,
    *,
    outputs: tuple[Path, ...],
    checks: tuple[str, ...] = (),
    scope: PermissionScope | None = None,
    require_qa: bool = False,
) -> tuple[EngineeringSessionStore, PermissionScope, RunLimits]:
    original = _scope(workspace) if scope is None else scope
    limits = _limits()
    store = EngineeringSessionStore(workspace)
    store.create_run(
        RoleRunCommand("declarations", "director", "Verify current source.", workspace, required_checks=checks),
        scope=original,
        limits=limits,
        engine_version="engineering_v2",
        request_key=None,
        verification_output_roots=outputs,
        require_qa=require_qa,
    )
    return store, original, limits


def _verifier(
    workspace: Path, store: EngineeringSessionStore, scope: PermissionScope, limits: RunLimits
) -> EngineeringVerification:
    row = store.load_run("declarations", workspace=workspace)
    return EngineeringVerification(
        workspace,
        store=store,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        writable_output_roots=tuple(Path(root) for root in row["verification_output_roots"]),
    )


async def test_browser_cleanup_keeps_next_native_read_reachable(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing optional dist must not turn real browser history into fatal authority failure."""
    workspace = isolated_workspace
    (workspace / "dist").mkdir()
    _write(workspace / "index.html", "<!doctype html><meta charset='utf-8'><h1>当前候选</h1>")
    _write(workspace / "source.txt", "ordinary read remains reachable")
    scope = replace(
        browser_scope(workspace),
        read_roots=(workspace, *browser_scope(workspace).read_roots[1:]),
        write_roots=(workspace,),
        enabled_tools=frozenset({"browser_observe", "execute_command", "read_file"}),
    )
    for response in (
        _response(("capture", "browser_observe", {"entry": "index.html", "frame_times_ms": [0]})),
        _response(("remove-optional-output", "execute_command", {"command": "rm -rf dist"})),
        _response(("next-ordinary-read", "read_file", {"file": "source.txt"})),
        _response(text="Read the current source after optional output cleanup."),
    ):
        scripted_provider.enqueue_json(response)
    service = EngineeringRunService(
        workspace,
        client=scripted_provider.client("openai", workspace, monkeypatch),
        writable_output_roots=(workspace / "dist",),
    )
    result = await service.run_role(
        RoleRunCommand("cleanup", "director", "Capture, clean optional output, then read source.", workspace),
        scope=scope,
        limits=_limits(),
        cancel=asyncio.Event(),
    )
    events = service.store.load_events("cleanup")
    observed = {e["payload"]["call_id"]: e["payload"] for e in events if e["kind"] == "tool_observed"}
    assert observed["capture"]["ok"], observed["capture"]
    assert observed["remove-optional-output"]["ok"], observed["remove-optional-output"]
    assert not (workspace / "dist").exists()
    assert "next-ordinary-read" in observed, (result.text, [e for e in events if e["kind"] == "role_error"])
    assert observed["next-ordinary-read"]["ok"]
    assert "ordinary read remains reachable" in observed["next-ordinary-read"]["content"]
    assert len(scripted_provider.requests) == 4
    assert result.status == "completed", result.text
    assert service.store.load_run("cleanup", workspace=workspace)["verification_output_roots"] == [
        str(workspace / "dist")
    ]


@pytest.mark.parametrize("explicit", [False, True], ids=["implicit", "native-selected"])
async def test_missing_declared_output_allows_real_readonly_check_without_mkdir(
    isolated_workspace: Path, explicit: bool
) -> None:
    workspace = isolated_workspace
    _write(workspace / "source.txt", "中文输入")
    check = _python("from pathlib import Path; assert Path('source.txt').read_text(encoding='utf-8') == '中文输入'")
    store, scope, limits = _seed(workspace, outputs=(workspace / "dist",), checks=() if explicit else (check,))
    verifier = _verifier(workspace, store, scope, limits)
    parent = None
    if explicit:
        turn = store.append_event("declarations", kind="turn_started", role="director", payload={})
        store.record_tool_dispatch(
            "declarations",
            turn_seq=turn,
            call_id="choose",
            tool="verify_project",
            arguments={"commands": [check]},
            role="director",
        )
        parent = (turn, "choose")
    with store.acquire_workspace(workspace):
        observations = await verifier.run_verification(
            (check,), session_id="declarations", workspace_revision=0, requested_by=parent
        )
    assert len(observations) == 1 and observations[0].ok and observations[0].exit_code == 0
    completed = store.load_events("declarations", kinds=("verification_completed",))[-1]["payload"]
    assert completed["verification"] == "passed"
    assert completed["input_binding"]["scope"]["output_roots"] == []
    assert not (workspace / "dist").exists()
    assert store.load_run("declarations", workspace=workspace)["verification_output_roots"] == [str(workspace / "dist")]


async def test_implicit_retry_refreshes_effective_outputs_after_authorized_director_prepare(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = isolated_workspace
    _write(workspace / "source.txt", "preserved")
    check = _python(
        "from pathlib import Path; Path('dist').mkdir(exist_ok=True); Path('dist/result.txt').write_text('verified',encoding='utf-8')"
    )
    for response in (
        _response(text="Run the required check."),
        _response(("prepare-dist", "execute_command", {"command": "mkdir dist"})),
        _response(text="The output directory is prepared; run the required check again."),
    ):
        scripted_provider.enqueue_json(response)
    cancel = asyncio.Event()

    async def stop_after_second_failed_attempt(event: dict[str, Any]) -> bool:
        # Bounded negative experiment: prevent stale-subset regressions from
        # consuming an empty model queue. The second real attempt is decisive.
        if event["kind"] == "verification_completed":
            completed = service.store.load_events("implicit-refresh", kinds=("verification_completed",))
            if len(completed) >= 2 and event["payload"]["verification"] != "passed":
                cancel.set()
        return True

    service = EngineeringRunService(
        workspace,
        client=scripted_provider.client("openai", workspace, monkeypatch),
        writable_output_roots=(workspace / "dist",),
        event_publisher=stop_after_second_failed_attempt,
    )
    result = await service.run_role(
        RoleRunCommand(
            "implicit-refresh",
            "director",
            "Prepare and verify without altering source.",
            workspace,
            required_checks=(check,),
        ),
        scope=_scope(workspace),
        limits=_limits(),
        cancel=cancel,
    )
    await service.flush_events()
    attempts = service.store.load_events("implicit-refresh", kinds=("verification_completed",))
    assert len(attempts) == 2, (result.text, attempts)
    first, second = [event["payload"] for event in attempts]
    assert first["verification"] == "failed" and first["checks"][0]["exit_code"] == 1
    assert "Read-only file system" in first["checks"][0]["content"]
    assert first["input_binding"]["scope"]["output_roots"] == []
    assert second["input_binding"]["scope"]["output_roots"] == [str(workspace / "dist")]
    assert second["verification"] == "passed" and second["checks"][0]["exit_code"] == 0
    assert get_default_adapter().read_text(str(workspace / "dist/result.txt"), encoding="utf-8") == "verified"
    assert get_default_adapter().read_text(str(workspace / "source.txt"), encoding="utf-8") == "preserved"
    assert "Read-only file system" in json.dumps(scripted_provider.requests[1]["body"], ensure_ascii=False)
    assert result.status == "completed" and result.verification == "passed", result


async def test_native_selected_retry_uses_prepared_directory_without_expanding_saved_outputs(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = isolated_workspace
    check = _python(
        "from pathlib import Path; Path('dist').mkdir(exist_ok=True); Path('dist/result.txt').write_text('verified',encoding='utf-8')"
    )
    for response in (
        _response(("verify-absent", "verify_project", {"commands": [check]})),
        _response(("prepare-dist", "execute_command", {"command": "mkdir dist"})),
        _response(("verify-prepared", "verify_project", {"commands": [check]})),
        _response(text="Selected checks passed on the prepared current candidate."),
    ):
        scripted_provider.enqueue_json(response)
    service = EngineeringRunService(
        workspace,
        client=scripted_provider.client("openai", workspace, monkeypatch),
        writable_output_roots=(workspace / "dist",),
    )
    result = await service.run_role(
        RoleRunCommand("native-refresh", "director", "Select and execute meaningful current checks.", workspace),
        scope=_scope(workspace),
        limits=_limits(),
        cancel=asyncio.Event(),
    )
    attempts = service.store.load_events("native-refresh", kinds=("verification_completed",))
    assert len(attempts) == 2, (result.text, attempts)
    first, second = [event["payload"] for event in attempts]
    assert first["verification"] == "failed" and first["checks"][0]["exit_code"] == 1
    assert first["input_binding"]["scope"]["output_roots"] == []
    assert second["verification"] == "passed" and second["checks"][0]["exit_code"] == 0
    assert second["input_binding"]["scope"]["output_roots"] == [str(workspace / "dist")]
    assert service.store.load_run("native-refresh", workspace=workspace)["verification_output_roots"] == [
        str(workspace / "dist")
    ]
    assert result.status == "completed" and result.verification == "passed", result


@pytest.mark.parametrize("invalid", ["workspace", "control", "outside", "symlink", "file", "empty-grant"])
def test_all_original_declarations_are_validated_before_missing_subset_filter(
    isolated_workspace: Path, invalid: str
) -> None:
    workspace = isolated_workspace
    alias = workspace / "alias"
    alias.symlink_to(workspace.parent, target_is_directory=True)
    _write(workspace / "file", "not a directory")
    bad = {
        "workspace": workspace,
        "control": workspace / ".polaris/absent",
        "outside": workspace.parent / "absent",
        "symlink": alias / "absent",
        "file": workspace / "file",
        "empty-grant": workspace / "another-missing",
    }[invalid]
    scope = replace(_scope(workspace), write_roots=()) if invalid == "empty-grant" else _scope(workspace)
    store, scope, limits = _seed(workspace, outputs=(workspace / "dist", bad), scope=scope)
    with pytest.raises(ValueError, match="output"):
        _verifier(workspace, store, scope, limits)
    assert not (workspace / "dist").exists()


@pytest.mark.parametrize("replacement", ["file", "symlink"])
async def test_absent_declaration_cannot_disappear_from_later_manifest_validation(
    isolated_workspace: Path, replacement: str
) -> None:
    workspace = isolated_workspace
    check = "printf should-not-run"
    store, scope, limits = _seed(workspace, outputs=(workspace / "dist",), checks=(check,))
    verifier = _verifier(workspace, store, scope, limits)
    if replacement == "file":
        _write(workspace / "dist", "invalid replacement")
    else:
        (workspace / "dist").symlink_to(workspace.parent, target_is_directory=True)
    checks = await verifier.run_verification((check,), session_id="declarations", workspace_revision=0)
    assert checks == ()
    assert store.load_run("declarations", workspace=workspace)["verification"] == "unknown"
    assert not store.load_events("declarations", kinds=("tool_started",))


async def test_cold_original_outputs_do_not_gain_new_host_outputs_or_replay_unknown(isolated_workspace: Path) -> None:
    workspace = isolated_workspace
    check = "printf should-not-run"
    store, scope, limits = _seed(workspace, outputs=(workspace / "dist",), checks=(check,))
    turn = store.append_event("declarations", kind="turn_started", role="director", payload={})
    store.record_tool_dispatch(
        "declarations",
        turn_seq=turn,
        call_id="unknown-effect",
        tool="execute_command",
        arguments={"command": "mkdir dist"},
        role="director",
    )
    store.update_run("declarations", status="interrupted", verification="unknown")
    historical = store.load_run("declarations", workspace=workspace)
    events = store.load_events("declarations")
    cold = EngineeringRunService(
        workspace,
        writable_output_roots=(workspace / "dist", workspace / "cache"),
        permission_resolver=lambda _row: scope,
    )
    saved = cold._saved_outputs(historical)
    assert saved == (workspace / "dist",)
    verifier = cold._verification(scope, limits, asyncio.Event(), output_roots=saved)
    assert verifier.review_binding("declarations")["profile"]["output_roots"] == []
    assert cold.observe("declarations", workspace=workspace)["verification"] == "unknown"
    assert not (workspace / "dist").exists() and not (workspace / "cache").exists()
    assert cold.store.load_run("declarations", workspace=workspace) == historical
    assert cold.store.load_events("declarations") == events
    assert cold.store.pending_tool_calls("declarations")[0]["call_id"] == "unknown-effect"


def test_revoked_host_output_policy_does_not_silently_drop_missing_declaration(isolated_workspace: Path) -> None:
    workspace = isolated_workspace
    store, scope, _limits_value = _seed(workspace, outputs=(workspace / "dist",))
    row = store.load_run("declarations", workspace=workspace)
    cold = EngineeringRunService(workspace, writable_output_roots=(), permission_resolver=lambda _row: scope)
    with pytest.raises(PermissionError, match="current host policy"):
        cold._saved_outputs(row)


async def test_one_attempt_never_adds_output_mount_after_construction(isolated_workspace: Path) -> None:
    workspace = isolated_workspace
    check = _python("from pathlib import Path; Path('dist/result.txt').write_text('verified',encoding='utf-8')")
    store, scope, limits = _seed(workspace, outputs=(workspace / "dist",), checks=(check,))
    attempt = _verifier(workspace, store, scope, limits)
    (workspace / "dist").mkdir()
    observations = await attempt.run_verification((check,), session_id="declarations", workspace_revision=0)
    assert len(observations) == 1 and not observations[0].ok and observations[0].exit_code == 1
    assert "Read-only file system" in observations[0].content
    assert not (workspace / "dist/result.txt").exists()
    fresh = _verifier(workspace, store, scope, limits)
    observations = await fresh.run_verification((check,), session_id="declarations", workspace_revision=0)
    assert observations[0].ok and observations[0].exit_code == 0
    assert fresh.current_verification("declarations") == "passed"


@pytest.mark.parametrize("replacement", ["missing", "file", "symlink", "directory"])
async def test_cold_approved_qa_cannot_reuse_removed_or_replaced_declared_output(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    workspace = isolated_workspace
    output = workspace / "dist"
    output.mkdir()
    _write(workspace / "source.txt", "current source")
    scope = replace(_scope(workspace), enabled_tools=_scope(workspace).enabled_tools | {"report_review"})
    for response in (
        _response(text="The current candidate is ready."),
        _response(("qa-source", "read_file", {"file": "source.txt"})),
        _response(("qa-approve", "report_review", {"approved": True, "feedback": "Read current source."})),
    ):
        scripted_provider.enqueue_json(response)
    service = EngineeringRunService(
        workspace,
        client=scripted_provider.client("openai", workspace, monkeypatch),
        permission_resolver=lambda _row: scope,
        writable_output_roots=(output,),
        require_qa=True,
    )
    command = RoleRunCommand(
        "qa-output", "director", "Verify current source and request QA.", workspace, required_checks=("exit 0",)
    )
    await service.run_role(command, scope=scope, limits=_limits(), cancel=asyncio.Event())
    assert service.observe(command.session_id, workspace=workspace)["project_outcome"] == "accepted"
    historical = service.store.load_run(command.session_id, workspace=workspace)
    events = service.store.load_events(command.session_id)
    cold = EngineeringRunService(workspace, writable_output_roots=(output,), permission_resolver=lambda _row: scope)
    output.rmdir()
    if replacement == "file":
        _write(output, "not a directory")
    elif replacement == "symlink":
        output.symlink_to(workspace.parent, target_is_directory=True)
    elif replacement == "directory":
        output.mkdir()
    for observer in (service, cold):
        row = observer.observe(command.session_id, workspace=workspace)
        assert row["verification"] == "unknown" and row["qa_verdict"] == "unknown"
        assert row["project_outcome"] == "unknown" and row["acceptance_basis"] is None
        assert observer.store.load_run(command.session_id, workspace=workspace) == historical
        assert observer.store.load_events(command.session_id) == events


async def test_historical_none_declaration_uses_old_completion_not_current_host(isolated_workspace: Path) -> None:
    workspace = isolated_workspace
    output = workspace / "dist"
    output.mkdir()
    store = EngineeringSessionStore(workspace)
    scope, limits = _scope(workspace), _limits()
    store.create_run(
        RoleRunCommand(
            "legacy-output", "director", "Read historical declaration.", workspace, required_checks=("exit 0",)
        ),
        scope=scope,
        limits=limits,
        engine_version="engineering_v2",
        request_key=None,
    )
    verifier = EngineeringVerification(
        workspace, store=store, scope=scope, limits=limits, cancel=asyncio.Event(), writable_output_roots=(output,)
    )
    assert (await verifier.run_verification(("exit 0",), session_id="legacy-output", workspace_revision=0))[0].ok
    row = store.load_run("legacy-output", workspace=workspace)
    assert row["verification_output_roots"] is None
    output.rmdir()
    cold = EngineeringRunService(
        workspace, writable_output_roots=(output, workspace / "cache"), permission_resolver=lambda _row: scope
    )
    assert cold._saved_outputs(row) == (output,)
    assert cold.observe("legacy-output", workspace=workspace)["verification"] == "unknown"
    assert store.load_run("legacy-output", workspace=workspace) == row
    assert not (workspace / "cache").exists()
