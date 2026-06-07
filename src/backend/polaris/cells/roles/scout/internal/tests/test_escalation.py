"""Tests for the scout escalation bridge + scout profile resolvability (UTF-8).

No real LLM calls: ``RoleRuntimeService.execute_role_session`` is monkeypatched.
"""

from __future__ import annotations

from typing import Any

import pytest
from polaris.cells.roles.scout.internal.escalation import escalate_probe
from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1


class _FakeResult:
    """Minimal stand-in for ``RoleExecutionResultV1`` (only ``output`` is read)."""

    output = "ESCALATED FINDINGS"
    metadata: dict[str, Any] = {}
    error_message = ""


@pytest.mark.asyncio
async def test_escalate_probe_invokes_role_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_exec(self: Any, command: Any) -> _FakeResult:
        captured["role"] = command.role
        captured["session_id"] = command.session_id
        captured["user_message"] = command.user_message
        return _FakeResult()

    from polaris.cells.roles.runtime.public import service as runtime_service

    monkeypatch.setattr(runtime_service.RoleRuntimeService, "execute_role_session", fake_exec)

    out = await escalate_probe(ScoutProbeTargetV1(query="where is payment", allow_escalation=True), workspace=".")

    assert captured["role"] == "scout"
    assert captured["user_message"] == "where is payment"
    assert captured["session_id"].startswith("scout-probe-")
    assert out == "ESCALATED FINDINGS"


def test_scout_profile_resolvable_via_core_roles_yaml() -> None:
    """Scout must be resolvable through the SAME loader path the core roles use.

    ``load_core_roles()`` loads ``config/roles/core_roles.yaml`` via
    ``RoleProfileRegistry.load_from_yaml``. We exercise that loader against the
    canonical config on a fresh registry (order-independent vs. the global
    singleton) and assert scout resolves with a non-empty system-prompt id.
    """
    from pathlib import Path

    from polaris.cells.roles.profile.internal.registry import RoleProfileRegistry

    config_path = Path(__file__).resolve().parents[3] / "profile" / "config" / "roles" / "core_roles.yaml"
    assert config_path.exists(), f"core_roles.yaml not found at {config_path}"

    reg = RoleProfileRegistry()
    reg.load_from_yaml(config_path)

    assert reg.has_role("scout")
    profile = reg.get_profile("scout")
    assert profile is not None
    # A real, non-empty system-prompt id is required for the kernel to compose a turn.
    assert profile.prompt_policy.core_template_id
    # Scout must stay auxiliary, never promoted into the core-role set.
    assert "scout" not in RoleProfileRegistry.CORE_ROLES


def test_scout_profile_registered_after_load_core_roles() -> None:
    """The global ``load_core_roles()`` entry point also resolves scout."""
    from polaris.cells.roles.profile.internal.registry import load_core_roles, registry

    load_core_roles()
    assert registry.has_role("scout")
    profile = registry.get_profile("scout")
    assert profile is not None
    assert profile.prompt_policy.core_template_id
