"""Real command verification and host-only recovery into a fresh directory."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_loop import _native


async def test_cold_export_recovers_original_verified_bytes_without_changing_current_candidate(
    isolated_workspace: Path, tmp_path: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = b"ORIGINAL_checkpoint_candidate\n"
    source = isolated_workspace / "src"
    source.mkdir()
    entry = source / "thing.bin"
    entry.write_bytes(original)
    ignored = source / ".git"
    ignored.mkdir()
    (ignored / "config").write_bytes(b"private Git metadata")
    (isolated_workspace / "outside_grant").write_bytes(b"ungranted workspace data")
    scope = PermissionScope((source,), (), frozenset({"execute_command"}), False)
    command = RoleRunCommand(
        "export-test", "director", "Verify original.", isolated_workspace, required_checks=("true",)
    )
    service = EngineeringRunService(
        isolated_workspace,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
        retain_verified_candidate=True,
        permission_resolver=lambda _: scope,
    )
    scripted_provider.enqueue_json(_native(text="Done."))
    result = await service.run_role(command, scope=scope, limits=RunLimits(None, 100000, 20000), cancel=asyncio.Event())
    assert result.verification == "passed", result.text
    events = service.store.load_events(command.session_id, kinds=("verification_completed",))
    ref = events[-1]["payload"]["input_binding"]["verified_checkpoint"]
    assert ref["status"] == "available"
    assert original.decode().strip() not in json.dumps(events)
    entry.write_bytes(b"changed later")
    cold = EngineeringRunService(
        isolated_workspace, permission_resolver=lambda _: scope, retain_verified_candidate=True
    )
    assert cold.observe(command.session_id, workspace=isolated_workspace)["verification"] == "unknown"
    out = tmp_path / "recovered"
    cold.export_verified_candidate(command.session_id, destination=out)
    assert (out / "src" / "thing.bin").read_bytes() == original
    assert not (out / "src" / ".git").exists()
    assert not (out / "outside_grant").exists()
    assert entry.read_bytes() == b"changed later"
    assert len(scripted_provider.requests) == 1
    with pytest.raises(FileExistsError):
        cold.export_verified_candidate(command.session_id, destination=out)
    with pytest.raises(ValueError):
        cold.export_verified_candidate(command.session_id, destination=source / "restore")
    denied = EngineeringRunService(
        isolated_workspace,
        retain_verified_candidate=True,
        permission_resolver=lambda _: PermissionScope((), (), frozenset(), False),
    )
    with pytest.raises(PermissionError):
        denied.export_verified_candidate(command.session_id, destination=tmp_path / "denied")
    assert not (tmp_path / "denied").exists()
    archive = cold.store.control_root / "checkpoints" / (ref["sha256"] + ".tar")
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == ref["sha256"]
    archive.write_bytes(b"corruption")
    with pytest.raises(ValueError, match="digest"):
        cold.export_verified_candidate(command.session_id, destination=tmp_path / "corrupt")
    assert not (tmp_path / "corrupt").exists()


async def test_ineligible_and_racing_captures_leave_last_good_checkpoint(
    isolated_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.internal import engineering_checkpoint
    from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification

    source = isolated_workspace / "program"
    source.write_bytes(b"first")
    source.chmod(0o700)
    scope = PermissionScope((isolated_workspace,), (), frozenset({"execute_command"}), False)
    limits = RunLimits(None, 100000, 20000)
    run = RoleRunCommand("changing", "director", "Check bytes", isolated_workspace, required_checks=("true",))
    service = EngineeringRunService(
        isolated_workspace, permission_resolver=lambda _: scope, retain_verified_candidate=True
    )
    store = service.store
    store.create_run(
        run,
        scope=scope,
        limits=limits,
        request_key=None,
        engine_version=service.engine_version,
        retain_verified_candidate=True,
    )
    verifier = EngineeringVerification(
        isolated_workspace,
        store=store,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        retain_verified_candidate=True,
    )

    async def verify():
        with store.acquire_workspace(isolated_workspace):
            await verifier.run_verification(("true",), session_id=run.session_id, workspace_revision=0)
        return store.load_events(run.session_id, kinds=("verification_completed",))[-1]

    original = await verify()
    ref = original["payload"]["input_binding"]["verified_checkpoint"]
    assert ref["status"] == "available"
    assert (await verify())["payload"]["input_binding"]["verified_checkpoint"] == ref
    link = isolated_workspace / "link"
    link.symlink_to(source)
    unsupported = await verify()
    assert unsupported["payload"]["verification"] == "passed"
    assert unsupported["payload"]["input_binding"]["verified_checkpoint"]["status"] == "unavailable"
    link.unlink()
    save = engineering_checkpoint.save_checkpoint

    def mutate(*args, **kwargs):
        captured = save(*args, **kwargs)
        source.write_bytes(b"later")
        return captured

    monkeypatch.setattr(engineering_checkpoint, "save_checkpoint", mutate)
    unstable = await verify()
    assert unstable["payload"]["verification"] == "unknown"
    assert unstable["payload"]["input_binding"]["verified_checkpoint"]["status"] == "unavailable"
    recovered = service.export_verified_candidate(run.session_id, destination=tmp_path / "best")
    assert (recovered / "program").read_bytes() == b"first"
    assert (recovered / "program").stat().st_mode & 0o100
    assert source.read_bytes() == b"later"
    with pytest.raises(ValueError, match="no retained verified candidate"):
        service.export_verified_candidate(
            run.session_id, destination=tmp_path / "unsupported", verification_seq=unsupported["seq"]
        )


async def test_old_run_does_not_gain_retention_when_current_host_enables_it(isolated_workspace: Path) -> None:
    from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
    from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification

    scope = PermissionScope((isolated_workspace,), (), frozenset({"execute_command"}), False)
    run = RoleRunCommand("historical", "director", "Verify", isolated_workspace, required_checks=("true",))
    limits = RunLimits(None, 100000, 20000)
    (isolated_workspace / "candidate").write_bytes(b"immutable opt-in")
    store = EngineeringSessionStore(isolated_workspace)
    store.create_run(run, scope=scope, limits=limits, request_key=None, engine_version="engineering_v2")
    verifier = EngineeringVerification(
        isolated_workspace,
        store=store,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        retain_verified_candidate=True,
    )
    with store.acquire_workspace(isolated_workspace):
        await verifier.run_verification(run.required_checks, session_id=run.session_id, workspace_revision=0)
    passed = store.load_events(run.session_id, kinds=("verification_completed",))[-1]["payload"]
    assert passed["verification"] == "passed"
    assert "verified_checkpoint" not in passed["input_binding"]
    assert not (store.control_root / "checkpoints").exists()


async def test_real_cli_exports_selected_event_with_current_host_opt_in(
    isolated_workspace: Path, tmp_path: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import subprocess
    import sys

    candidate = isolated_workspace / "output.bin"
    candidate.write_bytes(b"verified cli candidate")
    scope = PermissionScope((isolated_workspace,), (), frozenset({"execute_command"}), False)
    service = EngineeringRunService(
        isolated_workspace,
        retain_verified_candidate=True,
        permission_resolver=lambda _: scope,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
    )
    scripted_provider.enqueue_json(_native(text="Done."))
    command = RoleRunCommand(
        "cli-export", "director", "Validate candidate.", isolated_workspace, required_checks=("true",)
    )
    identity = await service.start(
        command, scope=scope, limits=RunLimits(None, 100000, 20000), permission_profile_id="recovery"
    )
    assert (await service.wait(identity, workspace=isolated_workspace)).verification == "passed"
    event = service.store.load_events(identity, kinds=("verification_completed",))[-1]
    candidate.write_bytes(b"current cli candidate")
    host = tmp_path / "host.json"
    policy = {
        "retain_verified_candidate": True,
        "permission_profiles": {
            "recovery": {
                "read_roots": ["."],
                "write_roots": [],
                "enabled_tools": ["execute_command"],
                "network_allowed": False,
            }
        },
    }
    host.write_text(json.dumps(policy), encoding="utf-8")
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[5]),
        "KERNELONE_ENGINEERING_HOST_CONFIG": str(host),
    }
    out = tmp_path / "cli-output"
    args = [
        sys.executable,
        "-m",
        "polaris.delivery.cli",
        "console",
        "--workspace",
        str(isolated_workspace),
        "--role",
        "director",
        "--engineering-action",
        "export",
        "--run-id",
        identity,
        "--destination",
        str(out),
        "--verification-seq",
        str(event["seq"]),
    ]

    def invoke():
        return subprocess.run(args, env=env, capture_output=True, encoding="utf-8", check=False, timeout=30)

    success = await asyncio.to_thread(invoke)
    assert success.returncode == 0, (success.stdout, success.stderr)
    assert json.loads(success.stdout)["destination"] == str(out)
    assert (out / "output.bin").read_bytes() == b"verified cli candidate"
    assert candidate.read_bytes() == b"current cli candidate"
    args[args.index(str(out))] = str(tmp_path / "denied")
    host.write_text(json.dumps({**policy, "retain_verified_candidate": False}), encoding="utf-8")
    denied = await asyncio.to_thread(invoke)
    assert denied.returncode == 1, (denied.stdout, denied.stderr)
    assert "retention" in json.loads(denied.stdout)["error"]
    assert not (tmp_path / "denied").exists()
