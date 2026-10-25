"""Command-cache authority through real host settings, SQLite and cold recovery."""

from __future__ import annotations

import asyncio
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from polaris.bootstrap.config import EngineeringPermissionProfile, ServerConfig, Settings
from polaris.bootstrap.engineering_runtime import resolve_engineering_profile
from polaris.cells.roles.runtime.internal.engineering_loop import run_engineering_loop
from polaris.cells.roles.runtime.internal.engineering_roles import role_scope
from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    PermissionScope,
    RoleRunCommand,
    RunLimits,
    ToolObservation,
)
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_loop import _native
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from pydantic import ValidationError

CacheMode = Literal["disabled", "read_only", "read_write"]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    fs = KernelFileSystem(str(tmp_path), get_default_adapter(), read_only_paths=True)
    fs.workspace_mkdir("workspace", parents=True, exist_ok=True)
    return tmp_path / "workspace"


def host_settings(workspace: Path, mode: CacheMode | None = None, *, empty: bool = False) -> Settings:
    profile: dict[str, Any] = {
        "read_roots": [] if empty else [str(workspace)],
        "write_roots": [] if empty else [str(workspace)],
        "enabled_tools": [] if empty else ["read_file", "execute_command", "browser_observe", "report_review"],
        "network_allowed": False,
    }
    if mode is not None:
        profile["command_cache"] = mode
    return Settings(
        workspace=workspace,
        server=ServerConfig(engineering_permission_profiles={"explicit": EngineeringPermissionProfile(**profile)}),
    )


def cold_service(workspace: Path, settings: Settings) -> EngineeringRunService:
    return EngineeringRunService(
        workspace, permission_resolver=lambda _row: resolve_engineering_profile(settings, workspace, "explicit")
    )


def persist(
    service: EngineeringRunService,
    scope: PermissionScope,
    *,
    identity: str = "cache-authority",
    required_checks: tuple[str, ...] = (),
) -> str:
    return service.store.create_run(
        RoleRunCommand(
            identity,
            "director",
            "Synthetic cache authority fixture.",
            service.workspace,
            required_checks=required_checks,
        ),
        scope=scope,
        limits=RunLimits(None, 100_000, 16_000),
        engine_version=service.engine_version,
        request_key=identity,
        permission_profile_id="explicit",
    )


@pytest.mark.parametrize("mode", ["read_only", "read_write"])
def test_host_json_to_saved_scope_to_cold_recovery_preserves_explicit_authority(
    workspace: Path, mode: CacheMode
) -> None:
    settings = host_settings(workspace, mode)
    settings = Settings.model_validate(json.loads(settings.model_dump_json()))
    original = resolve_engineering_profile(settings, workspace, "explicit")
    service = cold_service(workspace, settings)
    identity = persist(service, original)
    cold = cold_service(workspace, settings)
    row = cold.store.load_run(identity, workspace=workspace)
    assert row["scope"]["command_cache"] == mode
    recovered = cold._revalidated_scope(row)
    assert recovered.command_cache == mode
    assert recovered.read_roots == (workspace,) and recovered.write_roots == (workspace,)
    assert recovered.network_allowed is False
    assert recovered.enabled_tools == frozenset({"read_file", "execute_command", "browser_observe", "report_review"})
    assert not (workspace / ".polaris/runtime/command-cache").exists()
    with pytest.raises(FrozenInstanceError):
        cast(Any, recovered).command_cache = "disabled"


@pytest.mark.parametrize("explicit_disabled", [False, True])
def test_missing_or_disabled_saved_cache_keeps_historical_identity_and_never_inherits_new_grant(
    workspace: Path, explicit_disabled: bool
) -> None:
    settings = host_settings(workspace, "disabled" if explicit_disabled else None, empty=True)
    original = resolve_engineering_profile(settings, workspace, "explicit")
    service = cold_service(workspace, settings)
    identity = persist(service, original)
    expected = {"read_roots": [], "write_roots": [], "enabled_tools": [], "network_allowed": False}
    assert service.store.load_run(identity, workspace=workspace)["scope"] == expected
    settings.server.engineering_permission_profiles["explicit"].command_cache = "read_write"
    cold = cold_service(workspace, settings)
    recovered = cold._revalidated_scope(cold.store.load_run(identity, workspace=workspace))
    assert recovered.command_cache == "disabled"
    assert recovered.read_roots == recovered.write_roots == ()
    assert recovered.enabled_tools == frozenset() and recovered.network_allowed is False
    assert persist(cold, recovered) == identity
    assert cold.store.load_run(identity, workspace=workspace)["scope"] == expected
    assert not (workspace / ".polaris/runtime/command-cache").exists()


@pytest.mark.parametrize(
    ("saved", "current", "allowed"),
    [
        ("disabled", "disabled", True),
        ("disabled", "read_only", True),
        ("disabled", "read_write", True),
        ("read_only", "disabled", False),
        ("read_only", "read_only", True),
        ("read_only", "read_write", True),
        ("read_write", "disabled", False),
        ("read_write", "read_only", False),
        ("read_write", "read_write", True),
    ],
)
def test_current_host_policy_is_ceiling_without_upgrading_saved_scope(
    workspace: Path, saved: CacheMode, current: CacheMode, allowed: bool
) -> None:
    settings = host_settings(workspace, saved)
    service = cold_service(workspace, settings)
    identity = persist(service, resolve_engineering_profile(settings, workspace, "explicit"))
    settings.server.engineering_permission_profiles["explicit"].command_cache = current
    cold = cold_service(workspace, settings)
    row = cold.store.load_run(identity, workspace=workspace)
    if allowed:
        assert cold._revalidated_scope(row).command_cache == saved
    else:
        with pytest.raises(PermissionError, match="saved scope exceeds current permission policy"):
            cold._revalidated_scope(row)


@pytest.mark.parametrize("invalid", [None, True, 1, "", "READ_ONLY", "read-write", [], {}, b"read_only"])
def test_host_and_persisted_decoder_reject_malformed_cache_mode(workspace: Path, invalid: Any) -> None:
    with pytest.raises(ValidationError):
        EngineeringPermissionProfile.model_validate({"command_cache": invalid})
    settings = host_settings(workspace)
    service = cold_service(workspace, settings)
    identity = persist(service, resolve_engineering_profile(settings, workspace, "explicit"))
    row = service.store.load_run(identity, workspace=workspace)
    row["scope"]["command_cache"] = invalid
    with pytest.raises(ValueError, match=r"command.cache"):
        service._scope(row)
    with pytest.raises(ValueError, match=r"command.cache"):
        PermissionScope((), (), frozenset(), False, command_cache=invalid)


@pytest.mark.parametrize("mode", ["disabled", "read_only", "read_write"])
def test_role_projection_preserves_director_and_caps_all_readonly_roles(workspace: Path, mode: CacheMode) -> None:
    settings = host_settings(workspace, mode)
    original = resolve_engineering_profile(settings, workspace, "explicit")
    for role in ("director", "pm", "chief_engineer", "qa"):
        derived = role_scope(role, original)
        assert derived.command_cache == (mode if role == "director" or mode == "disabled" else "read_only")
        assert derived.enabled_tools <= original.enabled_tools
        assert derived.read_roots == original.read_roots
        if role != "director":
            assert derived.write_roots == () and derived.network_allowed is False


@pytest.mark.parametrize("mode", ["disabled", "read_only", "read_write"])
async def test_browser_capture_projects_readonly_cache_before_helper_spawn(
    workspace: Path, mode: CacheMode, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = host_settings(workspace, mode)
    profile = settings.server.engineering_permission_profiles["explicit"]
    profile.browser = {  # type: ignore[assignment]
        "executable": "/usr/bin/true",
        "python_executable": "/usr/bin/python3",
        "python_paths": [],
    }
    scope = resolve_engineering_profile(settings, workspace, "explicit")
    service = cold_service(workspace, settings)
    identity = persist(service, scope)
    fs = KernelFileSystem(str(workspace), get_default_adapter(), read_only_paths=True)
    fs.workspace_write_text_atomic("index.html", "<h1>Synthetic capture fixture</h1>", encoding="utf-8")
    if mode != "disabled":
        fs.workspace_mkdir(".polaris/runtime/command-cache/data", parents=True, exist_ok=True)
    captured: list[PermissionScope] = []

    async def capture(call_id: str, arguments: dict[str, Any], **kwargs: Any) -> ToolObservation:
        captured.append(kwargs["scope"])
        return ToolObservation(call_id, "browser_observe", False, "Synthetic browser boundary only.", None, False)

    monkeypatch.setattr("polaris.cells.roles.runtime.internal.engineering_browser.capture_browser_observation", capture)
    command = RoleRunCommand(identity, "director", "Inspect synthetic page.", workspace)
    owner = service._owner.set((command, scope, RunLimits(None, 100_000, 16_000), asyncio.Event(), cast(Any, None)))
    role = service._active_role.set("director")
    try:
        observed = await service._observe_dispatch({"entry": "index.html"}, 1, "browser-cache-cap")
    finally:
        service._active_role.reset(role)
        service._owner.reset(owner)
    assert len(captured) == 1, observed.content
    assert captured[0].command_cache == ("disabled" if mode == "disabled" else "read_only")
    assert captured[0].write_roots == () and captured[0].network_allowed is False


@pytest.mark.parametrize(("mode", "revision"), [("disabled", 0), ("read_only", 0), ("read_write", 1)])
async def test_native_command_with_empty_source_write_grant_advances_revision_for_cache_rw(
    isolated_workspace: Path,
    mode: CacheMode,
    revision: int,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = isolated_workspace
    settings = host_settings(workspace, mode)
    settings.server.engineering_permission_profiles["explicit"].write_roots = ()
    scope = resolve_engineering_profile(settings, workspace, "explicit")
    service = cold_service(workspace, settings)
    check = "python3 -B -c 'assert 6 * 7 == 42'"
    identity = persist(service, scope, required_checks=(check,))
    limits = RunLimits(None, 100_000, 16_000)
    fs = KernelFileSystem(str(workspace), get_default_adapter(), read_only_paths=True)
    if mode != "disabled":
        fs.workspace_mkdir(".polaris/runtime/command-cache/data", parents=True, exist_ok=True)
    verifier = EngineeringVerification(
        workspace, store=service.store, scope=scope, limits=limits, cancel=asyncio.Event()
    )
    with service.store.acquire_workspace(workspace):
        checks = await verifier.run_verification((check,), session_id=identity, workspace_revision=0)
        assert len(checks) == 1 and checks[0].ok and checks[0].exit_code == 0
        assert service.store.load_run(identity, workspace=workspace)["verification"] == "passed"
        scripted_provider.enqueue_json(_native(("cache-capability", "execute_command", {"command": "true"})))
        scripted_provider.enqueue_json(_native(text="Synthetic capability observation completed."))
        result = await run_engineering_loop(
            RoleRunCommand(identity, "director", "Synthetic cache authority fixture.", workspace),
            scope=scope,
            limits=limits,
            cancel=asyncio.Event(),
            store=service.store,
            client=scripted_provider.client("openai", workspace, monkeypatch),
        )
    assert result.status == "completed", result.text
    row = service.store.load_run(identity, workspace=workspace)
    assert row["workspace_revision"] == revision
