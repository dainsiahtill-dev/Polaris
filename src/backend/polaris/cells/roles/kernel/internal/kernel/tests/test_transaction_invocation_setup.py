from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast

import pytest
from polaris.cells.roles.kernel.internal.kernel import transaction_invocation_setup as setup
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.delivery_mode import _ensure_context_delivery_mode_marker
from polaris.cells.roles.kernel.internal.transaction.tool_surface import TransactionToolSurfacePlan
from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest


@dataclass
class _Request:
    message: str = "build"
    workspace: str = "."
    context_override: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Profile:
    role_id: str = "director"
    model: str = "test-model"
    provider_id: str = "test-provider"
    tool_policy: Any = field(default_factory=lambda: SimpleNamespace(whitelist=["write_file"]))


class _Gateway:
    async def build_context(self, _context_request: Any, *, system_prompt: str) -> SimpleNamespace:
        return SimpleNamespace(
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": "task"}],
            token_estimate=17,
        )


@pytest.mark.asyncio
async def test_turn_setup_restores_delivery_marker_before_tool_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    captured: dict[str, Any] = {}
    gateway = _Gateway()

    monkeypatch.setattr(
        setup.ToolLoopController,
        "from_request",
        staticmethod(
            lambda *, request, profile: SimpleNamespace(build_context_request=lambda: {"role": profile.role_id})
        ),
    )

    import polaris.cells.roles.kernel.public.service as public_service

    monkeypatch.setattr(public_service, "RoleContextGateway", lambda *_args, **_kwargs: gateway)

    def fake_delivery_marker(
        messages: list[dict[str, Any]],
        _context_override: Any,
        _message: Any,
    ) -> list[dict[str, Any]]:
        calls.append("delivery")
        return [*messages, {"role": "system", "content": "delivery-marker"}]

    def fake_platform_metadata(
        messages: list[dict[str, Any]],
        _context_override: Any,
    ) -> list[dict[str, Any]]:
        calls.append("platform")
        return [*messages, {"role": "system", "content": "platform-metadata"}]

    def fake_tool_surface(**kwargs: Any) -> TransactionToolSurfacePlan:
        captured.update(kwargs)
        return TransactionToolSurfacePlan(tool_definitions=[{"name": "write_file"}], runtime_tool_policy_audit={})

    monkeypatch.setattr(setup, "_ensure_context_delivery_mode_marker", fake_delivery_marker)
    monkeypatch.setattr(setup, "_ensure_platform_tool_contract_metadata", fake_platform_metadata)
    monkeypatch.setattr(setup, "plan_transaction_tool_surface", fake_tool_surface)

    result = await setup.build_transaction_invocation_setup(
        kernel=RoleExecutionKernel.create_default(workspace="."),
        role="director",
        profile=cast(RoleProfile, _Profile()),
        request=cast(RoleTurnRequest, _Request()),
        system_prompt="sys",
        mode="turn",
        restore_delivery_mode_marker=True,
    )

    assert calls == ["delivery", "platform"]
    assert captured["mode"] == "turn"
    assert captured["messages"] == result.messages
    assert result.context_gateway is gateway
    assert result.tool_surface.tool_definitions == [{"name": "write_file"}]


@pytest.mark.asyncio
async def test_stream_setup_skips_delivery_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        setup.ToolLoopController,
        "from_request",
        staticmethod(
            lambda *, request, profile: SimpleNamespace(build_context_request=lambda: {"role": profile.role_id})
        ),
    )

    import polaris.cells.roles.kernel.public.service as public_service

    monkeypatch.setattr(public_service, "RoleContextGateway", lambda *_args, **_kwargs: _Gateway())

    def fake_delivery_marker(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        calls.append("delivery")
        return []

    def fake_platform_metadata(messages: list[dict[str, Any]], _context_override: Any) -> list[dict[str, Any]]:
        calls.append("platform")
        return messages

    monkeypatch.setattr(setup, "_ensure_context_delivery_mode_marker", fake_delivery_marker)
    monkeypatch.setattr(setup, "_ensure_platform_tool_contract_metadata", fake_platform_metadata)
    monkeypatch.setattr(
        setup,
        "plan_transaction_tool_surface",
        lambda **_kwargs: TransactionToolSurfacePlan(tool_definitions=[], runtime_tool_policy_audit={}),
    )

    await setup.build_transaction_invocation_setup(
        kernel=RoleExecutionKernel.create_default(workspace="."),
        role="director",
        profile=cast(RoleProfile, _Profile()),
        request=cast(RoleTurnRequest, _Request()),
        system_prompt="sys",
        mode="stream",
        restore_delivery_mode_marker=False,
    )

    assert calls == ["platform"]


def test_explicit_analyze_only_context_restores_authoritative_marker() -> None:
    messages = [{"role": "user", "content": "Produce the implementation blueprint."}]

    result = _ensure_context_delivery_mode_marker(
        messages,
        {"delivery_mode": "analyze_only"},
    )

    assert result[-1]["content"].startswith("[mode:analyze_only]\n")
