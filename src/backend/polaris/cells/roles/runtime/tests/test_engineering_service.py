"""Public service lifecycle with real SQLite and only a synthetic provider."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.kernelone.llm.engine.client import ResilientLLMClient


def _scope(workspace: Path) -> PermissionScope:
    return PermissionScope((workspace,), (), frozenset(), False)


def _limits() -> RunLimits:
    return RunLimits(time.time() + 10, 1_000_000, 64_000)


async def _terminal(service: Any, session_id: str, workspace: Path) -> dict[str, Any]:
    async with asyncio.timeout(8):
        while True:
            row = service.observe(session_id, workspace=workspace)
            if row["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                return cast(dict[str, Any], row)
            await asyncio.sleep(0.01)


async def test_start_reuses_request_and_keeps_reply_separate_from_acceptance(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    (isolated_workspace / "artifact.txt").write_text("ready", encoding="utf-8")
    (isolated_workspace / "test_artifact.py").write_text(
        "from pathlib import Path\nimport unittest\n"
        "class ArtifactTest(unittest.TestCase):\n"
        "    def test_actual_artifact(self):\n"
        "        self.assertEqual(Path('artifact.txt').read_text(encoding='utf-8'), 'ready')\n",
        encoding="utf-8",
    )
    scripted_provider.enqueue_json(
        {"choices": [{"message": {"role": "assistant", "content": "Implementation summary."}, "finish_reason": "stop"}]}
    )
    service = EngineeringRunService(
        isolated_workspace, client=scripted_provider.client("openai", isolated_workspace, monkeypatch)
    )
    command = RoleRunCommand(
        "service-first",
        "director",
        "Explain the work completed.",
        isolated_workspace,
        required_checks=("python3 -m unittest",),
    )
    # Task 4: a completed reply triggers the saved check through the actual
    # final verifier. Its execution authority remains an explicit host grant.
    scope = PermissionScope((isolated_workspace,), (), frozenset({"execute_command"}), False)
    limits = _limits()
    first = await service.start(command, scope=scope, limits=limits, request_key="same-request")
    second = await service.start(
        replace(command, session_id="fresh-allocation"), scope=scope, limits=limits, request_key="same-request"
    )
    assert first == second == command.session_id
    row = await _terminal(service, first, isolated_workspace)
    assert row["status"] == "completed", row
    assert row["text"] == "Implementation summary."
    assert row["verification"] == "passed"
    assert len(row["checks"]) == 1 and row["checks"][0]["exit_code"] == 0
    assert row["required_checks"] == ["python3 -m unittest"]
    assert row["engine_version"] == "engineering_v2"
    assert len(scripted_provider.requests) == 1


async def test_current_workspace_is_required_for_every_public_action(isolated_workspace: Path) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    service = EngineeringRunService(isolated_workspace)
    other = isolated_workspace / "another-workspace"
    other.mkdir()
    with pytest.raises(PermissionError):
        service.observe("unknown", workspace=other)
    with pytest.raises(PermissionError):
        await service.cancel("unknown", workspace=other)
    with pytest.raises(PermissionError):
        await service.resume("unknown", workspace=other)


async def test_default_service_assembles_real_kernelone_client(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    # Only model configuration is synthetic. The service must construct its own
    # actual client/executor and perform the HTTP request without client injection.
    scripted_provider.client("openai", isolated_workspace, monkeypatch)
    scripted_provider.enqueue_json(
        {"choices": [{"message": {"role": "assistant", "content": "Default client works."}, "finish_reason": "stop"}]}
    )
    service = EngineeringRunService(isolated_workspace)
    command = RoleRunCommand("service-default", "director", "Explain the task.", isolated_workspace)
    result = await service.run_role(command, scope=_scope(isolated_workspace), limits=_limits(), cancel=asyncio.Event())
    assert result.status == "completed", result.text
    assert result.text == "Default client works."
    assert len(scripted_provider.requests) == 1


class _WaitingProvider:
    """Only the model port is replaced; loop, store and workspace lock are real."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def invoke(self, _request: object) -> object:
        self.started.set()
        try:
            await asyncio.Event().wait()
            raise AssertionError("unreachable waiting provider completion")
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


async def test_pre_cancelled_public_role_never_dispatches_provider(isolated_workspace: Path) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    provider = _WaitingProvider()
    service = EngineeringRunService(isolated_workspace, client=cast(ResilientLLMClient, provider))
    cancel = asyncio.Event()
    cancel.set()
    command = RoleRunCommand("service-pre-cancelled", "director", "Do the task.", isolated_workspace)
    result = await service.run_role(command, scope=_scope(isolated_workspace), limits=_limits(), cancel=cancel)
    assert result.status == "cancelled"
    assert not provider.started.is_set()
    with service.store.acquire_workspace(isolated_workspace):
        assert not service.store.pending_tool_calls(command.session_id)


async def test_repeated_cancel_does_not_interrupt_drain_or_release_lock(isolated_workspace: Path) -> None:
    from polaris.cells.roles.runtime.internal.engineering_session import WorkspaceBusyError
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    draining, drained = asyncio.Event(), asyncio.Event()
    second_cancellation = asyncio.Event()

    class DrainingProvider(_WaitingProvider):
        async def invoke(self, _request: object) -> object:
            self.started.set()
            try:
                await asyncio.Event().wait()
                raise AssertionError("unreachable waiting provider completion")
            except asyncio.CancelledError:
                draining.set()
                try:
                    await drained.wait()
                except asyncio.CancelledError:
                    second_cancellation.set()
                    raise
                raise

    provider = DrainingProvider()
    service = EngineeringRunService(isolated_workspace, client=cast(ResilientLLMClient, provider))
    command = RoleRunCommand("service-double-cancel", "director", "Do the task.", isolated_workspace)
    await service.start(command, scope=_scope(isolated_workspace), limits=_limits())
    await asyncio.wait_for(provider.started.wait(), timeout=3)
    first = asyncio.create_task(service.cancel(command.session_id, workspace=isolated_workspace))
    await asyncio.wait_for(draining.wait(), timeout=3)
    second = asyncio.create_task(service.cancel(command.session_id, workspace=isolated_workspace))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    try:
        assert not second_cancellation.is_set()
        assert not first.done() and not second.done()
        with pytest.raises(WorkspaceBusyError), service.store.acquire_workspace(isolated_workspace):
            pytest.fail("workspace lock released before the active owner drained")
    finally:
        drained.set()
        await asyncio.gather(first, second)
    assert service.observe(command.session_id, workspace=isolated_workspace)["status"] == "cancelled"
    with service.store.acquire_workspace(isolated_workspace):
        pass


async def test_cancel_waits_for_current_work_before_releasing_workspace(isolated_workspace: Path) -> None:
    from polaris.cells.roles.runtime.internal.engineering_session import WorkspaceBusyError
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    provider = _WaitingProvider()
    service = EngineeringRunService(isolated_workspace, client=cast(ResilientLLMClient, provider))
    command = RoleRunCommand("service-cancel", "director", "Do the task.", isolated_workspace)
    await service.start(command, scope=_scope(isolated_workspace), limits=_limits())
    async with asyncio.timeout(3):
        await provider.started.wait()
    second = replace(command, session_id="second-writer", instruction="Another task.")
    with pytest.raises(WorkspaceBusyError):
        await service.start(second, scope=_scope(isolated_workspace), limits=_limits())
    await service.cancel(command.session_id, workspace=isolated_workspace)
    assert provider.cancelled.is_set()
    row = service.observe(command.session_id, workspace=isolated_workspace)
    assert row["status"] == "cancelled"
    with service.store.acquire_workspace(isolated_workspace):
        assert service.store.pending_tool_calls(command.session_id) == []


async def test_resume_requires_current_permission_policy_not_only_saved_grant(isolated_workspace: Path) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    service = EngineeringRunService(isolated_workspace)
    command = RoleRunCommand("service-interrupted", "director", "Do the task.", isolated_workspace)
    service.store.create_run(
        command, scope=_scope(isolated_workspace), limits=_limits(), engine_version="engineering_v2", request_key=None
    )
    service.store.update_run(command.session_id, status="interrupted")
    with pytest.raises(PermissionError, match="current permission"):
        await service.resume(command.session_id, workspace=isolated_workspace)
    assert service.observe(command.session_id, workspace=isolated_workspace)["status"] == "interrupted"
