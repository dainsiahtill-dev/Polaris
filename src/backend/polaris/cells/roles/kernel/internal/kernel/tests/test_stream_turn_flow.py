"""Tests for the streaming role-turn flow owner."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.kernel import stream_turn_flow as flow
from polaris.cells.roles.profile.public.service import PromptFingerprint, RoleProfile, RoleTurnRequest


class _Publisher:
    instances: list[_Publisher] = []

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.__class__.instances.append(self)

    async def publish_stream_event(self, **payload: Any) -> None:
        self.events.append(dict(payload))


def _profile() -> RoleProfile:
    return RoleProfile(
        role_id="director",
        display_name="Director",
        description="Executes governed streaming turns.",
        model="gpt-test",
    )


def _kernel() -> Any:
    return SimpleNamespace(
        workspace="/tmp/workspace",
        _cached_tool_gateway="previous",
        _cached_gateway_profile="previous-profile",
    )


async def _collect_events(kernel: Any, request: RoleTurnRequest) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for event in flow.execute_stream_role_turn(
        kernel=kernel,
        role="director",
        request=request,
    ):
        events.append(event)
    return events


def test_execute_stream_role_turn_yields_fingerprint_then_transaction_events(monkeypatch: Any) -> None:
    profile = _profile()
    fingerprint = PromptFingerprint(core_hash="core", profile_fingerprint=profile.profile_fingerprint)
    captured: dict[str, Any] = {}
    _Publisher.instances.clear()

    def setup(*_: Any, **__: Any) -> Any:
        return SimpleNamespace(
            profile=profile,
            fingerprint=fingerprint,
            system_prompt="system",
        )

    async def stream_transaction(kernel: Any, **kwargs: Any) -> Any:
        captured["kernel"] = kernel
        captured.update(kwargs)
        yield {"type": "delta", "content": "done"}

    monkeypatch.setattr(flow, "resolve_stream_run_id", lambda run_id, workspace: run_id or "run:stream")
    monkeypatch.setattr(flow, "UEPEventPublisher", _Publisher)
    monkeypatch.setattr(flow, "build_role_turn_prompt_setup", setup)
    monkeypatch.setattr(flow, "execute_transaction_kernel_stream", stream_transaction)

    kernel = _kernel()
    request = RoleTurnRequest(message="hello")
    events = asyncio.run(_collect_events(kernel, request))

    assert request.run_id == "run:stream"
    assert kernel._cached_tool_gateway is None
    assert kernel._cached_gateway_profile is None
    assert events == [
        {"type": "fingerprint", "fingerprint": fingerprint},
        {"type": "delta", "content": "done"},
    ]
    assert captured["kernel"] is kernel
    assert captured["role"] == "director"
    assert captured["profile"] is profile
    assert captured["system_prompt"] == "system"
    assert captured["fingerprint"] is fingerprint
    assert captured["stream_run_id"] == "run:stream"
    assert _Publisher.instances[0].events[0]["event_type"] == "fingerprint"


def test_execute_stream_role_turn_converts_transaction_error_to_stream_event(monkeypatch: Any) -> None:
    profile = _profile()
    fingerprint = PromptFingerprint(core_hash="core", profile_fingerprint=profile.profile_fingerprint)
    _Publisher.instances.clear()

    def setup(*_: Any, **__: Any) -> Any:
        return SimpleNamespace(
            profile=profile,
            fingerprint=fingerprint,
            system_prompt="system",
        )

    async def stream_transaction(*_: Any, **__: Any) -> Any:
        if False:
            yield {}
        raise RuntimeError("stream failed")

    monkeypatch.setattr(flow, "resolve_stream_run_id", lambda run_id, workspace: run_id or "run:stream")
    monkeypatch.setattr(flow, "UEPEventPublisher", _Publisher)
    monkeypatch.setattr(flow, "build_role_turn_prompt_setup", setup)
    monkeypatch.setattr(flow, "execute_transaction_kernel_stream", stream_transaction)

    events = asyncio.run(_collect_events(_kernel(), RoleTurnRequest(message="hello")))

    assert events[-1] == {"type": "error", "error": "stream failed"}
    assert _Publisher.instances[0].events[-1] == {
        "workspace": "/tmp/workspace",
        "run_id": "run:stream",
        "role": "director",
        "event_type": "error",
        "payload": {"error": "stream failed"},
    }


def test_execute_stream_role_turn_propagates_setup_errors(monkeypatch: Any) -> None:
    def fail_setup(*_: Any, **__: Any) -> Any:
        raise ValueError("missing profile")

    monkeypatch.setattr(flow, "resolve_stream_run_id", lambda run_id, workspace: "run:stream")
    monkeypatch.setattr(flow, "UEPEventPublisher", _Publisher)
    monkeypatch.setattr(flow, "build_role_turn_prompt_setup", fail_setup)

    with pytest.raises(ValueError, match="missing profile"):
        asyncio.run(_collect_events(_kernel(), RoleTurnRequest(message="hello")))
