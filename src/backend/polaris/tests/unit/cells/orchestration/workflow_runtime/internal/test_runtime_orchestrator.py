"""Tests for workflow_runtime internal runtime_orchestrator module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polaris.cells.orchestration.workflow_runtime.internal.event_stream import EventStream
from polaris.cells.orchestration.workflow_runtime.internal.runtime_orchestrator import (
    RuntimeOrchestrator,
    ServiceDefinition,
    ServiceHandle,
    ServiceState,
)
from polaris.cells.orchestration.workflow_runtime.public.process_launch import RunMode


class TestServiceDefinition:
    def test_to_launch_request(self) -> None:
        definition = ServiceDefinition(
            name="pm",
            command=["python", "-m", "pm"],
            working_dir=Path("."),
            run_mode=RunMode.SINGLE,
        )
        req = definition.to_launch_request()
        assert req.name == "pm"
        assert req.role == "pm"


class TestServiceHandle:
    def test_is_running(self) -> None:
        handle = ServiceHandle(
            id="s1",
            definition=MagicMock(),
            state=ServiceState.RUNNING,
        )
        assert handle.is_running is True
        handle.state = ServiceState.COMPLETED
        assert handle.is_running is False

    def test_is_completed(self) -> None:
        handle = ServiceHandle(
            id="s1",
            definition=MagicMock(),
            state=ServiceState.FAILED,
        )
        assert handle.is_completed is True


class TestRuntimeOrchestrator:
    @pytest.fixture
    def orchestrator(self) -> RuntimeOrchestrator:
        return RuntimeOrchestrator(event_stream=EventStream())

    @pytest.mark.asyncio
    async def test_shutdown_empty(self, orchestrator: RuntimeOrchestrator) -> None:
        await orchestrator.shutdown()
        assert orchestrator.list_all() == []

    @pytest.mark.asyncio
    async def test_submit_and_status(self, orchestrator: RuntimeOrchestrator) -> None:
        with patch.object(
            orchestrator._launcher,
            "launch",
            new_callable=AsyncMock,
            return_value=MagicMock(
                is_success=MagicMock(return_value=True),
                pid=123,
                process_handle={"id": "p1"},
            ),
        ):
            definition = ServiceDefinition(
                name="pm",
                command=["echo", "hi"],
                working_dir=Path("."),
            )
            handle = await orchestrator.submit(definition)
            assert handle.definition.name == "pm"
            status = await orchestrator.status(handle)
            assert status["name"] == "pm"

    @pytest.mark.asyncio
    async def test_terminate_not_running(self, orchestrator: RuntimeOrchestrator) -> None:
        handle = ServiceHandle(
            id="s1",
            definition=MagicMock(),
            state=ServiceState.COMPLETED,
        )
        result = await orchestrator.terminate(handle)
        assert result is True

    @pytest.mark.asyncio
    async def test_list_active(self, orchestrator: RuntimeOrchestrator) -> None:
        with patch.object(
            orchestrator._launcher,
            "launch",
            new_callable=AsyncMock,
            return_value=MagicMock(
                is_success=MagicMock(return_value=True),
                pid=123,
                process_handle={"id": "p1"},
            ),
        ):
            definition = ServiceDefinition(
                name="pm",
                command=["echo", "hi"],
                working_dir=Path("."),
            )
            handle = await orchestrator.submit(definition)
            active = orchestrator.list_active()
            assert len(active) == 1
            assert active[0].id == handle.id

    @pytest.mark.asyncio
    async def test_launch_pm(self, orchestrator: RuntimeOrchestrator) -> None:
        with patch.object(
            orchestrator._launcher,
            "launch_pm",
            return_value=MagicMock(
                command=["python", "-m", "pm"],
                env_vars={},
            ),
        ), patch.object(
            orchestrator._launcher,
            "launch",
            new_callable=AsyncMock,
            return_value=MagicMock(
                is_success=MagicMock(return_value=True),
                pid=123,
                process_handle={"id": "p1"},
            ),
        ):
            handle = await orchestrator.launch_pm(Path("."))
            assert handle.definition.name == "pm"

    @pytest.mark.asyncio
    async def test_launch_director(self, orchestrator: RuntimeOrchestrator) -> None:
        with patch.object(
            orchestrator._launcher,
            "launch_director",
            return_value=MagicMock(
                command=["python", "-m", "director"],
                env_vars={},
            ),
        ), patch.object(
            orchestrator._launcher,
            "launch",
            new_callable=AsyncMock,
            return_value=MagicMock(
                is_success=MagicMock(return_value=True),
                pid=123,
                process_handle={"id": "p1"},
            ),
        ):
            handle = await orchestrator.launch_director(Path("."))
            assert handle.definition.name == "director"
