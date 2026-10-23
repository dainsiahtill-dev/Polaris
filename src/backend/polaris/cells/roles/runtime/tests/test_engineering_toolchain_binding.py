"""Host toolchain scope migration; real configuration/SQLite/K1, no provider calls."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.bootstrap.config import EngineeringPermissionProfile, ServerConfig, Settings, SettingsUpdate
from polaris.bootstrap.engineering_runtime import resolve_engineering_profile
from polaris.cells.roles.runtime.internal.engineering_roles import role_scope
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

TOOL = "synthetic_declared_tool"


@pytest.fixture
def fixture_tree(tmp_path: Path) -> tuple[Path, Path, tuple[Path, ...]]:
    fs = KernelFileSystem(str(tmp_path), get_default_adapter(), read_only_paths=True)
    fs.workspace_mkdir("workspace/.polaris", parents=True, exist_ok=True)
    for label in ("first", "second"):
        fs.workspace_mkdir("toolchain/" + label + "/bin", parents=True, exist_ok=True)
        path = "toolchain/" + label + "/bin/" + TOOL
        fs.workspace_write_text_atomic(path, "#!/bin/sh\nprintf '" + label.upper() + "\\n'\n", encoding="utf-8")
        os.chmod(tmp_path / path, 0o755)  # Executable metadata on the explicit synthetic fixture only.
    return (
        tmp_path / "workspace",
        tmp_path / "toolchain",
        (tmp_path / "toolchain/second/bin", tmp_path / "toolchain/first/bin"),
    )


def settings_for(
    workspace: Path,
    toolchain: Path,
    bins: tuple[Path, ...],
    *,
    enabled: tuple[str, ...] = ("execute_command", "read_file", "verify_project"),
) -> Settings:
    profile = EngineeringPermissionProfile.model_validate(
        {
            "read_roots": [str(workspace), str(toolchain)],
            "write_roots": [],
            "enabled_tools": enabled,
            "network_allowed": False,
            "toolchain_bin_dirs": [str(p) for p in bins],
        }
    )
    return Settings(workspace=workspace, server=ServerConfig(engineering_permission_profiles={"narrow": profile}))


def seed(
    workspace: Path, settings: Settings, identity: str, scope: PermissionScope
) -> tuple[EngineeringRunService, RoleRunCommand, RunLimits]:
    service = EngineeringRunService(
        workspace, permission_resolver=lambda _row: resolve_engineering_profile(settings, workspace, "narrow")
    )
    command = RoleRunCommand(
        identity, "director", "Explicit synthetic host toolchain migration fixture.", workspace, required_checks=(TOOL,)
    )
    limits = RunLimits(None, 100_000, 16_000)
    service.store.create_run(
        command,
        scope=scope,
        limits=limits,
        engine_version=service.engine_version,
        request_key=identity,
        permission_profile_id="narrow",
    )
    return service, command, limits


async def test_real_host_producer_cold_scope_qa_verifier_and_ordered_command(
    fixture_tree: tuple[Path, Path, tuple[Path, ...]],
) -> None:
    workspace, root, bins = fixture_tree
    settings = settings_for(workspace, root, (*bins, bins[0]))
    # Real host JSON roundtrip, not a dataclass-only assertion.
    restored_settings = Settings.model_validate(json.loads(settings.model_dump_json()))
    scope = resolve_engineering_profile(restored_settings, workspace, "narrow")
    assert scope.toolchain_bin_dirs == bins
    _service, command, limits = seed(workspace, restored_settings, "host-cold", scope)
    cold = EngineeringRunService(
        workspace, permission_resolver=lambda _row: resolve_engineering_profile(restored_settings, workspace, "narrow")
    )
    row = cold.store.load_run(command.session_id, workspace=workspace)
    assert row["scope"]["toolchain_bin_dirs"] == [str(p) for p in bins]
    recovered = cold._revalidated_scope(row)
    assert recovered.toolchain_bin_dirs == bins
    for role in ("pm", "chief_engineer", "director", "qa"):
        derived = role_scope(role, recovered)
        assert derived.toolchain_bin_dirs == bins
        assert derived.enabled_tools <= recovered.enabled_tools
        if role != "director":
            assert derived.write_roots == () and derived.network_allowed is False
    for actual_scope in (recovered, role_scope("qa", recovered)):
        observed = await EngineeringTools(workspace, actual_scope, limits, asyncio.Event()).execute(
            "real-command", "execute_command", {"command": TOOL}
        )
        assert observed.ok and observed.exit_code == 0, observed.content
        assert "SECOND" in observed.content and "FIRST" not in observed.content
    verifier = EngineeringVerification(
        workspace, store=cold.store, scope=recovered, limits=limits, cancel=asyncio.Event()
    )
    assert verifier.tools.scope.toolchain_bin_dirs == bins
    assert verifier._profile()["toolchain_bin_dirs"] == [str(p) for p in bins]
    checks = await verifier.run_verification((TOOL,), session_id=command.session_id, workspace_revision=0)
    assert len(checks) == 1 and checks[0].ok and checks[0].exit_code == 0, checks
    completed = cold.store.load_events(command.session_id, kinds=("verification_completed",))[-1]
    assert completed["payload"]["input_binding"]["scope"]["toolchain_bin_dirs"] == [str(p) for p in bins]
    assert not cold.store.pending_tool_calls(command.session_id)


@pytest.mark.parametrize("empty_read_roots", [False, True])
async def test_empty_historical_scope_does_not_inherit_new_bins_or_tools(
    fixture_tree: tuple[Path, Path, tuple[Path, ...]], monkeypatch: pytest.MonkeyPatch, empty_read_roots: bool
) -> None:
    workspace, root, bins = fixture_tree
    current = settings_for(workspace, root, bins, enabled=())
    original_reads = () if empty_read_roots else (workspace, root)
    old_scope = PermissionScope(original_reads, (), frozenset(), False)
    service, command, limits = seed(workspace, current, "legacy-empty", old_scope)
    row = service.store.load_run(command.session_id, workspace=workspace)
    expected = {
        "read_roots": [str(path) for path in original_reads],
        "write_roots": [],
        "enabled_tools": [],
        "network_allowed": False,
    }
    assert row["scope"] == expected
    with sqlite3.connect("file:" + str(service.store.database_path) + "?mode=ro", uri=True) as connection:
        encoded_scope = connection.execute(
            "SELECT scope FROM runs WHERE session_id=?", (command.session_id,)
        ).fetchone()[0]
    assert encoded_scope == json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    cold = EngineeringRunService(
        workspace, permission_resolver=lambda _row: resolve_engineering_profile(current, workspace, "narrow")
    )
    recovered = cold._revalidated_scope(cold.store.load_run(command.session_id, workspace=workspace))
    assert recovered.toolchain_bin_dirs == () and recovered.enabled_tools == frozenset()
    empty = settings_for(workspace, root, (), enabled=())
    empty.server.engineering_permission_profiles["narrow"].read_roots = tuple(str(path) for path in original_reads)
    equivalent = resolve_engineering_profile(empty, workspace, "narrow")
    again = replace(command, session_id="new-empty-identity")
    assert (
        cold.store.create_run(
            again,
            scope=equivalent,
            limits=limits,
            engine_version=cold.engine_version,
            request_key=command.session_id,
            permission_profile_id="narrow",
        )
        == command.session_id
    )
    assert cold.store.load_run(command.session_id, workspace=workspace)["scope"] == expected
    verifier = EngineeringVerification(
        workspace, store=cold.store, scope=recovered, limits=limits, cancel=asyncio.Event()
    )
    assert set(verifier._profile()) == {"read_roots", "output_roots", "execute_command", "network_allowed"}

    async def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("explicit empty tool grant reached physical process")

    monkeypatch.setattr(EngineeringTools, "_process", forbidden)
    observation = await EngineeringTools(workspace, recovered, limits, asyncio.Event()).execute(
        "no-authority", "execute_command", {"command": TOOL}
    )
    assert not observation.ok and "disabled" in observation.content


async def test_current_host_revocation_denies_before_process_and_regrant_advances(
    fixture_tree: tuple[Path, Path, tuple[Path, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, root, bins = fixture_tree
    settings = settings_for(workspace, root, bins)
    scope = resolve_engineering_profile(settings, workspace, "narrow")
    service, command, limits = seed(workspace, settings, "revoke", scope)
    settings.server.engineering_permission_profiles["narrow"].toolchain_bin_dirs = ()

    async def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("revoked host toolchain reached physical process")

    with monkeypatch.context() as patch:
        patch.setattr(EngineeringTools, "_process", forbidden)
        tools = EngineeringTools(
            workspace, scope, limits, asyncio.Event(), authorize=lambda: service._authorize_tools(command.session_id)
        )
        rejected = await tools.execute("revoked", "execute_command", {"command": TOOL})
        assert not rejected.ok and "saved scope exceeds" in rejected.content
    settings.server.engineering_permission_profiles["narrow"].toolchain_bin_dirs = bins
    cold = EngineeringRunService(
        workspace, permission_resolver=lambda _row: resolve_engineering_profile(settings, workspace, "narrow")
    )
    tools = EngineeringTools(
        workspace, scope, limits, asyncio.Event(), authorize=lambda: cold._authorize_tools(command.session_id)
    )
    passed = await tools.execute("regranted", "execute_command", {"command": TOOL})
    assert passed.ok and "SECOND" in passed.content
    settings.server.engineering_permission_profiles["narrow"].toolchain_bin_dirs = tuple(reversed(bins))
    with pytest.raises(PermissionError, match="saved scope exceeds"):
        cold._revalidated_scope(cold.store.load_run(command.session_id, workspace=workspace))


@pytest.mark.parametrize("invalid", ["relative", "not_mounted", "writable", "file"])
def test_host_rejects_invalid_bin_authority(fixture_tree: tuple[Path, Path, tuple[Path, ...]], invalid: str) -> None:
    workspace, root, bins = fixture_tree
    bad = "relative/bin" if invalid == "relative" else str(bins[0] / TOOL) if invalid == "file" else str(bins[0])
    profile = {
        "read_roots": [str(workspace)] if invalid == "not_mounted" else [str(workspace), str(root)],
        "write_roots": [str(root)] if invalid == "writable" else [],
        "enabled_tools": [],
        "toolchain_bin_dirs": [bad],
    }
    with pytest.raises(ValueError, match=r"toolchain|toolchain_bin_dirs"):
        settings = Settings(
            workspace=workspace,
            server=ServerConfig(
                engineering_permission_profiles={"narrow": EngineeringPermissionProfile.model_validate(profile)}
            ),
        )
        resolve_engineering_profile(settings, workspace, "narrow")


def test_actual_host_file_and_settings_full_partial_clear_roundtrips(
    fixture_tree: tuple[Path, Path, tuple[Path, ...]], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, root, bins = fixture_tree
    fs = KernelFileSystem(str(workspace), get_default_adapter(), read_only_paths=True)
    host = {
        "permission_profiles": {
            "narrow": {
                "read_roots": [str(workspace), str(root)],
                "write_roots": [],
                "enabled_tools": [],
                "toolchain_bin_dirs": [str(p) for p in bins],
            }
        },
        "default_permission_profile_id": "narrow",
    }
    fs.workspace_write_text_atomic(".polaris/host.json", json.dumps(host, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KERNELONE_ENGINEERING_HOST_CONFIG", str(workspace / ".polaris/host.json"))
    monkeypatch.setenv("KERNELONE_WORKSPACE", str(workspace))
    monkeypatch.delenv("KERNELONE_ENGINEERING_PERMISSION_PROFILES", raising=False)
    settings = Settings.from_env()
    assert resolve_engineering_profile(settings, workspace, "narrow").toolchain_bin_dirs == bins
    # Only a synthetic fixture is updated; no service, provider or real settings file.
    settings.self_upgrade_mode = True
    settings.apply_update(SettingsUpdate(server=settings.to_payload()["server"]))
    assert resolve_engineering_profile(settings, workspace, "narrow").toolchain_bin_dirs == bins
    settings.apply_update(SettingsUpdate(director_iterations=3))
    assert resolve_engineering_profile(settings, workspace, "narrow").toolchain_bin_dirs == bins
    payload = settings.to_payload()
    payload["server"]["engineering_permission_profiles"]["narrow"]["toolchain_bin_dirs"] = []
    settings.apply_update(SettingsUpdate(server=payload["server"]))
    restored = Settings.model_validate(settings.to_payload())
    assert resolve_engineering_profile(restored, workspace, "narrow").toolchain_bin_dirs == ()
    assert restored.server.engineering_permission_profiles["narrow"].enabled_tools == frozenset()


async def test_removed_toolchain_preserves_terminal_history_but_denies_execution(
    fixture_tree: tuple[Path, Path, tuple[Path, ...]],
) -> None:
    workspace, root, bins = fixture_tree
    settings = settings_for(workspace, root, bins)
    scope = resolve_engineering_profile(settings, workspace, "narrow")
    service, command, limits = seed(workspace, settings, "historical-toolchain", scope)
    verifier = EngineeringVerification(
        workspace, store=service.store, scope=scope, limits=limits, cancel=asyncio.Event()
    )
    checks = await verifier.run_verification((TOOL,), session_id=command.session_id, workspace_revision=0)
    assert checks and checks[0].ok
    service.store.update_run(command.session_id, status="completed", text="Historical synthetic completion.")
    before_events = service.store.load_events(command.session_id)
    # Physical metadata mutation affects only this explicitly synthetic toolchain.
    os.rename(bins[0], bins[0].with_name("removed-bin"))
    cold = EngineeringRunService(
        workspace, permission_resolver=lambda _row: resolve_engineering_profile(settings, workspace, "narrow")
    )
    historical = cold.store.load_run(command.session_id, workspace=workspace)
    assert historical["verification"] == "passed"
    decoded = cold._scope(historical)
    assert decoded.toolchain_bin_dirs == bins
    observed = cold.observe(command.session_id, workspace=workspace)
    assert observed["status"] == "completed" and observed["verification"] == "unknown"
    assert observed["text"] == "Historical synthetic completion."
    await cold.cancel(command.session_id, workspace=workspace)
    assert cold.store.load_events(command.session_id) == before_events
    assert cold.store.load_run(command.session_id, workspace=workspace)["verification"] == "passed"
    with pytest.raises((ValueError, OSError)):
        cold._revalidated_scope(historical)
