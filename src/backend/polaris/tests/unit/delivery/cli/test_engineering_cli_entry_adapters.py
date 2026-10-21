"""Default CLI dispatch uses the shared engineering owner; history stays read-only."""

from __future__ import annotations

import importlib
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest
from polaris.bootstrap.config import EngineeringPermissionProfile, ServerConfig, Settings
from polaris.cells.events.fact_stream.public.service import (
    configure_debug_tracing,
    is_debug_tracing_enabled,
    set_debug_tracing_enabled,
)
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)


@pytest.mark.parametrize(
    "module_name,role",
    [
        ("polaris.delivery.cli.pm.cli", "pm"),
        ("polaris.delivery.cli.director.cli_thin", "director"),
        ("polaris.delivery.cli.__main__", "director"),
        ("polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli", "chief_engineer"),
    ],
)
@pytest.mark.parametrize("engine_args", [(), ("--engine", "engineering_v2")])
@pytest.mark.asyncio
async def test_cli_default_or_explicit_v2_runs_native_loop_and_emits_persisted_identity(
    module_name: str,
    role: str,
    engine_args: tuple[str, ...],
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scripted_provider.enqueue_json(
        {
            "id": "synthetic-cli",
            "choices": [
                {"message": {"role": "assistant", "content": "Reviewed requirements."}, "finish_reason": "stop"}
            ],
        }
    )
    service = EngineeringRunService(
        isolated_workspace,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
        permission_resolver=lambda row: PermissionScope((), (), frozenset(), False),
    )
    module = importlib.import_module(module_name)
    from polaris.bootstrap import config, engineering_runtime

    monkeypatch.setattr(
        config,
        "get_settings",
        lambda: Settings(
            workspace=isolated_workspace,
            server=ServerConfig(
                engineering_permission_profiles={
                    "none": EngineeringPermissionProfile(
                        read_roots=(), write_roots=(), enabled_tools=frozenset(), network_allowed=False
                    )
                },
                engineering_default_permission_profile_id="none",
            ),
        ),
    )

    monkeypatch.setattr(engineering_runtime, "create_engineering_service", lambda settings: service)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("legacy CLI bootstrap called")

    monkeypatch.setattr(module, "_bootstrap_backend_import_path", forbidden, raising=False)
    monkeypatch.setattr(module, "RoleRuntimeService", forbidden, raising=False)
    monkeypatch.setattr(module, "run_director_console", forbidden, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            module_name,
            *(["console"] if module_name.endswith(".__main__") else []),
            "--workspace",
            str(isolated_workspace),
            *engine_args,
            "--engineering-action",
            "execute",
            "--instruction",
            "Review requirements",
            "--request-key",
            "cli-key",
        ],
    )
    # Real host startup can leave native HTTP tracing enabled. Technical
    # events must remain observable on stderr without corrupting CLI stdout.
    tracing_was_enabled = is_debug_tracing_enabled()
    # Restore the exact caller environment after set_enabled updates it.
    monkeypatch.setenv("KERNELONE_DEBUG_TRACING", os.environ.get("KERNELONE_DEBUG_TRACING", "0"))
    try:
        with redirect_stdout(sys.stderr):
            configure_debug_tracing(True)
        if role == "chief_engineer":
            code = await module.main()
        else:
            # Main owns asyncio.run; invoke it in another thread without
            # replacing the shared service/store or native provider.
            import asyncio

            code = await asyncio.to_thread(module.main)
        captured = capsys.readouterr()
        raw = captured.out
        assert code == 0, raw
        output = json.loads(raw)
        assert '"event": "http.out.request"' in captured.err
        assert '"event": "http.out.response"' in captured.err
    finally:
        with redirect_stdout(sys.stderr):
            set_debug_tracing_enabled(tracing_was_enabled)
    assert output["engine_version"] == "engineering_v2"
    assert output["status"] == "completed"
    assert output["verification"] == "not_run"
    assert output["checks"] == []
    row = service.observe(output["run_id"], workspace=isolated_workspace)
    assert row["role"] == role
    assert row["request_key"] == "cli-key"
    assert row["engine_version"] == "engineering_v2"


@pytest.mark.parametrize("module_name", ["polaris.delivery.cli.pm.cli", "polaris.delivery.cli.director.cli_thin"])
def test_cli_parser_accepts_explicit_engine_before_legacy_bootstrap(module_name: str) -> None:
    module = importlib.import_module(module_name)
    args = module.create_parser().parse_args(
        ["--engine", "engineering_v2", "--engineering-action", "execute", "--instruction", "Review"]
    )
    assert args.engine == "engineering_v2"
    assert args.engineering_action == "execute"
    assert args.instruction == "Review"


@pytest.mark.parametrize(
    "module_name",
    [
        "polaris.delivery.cli.pm.cli",
        "polaris.delivery.cli.director.cli_thin",
        "polaris.delivery.cli.__main__",
        "polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli",
    ],
)
@pytest.mark.asyncio
async def test_explicit_legacy_cli_is_rejected_before_old_bootstrap_or_effects(
    module_name: str,
    isolated_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import asyncio

    module = importlib.import_module(module_name)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("retired engine reached old bootstrap")

    monkeypatch.setattr(module, "_bootstrap_backend_import_path", forbidden, raising=False)
    monkeypatch.setattr(module, "RoleRuntimeService", forbidden, raising=False)
    monkeypatch.setattr(module, "run_director_console", forbidden, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            module_name,
            *(["console"] if module_name.endswith(".__main__") else []),
            "--workspace",
            str(isolated_workspace),
            "--engine",
            "legacy",
            "--instruction",
            "must not run",
        ],
    )
    if module_name.endswith("chief_engineer_cli"):
        code = await module.main()
    else:
        code = await asyncio.to_thread(module.main)
    output = json.loads(capsys.readouterr().out)
    assert code == 1
    assert output["error_code"] == "legacy_engine_retired"
    assert not (isolated_workspace / ".polaris/runtime/engineering/sessions.sqlite3").exists()


@pytest.mark.parametrize(
    "module_name,legacy_options",
    [
        ("polaris.delivery.cli.pm.cli", ["--max-failures", "0"]),
        ("polaris.delivery.cli.pm.cli", ["--planner-response-path", "fake.json"]),
        ("polaris.delivery.cli.pm.cli", ["--timeout", "5"]),
        ("polaris.delivery.cli.director.cli_thin", ["--max-workers", "4"]),
        ("polaris.delivery.cli.director.cli_thin", ["--backend", "auto"]),
        ("polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli", ["--mode", "oneshot"]),
        ("polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli", ["--max-iterations", "4"]),
        ("polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli", ["--model", "other"]),
    ],
)
def test_old_execution_knobs_are_rejected_instead_of_silently_ignored(
    module_name: str,
    legacy_options: list[str],
) -> None:
    module = importlib.import_module(module_name)
    parser = (
        module.create_role_cli_parser("chief_engineer")
        if module_name.endswith("chief_engineer_cli")
        else module.create_parser()
    )
    with pytest.raises(SystemExit) as error:
        parser.parse_args(legacy_options)
    assert error.value.code == 2


def _history_snapshot(*roots: Path) -> dict[str, tuple[int, str | None]]:
    from hashlib import sha256

    return {
        f"{index}:{path.relative_to(root)}": (
            path.stat().st_mtime_ns,
            sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
        )
        for index, root in enumerate(roots)
        for path in (root, *root.rglob("*"))
    }


@pytest.mark.parametrize("command", [["console"], ["task", "list"], ["session", "list"], ["serve"]])
@pytest.mark.parametrize("global_option", [True, False])
def test_canonical_workspace_binding_survives_subparser_defaults(
    command: list[str],
    global_option: bool,
    tmp_path: Path,
) -> None:
    from polaris.delivery.cli import __main__ as main_cli

    arguments = (
        ["--workspace", str(tmp_path), *command]
        if global_option
        else [
            command[0],
            "--workspace",
            str(tmp_path),
            *command[1:],
        ]
    )
    assert main_cli.create_parser().parse_args(arguments).workspace == str(tmp_path)


@pytest.mark.parametrize(
    "arguments,expected_code",
    [
        (["task", "list"], 0),
        (["task", "show", "--task-id", "missing"], 1),
        (["session", "list"], 0),
        (["session", "show", "--session-id", "missing"], 1),
    ],
)
def test_canonical_history_query_never_bootstraps_or_initializes_a_workspace(
    arguments: list[str],
    expected_code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from polaris.delivery.cli import __main__ as main_cli, router
    from polaris.delivery.cli.director import console_host

    workspace = tmp_path / "cold-workspace"
    workspace.mkdir()

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("historical query initialized a workspace, console host or execution runtime")

    monkeypatch.setattr(main_cli, "_bootstrap_runtime", forbidden)
    monkeypatch.setattr(main_cli, "_bind_workspace_env", forbidden)
    monkeypatch.setattr(router.WorkspaceGuard, "ensure_workspace", forbidden)
    monkeypatch.setattr(console_host, "DirectorConsoleHost", forbidden)
    before = _history_snapshot(workspace)
    assert main_cli.main(["--workspace", str(workspace), *arguments]) == expected_code
    captured = capsys.readouterr()
    if expected_code == 0:
        assert json.loads(captured.out) == []
    else:
        assert "not found" in captured.err
    assert _history_snapshot(workspace) == before


@pytest.mark.parametrize("task_command", ["list", "show"])
def test_missing_task_history_workspace_is_reported_without_initialization(
    task_command: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import argparse

    from polaris.delivery.cli import router

    workspace = tmp_path / "never-created"
    args = argparse.Namespace(workspace=str(workspace), task_command=task_command, task_id="missing")
    assert router._route_task(args) == 1
    assert "workspace" in capsys.readouterr().err.lower()
    assert not workspace.exists()


def test_cli_task_history_reads_existing_execution_without_recreating_session_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from polaris.cells.events.fact_stream.public import (
        BootstrapFactStreamWorkspaceCommandV1,
        bootstrap_fact_stream_workspace,
        fact_stream_bootstrap_streams,
    )
    from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
    from polaris.delivery.cli import __main__ as main_cli
    from polaris.kernelone.storage import clear_storage_roots_cache, resolve_runtime_path

    # Synthetic owner-written history, never a Bench result or delivery receipt.
    workspace, private_home = tmp_path / "workspace", tmp_path / "private-home"
    workspace.mkdir()
    private_home.mkdir()
    for name in ("KERNELONE_RUNTIME_ROOT", "KERNELONE_RUNTIME_CACHE_ROOT", "KERNELONE_RAMDISK_ROOT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {
        "KERNELONE_HOME": str(private_home),
        "POLARIS_HOME": str(private_home),
        "KERNELONE_NATS_ENABLED": "false",
        "KERNELONE_NATS_REQUIRED": "false",
    }.items():
        monkeypatch.setenv(name, value)
    clear_storage_roots_cache()
    try:
        bootstrap_fact_stream_workspace(
            BootstrapFactStreamWorkspaceCommandV1(
                workspace=str(workspace),
                streams=fact_stream_bootstrap_streams(),
                maintenance_reason="cli-history-fixture",
            )
        )
        writer = TaskRuntimeService(str(workspace))
        row = writer.create_task_row(subject="Archived task", metadata={"history_marker": "preserve"})
        claimed = writer.claim_execution(row["id"], worker_id="synthetic-cli", role_id="director", run_id="history-run")
        assert claimed["success"] is True
        lock = Path(resolve_runtime_path(str(workspace), f"runtime/tasks/.task_{row['id']}.session.json.lock"))
        lock.unlink(missing_ok=True)
        before = _history_snapshot(workspace, private_home)
        assert main_cli.main(["--workspace", str(workspace), "task", "show", "--task-id", str(row["id"])]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert str(payload["id"]) == str(row["id"])
        assert payload["metadata"]["history_marker"] == "preserve"
        assert payload["execution_state"] == "in_progress"
        assert _history_snapshot(workspace, private_home) == before
        assert not lock.exists()
    finally:
        clear_storage_roots_cache()
