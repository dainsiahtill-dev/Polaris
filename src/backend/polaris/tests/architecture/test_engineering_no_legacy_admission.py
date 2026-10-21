"""Production entry behavior: old writes never bootstrap legacy state.

Synthetic callers only; no product workspace, external model or Bench verdict.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.delivery.cli import router


@pytest.mark.parametrize("command,subcommand", [("task", "create"), ("session", "switch"), ("session", "clear")])
def test_retired_cli_mutations_reject_before_workspace_initialization(
    command: str,
    subcommand: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "not-created"

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("retired write initialized a workspace")

    monkeypatch.setattr(router.WorkspaceGuard, "ensure_workspace", forbidden)
    args = argparse.Namespace(
        workspace=str(workspace),
        task_command=subcommand,
        session_command=subcommand,
        role="director",
        subject="old task",
        session_id="old-session",
    )
    code = router._route_task(args) if command == "task" else router._route_session(args)
    assert code == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "legacy_engine_retired"
    assert not workspace.exists()


@pytest.mark.parametrize("method_name", ["execute_role_task", "execute_role_session", "stream_chat_turn"])
@pytest.mark.asyncio
async def test_retired_public_role_execution_rejects_before_authority_preflight_or_kernel(
    method_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1, ExecuteRoleTaskCommandV1
    from polaris.cells.roles.runtime.public.service import RoleRuntimeService

    service = RoleRuntimeService()

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("retired call reached preflight or kernel")

    monkeypatch.setattr(service, "_validate_task_execution_attempt", forbidden)
    monkeypatch.setattr(service, "_require_directed_effect_session_attempt", forbidden)
    monkeypatch.setattr(service, "_get_kernel", forbidden)
    task = ExecuteRoleTaskCommandV1(
        role="director", task_id="old-task", workspace=str(tmp_path), objective="do not execute"
    )
    session = ExecuteRoleSessionCommandV1(
        role="director", session_id="old-session", workspace=str(tmp_path), user_message="do not execute"
    )
    if method_name == "stream_chat_turn":
        events = [event async for event in service.stream_chat_turn(session)]
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["error_code"] == "legacy_engine_retired"
    else:
        command = task if method_name == "execute_role_task" else session
        with pytest.raises(RuntimeError, match="legacy_engine_retired"):
            await getattr(service, method_name)(command)
    assert not list(tmp_path.iterdir())


def test_retired_public_kernel_factory_cannot_construct_a_legacy_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.runtime.public import service as public_service

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("retired factory constructed an executor")

    monkeypatch.setattr(public_service.registry, "list_roles", lambda: ["director"])
    monkeypatch.setattr(public_service, "RoleExecutionKernel", forbidden, raising=False)
    service = public_service.RoleRuntimeService()
    with pytest.raises(RuntimeError, match="legacy_engine_retired"):
        service._get_kernel(str(tmp_path))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "method_name", ["run_interactive", "run_oneshot", "run_autonomous", "run_server", "execute_role"]
)
@pytest.mark.asyncio
async def test_legacy_convenience_entry_cannot_create_a_runner(
    method_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.runtime.public.service import RoleRuntimeService

    service = RoleRuntimeService()

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("retired convenience call constructed a runner")

    monkeypatch.setattr(service, "_get_cli_runner", forbidden)
    kwargs: dict[str, Any] = {"role": "director", "workspace": str(tmp_path)}
    if method_name in {"run_oneshot", "run_autonomous"}:
        kwargs["goal"] = "do not execute"
    if method_name == "execute_role":
        kwargs = {"role_id": "director", "context": {"workspace": str(tmp_path)}}
    with pytest.raises(RuntimeError, match="legacy_engine_retired"):
        await getattr(service, method_name)(**kwargs)
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_legacy_status_reports_retirement_without_claiming_live_queue_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.runtime.public import service as public_service
    from polaris.cells.roles.runtime.public.contracts import GetRoleRuntimeStatusQueryV1

    monkeypatch.setattr(public_service.registry, "list_roles", lambda: ["director"])
    output = await public_service.RoleRuntimeService().get_runtime_status(
        GetRoleRuntimeStatusQueryV1(workspace=str(tmp_path), role="director", include_tools=True)
    )
    assert output["ready"] is False
    assert output["execution_retired"] is True
    assert output["queue"]["pending"] is None
    assert output["queue"]["running"] is None
    assert output["tools"]["available"] is False
    assert not list(tmp_path.iterdir())


def test_old_tui_cannot_launch_another_writable_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    from types import SimpleNamespace

    from polaris.cells.roles.runtime.public.service import run_tui

    def forbidden(**kwargs: Any) -> None:
        pytest.fail("retired TUI launched an old host")

    monkeypatch.setitem(sys.modules, "polaris.delivery.cli.textual_console", SimpleNamespace(run_claude_tui=forbidden))
    with pytest.raises(RuntimeError, match="legacy_engine_retired"):
        run_tui("director", str(tmp_path))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("mode", ["single_turn", "lobe_chain"])
@pytest.mark.asyncio
async def test_aggregate_execution_rejects_before_legacy_planning(
    mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.runtime.public.contracts import AggregateChatCompletionsCommandV1, AggregateChatMessageV1
    from polaris.cells.roles.runtime.public.service import RoleRuntimeService

    service = RoleRuntimeService()

    async def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("retired aggregate execution reached planning")

    monkeypatch.setattr(service, "build_aggregate_role_plan", forbidden)
    command = AggregateChatCompletionsCommandV1(
        workspace=str(tmp_path),
        execution_mode=mode,
        messages=(AggregateChatMessageV1(role="user", content="do not execute"),),
    )
    with pytest.raises(RuntimeError, match="legacy_engine_retired"):
        await service.chat_completions(command)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "arguments",
    [
        ["console", "--engine", "legacy", "--instruction", "never execute"],
        ["task", "create", "--subject", "old task"],
        ["session", "switch", "--session-id", "old-session"],
        ["session", "clear"],
    ],
)
def test_canonical_retired_intent_rejects_before_any_workspace_or_runtime_bootstrap(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from polaris.delivery.cli import __main__ as main_cli

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("retired canonical intent reached workspace/runtime bootstrap")

    for name in ["_resolve_workspace", "_bind_workspace_env", "_bootstrap_runtime"]:
        monkeypatch.setattr(main_cli, name, forbidden)
    code = main_cli.main(arguments)
    assert code == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "legacy_engine_retired"


def test_cli_history_list_is_a_cold_workspace_bound_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from polaris.delivery.cli.director import console_host

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("history query constructed the legacy console/runtime host")

    monkeypatch.setattr(console_host, "DirectorConsoleHost", forbidden)
    args = argparse.Namespace(workspace=str(tmp_path), session_command="list", role="director", limit=20, json=True)
    code = router._route_session(args)
    assert code == 0
    assert json.loads(capsys.readouterr().out) == []
    assert not list(tmp_path.iterdir())


def test_cli_history_reads_persisted_messages_without_mutating_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    import hashlib
    from polaris.cells.roles.session.public import RoleSessionService

    # Explicit synthetic legacy fixture, not a project or acceptance receipt.
    with RoleSessionService(workspace=str(tmp_path), enable_events=False) as writer:
        session = writer.create_session(role="director", workspace=str(tmp_path), title="synthetic old record")
        writer.add_message(session.id, "user", "archived goal")

    def snapshot() -> dict[str, tuple[int, str | None]]:
        return {str(p.relative_to(tmp_path)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None) for p in tmp_path.rglob("*")}

    before = snapshot()
    args = argparse.Namespace(workspace=str(tmp_path), session_command="show", role="director", session_id=session.id)
    assert router._route_session(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == session.id
    assert payload["messages"][0]["content"] == "archived goal"
    assert snapshot() == before
