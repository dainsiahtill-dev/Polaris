"""Slow readonly observations must not monopolize the engineering event loop."""

from __future__ import annotations

import asyncio
import threading
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import isolated_workspace as isolated_workspace


async def test_qa_admission_keeps_event_loop_live(isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = isolated_workspace
    (workspace / "candidate.txt").write_text("candidate", encoding="utf-8")
    scope = PermissionScope((workspace,), (), frozenset({"read_file", "execute_command", "report_review"}), False)
    limits = RunLimits(time.time() + 30, 1000000, 64000)
    service = EngineeringRunService(workspace, permission_resolver=lambda _row: scope, require_qa=True)
    command = RoleRunCommand("qa-live", "director", "Check the existing candidate", workspace)
    service.store.create_run(command, scope=scope, limits=limits, engine_version="engineering_v2", request_key=None)
    verifier = EngineeringVerification(
        workspace, store=service.store, scope=scope, limits=limits, cancel=asyncio.Event()
    )
    heartbeat = threading.Event()
    observed: list[bool] = []
    original = verifier.review_binding

    def slow_binding(session_id: str) -> dict[str, object]:
        observed.append(heartbeat.wait(0.25))
        return original(session_id)

    class AdmissionReachedError(Exception):
        pass

    def stop_at_admission(*args: Any, **kwargs: Any) -> int:
        assert kwargs["kind"] == "qa_review_started"
        raise AdmissionReachedError

    monkeypatch.setattr(verifier, "review_binding", slow_binding)
    monkeypatch.setattr(service.store, "append_event", stop_at_admission)
    asyncio.get_running_loop().call_soon(heartbeat.set)
    with pytest.raises(AdmissionReachedError):
        await service._review_qa(command, scope=scope, limits=limits, cancel=asyncio.Event(), verifier=verifier)
    assert observed == [True], "QA admission blocked the service event loop"


async def test_observation_preserves_context_and_drains_before_cancellation() -> None:
    from polaris.cells.roles.runtime.public.engineering_service import _observe_in_io

    authority: ContextVar[str] = ContextVar("test_observation_authority", default="missing")
    token = authority.set("host-bound")
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    seen: list[str] = []

    def observe() -> str:
        seen.append(authority.get())
        entered.set()
        assert release.wait(3)
        finished.set()
        return "observed"

    task = asyncio.create_task(_observe_in_io(observe))
    try:
        async with asyncio.timeout(2):
            while not entered.is_set():
                await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done(), "Cancelled owner abandoned its observation"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
        assert seen == ["host-bound"]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        authority.reset(token)


async def test_cancel_does_not_require_a_fresh_candidate_scan(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = EngineeringRunService(isolated_workspace)
    scope = PermissionScope((isolated_workspace,), (), frozenset(), False)
    command = RoleRunCommand("cancel-unavailable-inputs", "director", "Inspect", isolated_workspace)
    service.store.create_run(
        command, scope=scope, limits=RunLimits(None, 1000000, 64000), engine_version="engineering_v2", request_key=None
    )

    def unavailable_scan(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("Cancellation must not traverse candidate input trees")

    monkeypatch.setattr(service, "observe", unavailable_scan)
    await service.cancel(command.session_id, workspace=isolated_workspace)
    row = service.store.load_run(command.session_id, workspace=isolated_workspace)
    assert row["status"] in {"cancelled", "interrupted"}
    assert not service.store.pending_tool_calls(command.session_id)
