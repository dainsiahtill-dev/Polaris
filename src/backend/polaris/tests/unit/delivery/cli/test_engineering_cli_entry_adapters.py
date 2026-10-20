"""CLI opt-in dispatch enters the shared engineering service before legacy imports."""

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
        ("polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli", "chief_engineer"),
    ],
)
@pytest.mark.asyncio
async def test_cli_explicit_v2_runs_native_loop_and_emits_persisted_identity(
    module_name: str,
    role: str,
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
    monkeypatch.setattr(
        sys,
        "argv",
        [
            module_name,
            "--workspace",
            str(isolated_workspace),
            "--engine",
            "engineering_v2",
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
