"""Real continuity pipeline owners must follow the supplied session identity."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from typing import Any

import pytest
from polaris.kernelone.context.context_os import StateFirstContextOS
from polaris.kernelone.context.session_continuity import SessionContinuityEngine, SessionContinuityRequest
from polaris.kernelone.errors import ValidationError


def _request(content: str) -> SessionContinuityRequest:
    return SessionContinuityRequest(
        session_id="session-a",
        role="director",
        workspace="/workspace-a",
        messages=({"role": "user", "content": content, "sequence": 1},),
    )


async def _interleave(
    monkeypatch: pytest.MonkeyPatch,
    first: SessionContinuityRequest,
    second: SessionContinuityRequest,
) -> tuple[Any, Any]:
    engine = SessionContinuityEngine()
    computed = threading.Event()
    release = threading.Event()
    owners: set[StateFirstContextOS] = set()
    original = StateFirstContextOS._project_via_pipeline_sync

    def delayed(self: StateFirstContextOS, *args: Any, **kwargs: Any) -> Any:
        owners.add(self)
        result = original(self, *args, **kwargs)
        messages = args[1]
        if messages[0]["content"] == "OLDER SESSION INPUT" and not computed.is_set():
            computed.set()
            assert release.wait(10), "controlled older computation was not released"
        return result

    monkeypatch.setattr(StateFirstContextOS, "_project_via_pipeline_sync", delayed)
    old = asyncio.create_task(engine.project(first))
    try:
        assert await asyncio.to_thread(computed.wait, 10)
        current = await engine.project(second)
        release.set()
        old_result = (await asyncio.gather(old, return_exceptions=True))[0]
        if isinstance(old_result, ValidationError):
            # The caller supplies fresh current input rather than replaying the
            # rejected older request or converting it to an empty fallback.
            recovered = await engine.project(
                replace(
                    second,
                    messages=({"role": "user", "content": "FRESH CURRENT RETRY", "sequence": 2},),
                    session_context_config=current.persisted_context_config,
                )
            )
            assert "FRESH CURRENT RETRY" in str(recovered.context_os_projection.snapshot)
            assert "NEWER SESSION INPUT" in str(recovered.context_os_projection.snapshot)
            assert "OLDER SESSION INPUT" not in str(recovered.context_os_projection.snapshot)
        return old_result, current
    finally:
        release.set()
        await asyncio.gather(old, return_exceptions=True)
        await asyncio.gather(*(owner.close() for owner in owners))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,new_value", [("session_id", "session-b"), ("workspace", "/workspace-b"), ("role", "qa")]
)
async def test_different_session_owners_do_not_supersede_each_other(
    monkeypatch: pytest.MonkeyPatch, field: str, new_value: str
) -> None:
    old = _request("OLDER SESSION INPUT")
    latest = replace(_request("NEWER SESSION INPUT"), **{field: new_value})
    old_result, new_result = await _interleave(monkeypatch, old, latest)
    assert not isinstance(old_result, BaseException), "independent identity lost its own projection"
    assert "OLDER SESSION INPUT" in str(old_result.context_os_projection.snapshot)
    assert "NEWER SESSION INPUT" not in str(old_result.context_os_projection.snapshot)
    assert "NEWER SESSION INPUT" in str(new_result.context_os_projection.snapshot)
    assert "OLDER SESSION INPUT" not in str(new_result.context_os_projection.snapshot)


@pytest.mark.asyncio
async def test_same_session_keeps_currentness_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    old_result, latest = await _interleave(
        monkeypatch, _request("OLDER SESSION INPUT"), _request("NEWER SESSION INPUT")
    )
    assert isinstance(old_result, ValidationError)
    assert old_result.constraint == "projection_input_version"
    assert "NEWER SESSION INPUT" in str(latest.context_os_projection.snapshot)


@pytest.mark.asyncio
async def test_same_session_serial_restore_keeps_existing_history() -> None:
    engine = SessionContinuityEngine()
    try:
        first = await engine.project(_request("SERIAL ORIGINAL HISTORY"))
        second = await engine.project(
            replace(
                _request("SERIAL LATEST INPUT"),
                session_context_config=first.persisted_context_config,
            )
        )
        assert "SERIAL ORIGINAL HISTORY" in str(second.context_os_projection.snapshot)
        assert "SERIAL LATEST INPUT" in str(second.context_os_projection.snapshot)
        assert len(engine._context_os_cache) == 1
    finally:
        await asyncio.gather(*(owner.close() for owner in engine._context_os_cache.values()))


@pytest.mark.asyncio
async def test_anonymous_summary_view_is_closed_and_cannot_replace_session_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = SessionContinuityEngine()
    closed: set[StateFirstContextOS] = set()
    original_close = StateFirstContextOS.close

    async def close(self: StateFirstContextOS) -> None:
        closed.add(self)
        await original_close(self)

    monkeypatch.setattr(StateFirstContextOS, "close", close)
    try:
        current = await engine.project(_request("CURRENT SESSION VIEW"))
        owners = list(engine._context_os_cache.values())
        assert len(owners) == 1
        snapshot = owners[0]._working_state_manager.snapshot()
        pack = await engine.build_pack([{"role": "user", "content": "UNSCOPED SUMMARY INPUT"}])
        assert pack is not None
        assert owners[0]._working_state_manager.snapshot() == snapshot
        assert "CURRENT SESSION VIEW" in str(current.context_os_projection.snapshot)
        assert owners[0] not in closed
        assert len(closed) >= 2  # Nested continuity summary plus the explicit anonymous view.
    finally:
        await asyncio.gather(*(owner.close() for owner in engine._context_os_cache.values()))
