"""Fine tool authorization fails closed across its injected owner boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.policy.permission.internal.permission_service import (
    PermissionService,
    create_permission_service,
    get_permission_service,
    get_permission_service_sync,
    reset_permission_service,
)
from polaris.cells.roles.profile.public.service import (
    Action,
    Policy,
    PolicyEffect,
    Resource,
    ResourceType,
    Subject,
    SubjectType,
)


class _Port:
    def __init__(self, workspace: str, *, failure: Exception | None = None) -> None:
        self.workspace = workspace
        self.failure = failure

    def role_permissions(self) -> dict[str, frozenset[str]]:
        return {"pm": frozenset({"tool:execute:read_file"})}

    def check_tool_permission(
        self, role_id: str, tool_name: str, tool_args: dict[str, Any] | None = None
    ) -> tuple[bool, str]:
        if self.failure is not None:
            raise self.failure
        return (True, "authorized") if role_id == "pm" else (False, "tool_policy_unknown_role")


@pytest.mark.asyncio
async def test_missing_tool_policy_cannot_turn_rbac_allow_into_tool_authority(tmp_path: Path) -> None:
    service = PermissionService(str(tmp_path))
    rbac = await service.check_permission(
        Subject(SubjectType.ROLE, "pm"), Resource(ResourceType.TOOL, "read_file"), Action.EXECUTE
    )
    assert rbac.allowed
    result = await service.check_tool_permission("pm", "read_file", {"file": "src/a.py"})
    assert not result.allowed
    assert "unavailable" in result.reason


@pytest.mark.asyncio
async def test_unknown_role_cannot_inherit_a_permissive_rbac_tool_policy(tmp_path: Path) -> None:
    service = PermissionService(str(tmp_path))
    await service.initialize()
    service._policies["unknown-allow"] = Policy(
        id="unknown-allow",
        name="Explicit test allow",
        effect=PolicyEffect.ALLOW,
        subjects=[Subject(SubjectType.ROLE, "missing-permission-role")],
        resources=[Resource(ResourceType.TOOL, "*")],
        actions=[Action.EXECUTE],
    )
    result = await service.check_tool_permission("missing-permission-role", "read_file", {})
    assert not result.allowed


@pytest.mark.asyncio
async def test_legacy_gateway_exception_cannot_return_rbac_allow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.kernel.public.service import RoleToolGateway

    service = PermissionService(str(tmp_path))
    await service.initialize()

    def unavailable(*args: Any, **kwargs: Any) -> tuple[bool, str]:
        raise RuntimeError("test-only gateway failure")

    monkeypatch.setattr(RoleToolGateway, "check_tool_permission", unavailable)
    result = await service.check_tool_permission("pm", "read_file", {"file": "src/a.py"})
    assert not result.allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", (RuntimeError("offline"), OSError("broken"), KeyError("missing")))
async def test_broken_tool_policy_never_falls_back_to_rbac_allow(tmp_path: Path, failure: Exception) -> None:
    service = PermissionService(str(tmp_path), tool_policy_port=_Port(str(tmp_path), failure=failure))
    result = await service.check_tool_permission("pm", "read_file", {"file": "src/a.py"})
    assert not result.allowed
    assert result.decision == "deny"
    assert "unavailable" in result.reason


@pytest.mark.asyncio
async def test_port_workspace_binding_is_checked_on_each_decision(tmp_path: Path) -> None:
    port = _Port(str(tmp_path))
    service = PermissionService(str(tmp_path), tool_policy_port=port)
    assert (await service.check_tool_permission("pm", "read_file")).allowed
    port.workspace = str(tmp_path / "foreign")
    result = await service.check_tool_permission("pm", "read_file")
    assert not result.allowed
    assert "workspace" in result.reason


@pytest.mark.asyncio
async def test_port_cannot_change_workspace_while_returning_an_allow(tmp_path: Path) -> None:
    class MovingPort(_Port):
        def check_tool_permission(self, *args: Any, **kwargs: Any) -> tuple[bool, str]:
            self.workspace = str(tmp_path / "foreign")
            return True, "foreign workspace allow"

    service = PermissionService(str(tmp_path), tool_policy_port=MovingPort(str(tmp_path)))
    result = await service.check_tool_permission("pm", "read_file")
    assert not result.allowed
    assert "workspace" in result.reason


@pytest.mark.asyncio
async def test_permission_projection_cannot_publish_an_inflight_foreign_workspace(tmp_path: Path) -> None:
    class MovingProjection(_Port):
        calls = 0

        def role_permissions(self) -> dict[str, frozenset[str]]:
            self.calls += 1
            if self.calls > 1:
                self.workspace = str(tmp_path / "foreign")
                return {"pm": frozenset({"tool:execute:foreign"})}
            return super().role_permissions()

    service = PermissionService(str(tmp_path), tool_policy_port=MovingProjection(str(tmp_path)))
    await service.initialize()
    assert await service.get_effective_permissions(Subject(SubjectType.ROLE, "pm")) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("broken_policy", (False, True))
async def test_audit_sink_failure_never_changes_the_final_permission_decision(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, broken_policy: bool
) -> None:
    class BrokenAudit:
        def record_decision(self, **kwargs: Any) -> None:
            raise RuntimeError("audit unavailable")

    port = _Port(str(tmp_path), failure=RuntimeError("policy unavailable") if broken_policy else None)
    service = PermissionService(str(tmp_path), audit_sink=BrokenAudit(), tool_policy_port=port)
    result = await service.check_tool_permission("pm", "read_file")
    assert result.allowed is not broken_policy
    assert "Audit write failed" in caplog.text


@pytest.mark.asyncio
async def test_explicit_rbac_deny_precedes_a_port_allow(tmp_path: Path) -> None:
    service = PermissionService(str(tmp_path), tool_policy_port=_Port(str(tmp_path)))
    await service.initialize()
    service._policies["explicit-deny"] = Policy(
        id="explicit-deny",
        name="Explicit deny",
        effect=PolicyEffect.DENY,
        subjects=[Subject(SubjectType.ROLE, "pm")],
        resources=[Resource(ResourceType.TOOL, "*")],
        actions=[Action.EXECUTE],
        priority=100,
    )
    result = await service.check_tool_permission("pm", "read_file")
    assert not result.allowed
    assert result.reason == "denied by policy: explicit-deny"


@pytest.mark.asyncio
async def test_cache_never_borrows_another_workspace_or_port_binding(tmp_path: Path) -> None:
    reset_permission_service()
    first_port, second_port = _Port(str(tmp_path)), _Port(str(tmp_path))
    first = await get_permission_service(str(tmp_path), tool_policy_port=first_port)
    assert first is await get_permission_service(str(tmp_path), tool_policy_port=first_port)
    assert first is get_permission_service_sync(str(tmp_path), tool_policy_port=first_port)
    assert first is not await get_permission_service(str(tmp_path), tool_policy_port=second_port)
    bare = await get_permission_service(str(tmp_path))
    assert bare is not first
    assert not (await bare.check_tool_permission("pm", "read_file")).allowed
    assert first is not await get_permission_service(str(tmp_path / "other"))
    isolated = create_permission_service(str(tmp_path), tool_policy_port=first_port)
    assert isolated is not first


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", (("yes", "bad bool"), (True, None), (True,), None))
async def test_malformed_port_decisions_cannot_authorize(tmp_path: Path, decision: Any) -> None:
    class InvalidDecision(_Port):
        def check_tool_permission(self, *args: Any, **kwargs: Any) -> Any:
            return decision

    service = PermissionService(str(tmp_path), tool_policy_port=InvalidDecision(str(tmp_path)))
    assert not (await service.check_tool_permission("pm", "read_file")).allowed


def test_cold_rbac_owner_does_not_import_role_kernel(tmp_path: Path) -> None:
    program = r"""
import asyncio
import importlib.abc
import json
import sys
attempts = []
class BlockKernel(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "polaris.cells.roles.kernel" or fullname.startswith("polaris.cells.roles.kernel."):
            attempts.append(fullname)
            raise ImportError("RoleKernel unavailable to policy owner")
        return None
sys.meta_path.insert(0, BlockKernel())
from polaris.cells.policy.permission.public.service import PermissionService
from polaris.cells.roles.profile.public.service import Action, Resource, ResourceType, Subject, SubjectType
async def main():
    service = PermissionService(sys.argv[1])
    result = await service.check_permission(Subject(SubjectType.ROLE, "pm"), Resource(ResourceType.FILE, "src/a.py"), Action.READ)
    assert result.allowed
    assert not (await service.check_tool_permission("pm", "read_file")).allowed
    assert attempts == [], attempts
    print(json.dumps({"rbac": result.allowed, "kernel_imports": attempts}))
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path)],
        capture_output=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {"rbac": True, "kernel_imports": []}
