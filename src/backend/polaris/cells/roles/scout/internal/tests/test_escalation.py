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
