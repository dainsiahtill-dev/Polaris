"""Host-declared verifier fixtures bind real command receipts and cold observation."""

from __future__ import annotations

import asyncio
import hashlib
import shlex
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.bootstrap.config import EngineeringPermissionProfile, ServerConfig, Settings
from polaris.bootstrap.engineering_runtime import resolve_engineering_profile
from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification
from polaris.cells.roles.runtime.public.contracts.engineering_run import RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import isolated_workspace as isolated_workspace


@pytest.mark.parametrize(
    "damage", ["none", "changed", "missing", "symlink", "outside_grant", "candidate_owned", "unbound", "changed_during"]
)
async def test_pinned_check_uses_host_bytes_and_cold_evidence(
    isolated_workspace: Path, damage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = isolated_workspace / "candidate"
    workspace.mkdir()
    fixtures = isolated_workspace / "fixtures"
    fixtures.mkdir()
    source = b"print('independent check ran')\n"
    fixture = (workspace if damage == "candidate_owned" else fixtures) / "oracle.py"
    fixture.write_bytes(source)
    digest = hashlib.sha256(source).hexdigest()
    allowed = (workspace,) if damage == "outside_grant" else (workspace, fixtures)
    profile = EngineeringPermissionProfile(
        read_roots=tuple(map(str, allowed)),
        write_roots=(str(workspace),),
        enabled_tools=frozenset({"execute_command", "verify_project"}),
        verification_fixture_sha256={str(fixture): digest},
    )
    settings = Settings(workspace=workspace, server=ServerConfig(engineering_permission_profiles={"oracle": profile}))
    scope = resolve_engineering_profile(settings, workspace, "oracle")
    service = EngineeringRunService(workspace, permission_resolver=lambda _row: scope)
    check = "python3 -B " + shlex.quote(str(fixture))
    required = () if damage == "unbound" else (check,)
    command = RoleRunCommand(
        "pinned", "director", "Validate the current candidate.", workspace, required_checks=required
    )
    limits = RunLimits(None, 100_000, 8_000)
    service.store.create_run(
        command, scope=scope, limits=limits, engine_version=service.engine_version, request_key=None
    )
    saved = service.store.load_run(command.session_id, workspace=workspace)
    assert service._scope(saved) == scope
    assert not service._scope_within(scope, replace(scope, verification_fixture_sha256=()))
    if damage == "changed":
        fixture.write_bytes(b"pass\n")
    elif damage == "missing":
        fixture.unlink()
    elif damage == "symlink":
        fixture.rename(fixtures / "actual.py")
        fixture.symlink_to(fixtures / "actual.py")
    verifier = EngineeringVerification(
        workspace, store=service.store, scope=scope, limits=limits, cancel=asyncio.Event()
    )
    if damage == "changed_during":
        run_real_command = verifier.tools.execute

        async def host_edit_after_command(*args: Any, **kwargs: Any):
            receipt = await run_real_command(*args, **kwargs)
            fixture.write_bytes(b"pass\n")
            return receipt

        monkeypatch.setattr(verifier.tools, "execute", host_edit_after_command)
    checks = await verifier.run_verification((check,), session_id=command.session_id, workspace_revision=0)
    if damage != "none":
        assert verifier.current_verification(command.session_id) == "unknown"
        if damage == "changed_during":
            assert len(checks) == 1 and checks[0].ok and checks[0].exit_code == 0
            last = service.store.load_events(command.session_id, kinds=("verification_completed",))[-1]
            assert "verification fixture digest mismatch" in last["payload"]["detail"]
        else:
            assert not checks
        return
    assert len(checks) == 1 and checks[0].ok and "independent check ran" in checks[0].content
    cold = EngineeringRunService(workspace, permission_resolver=lambda _row: scope)
    verifier = cold._verification(scope, limits, asyncio.Event())
    assert verifier.current_verification(command.session_id) == "passed"
    observed = verifier.review_binding(command.session_id)["fixture_manifest"]
    assert observed[str(fixture)]["sha256"] == digest
    fixture.write_bytes(b"pass\n")
    assert verifier.current_verification(command.session_id) == "unknown"
    fixture.write_bytes(source)
    assert verifier.current_verification(command.session_id) == "unknown"
    checks = await verifier.run_verification((check,), session_id=command.session_id, workspace_revision=0)
    assert checks[0].ok and verifier.current_verification(command.session_id) == "passed"
