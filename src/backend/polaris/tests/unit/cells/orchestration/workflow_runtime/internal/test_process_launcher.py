"""Tests for workflow_runtime internal process_launcher module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.cells.orchestration.workflow_runtime.internal.process_launcher import (
    ProcessLauncher,
    launch_director_once,
    launch_pm_once,
)
from polaris.cells.orchestration.workflow_runtime.public.process_launch import (
    ProcessLaunchRequest,
    RunMode,
)


class TestProcessLauncher:
    @pytest.fixture
    def launcher(self) -> ProcessLauncher:
        return ProcessLauncher()

    @pytest.mark.asyncio
    async def test_launch_validation_failure(self, launcher: ProcessLauncher) -> None:
        req = ProcessLaunchRequest(command=[], workspace=Path("."))
        result = await launcher.launch(req)
        assert result.success is False
        assert "Validation failed" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_launch_success(self, launcher: ProcessLauncher) -> None:
        mock_broker = MagicMock()
        mock_broker.launch_process = AsyncMock(
            return_value=MagicMock(
                success=True,
                handle=MagicMock(execution_id="e1", pid=123),
                error_message=None,
            )
        )
        launcher._broker = mock_broker
        req = ProcessLaunchRequest(command=["echo", "hi"], workspace=Path("."))
        result = await launcher.launch(req)
        assert result.success is True
        assert result.pid == 123

    @pytest.mark.asyncio
    async def test_launch_broker_failure(self, launcher: ProcessLauncher) -> None:
        mock_broker = MagicMock()
        mock_broker.launch_process = AsyncMock(
            return_value=MagicMock(
                success=False,
                handle=None,
                error_message="broker down",
            )
        )
        launcher._broker = mock_broker
        req = ProcessLaunchRequest(command=["echo", "hi"], workspace=Path("."))
        result = await launcher.launch(req)
        assert result.success is False
        assert "broker down" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_terminate_not_found(self, launcher: ProcessLauncher) -> None:
        result = await launcher.terminate({"id": "missing"})
        assert result is False

    @pytest.mark.asyncio
    async def test_wait_for_not_found(self, launcher: ProcessLauncher) -> None:
        result = await launcher.wait_for({"id": "missing"})
        assert result.success is False
        assert "Process not found" in (result.error_message or "")

    def test_build_utf8_env(self, launcher: ProcessLauncher) -> None:
        env = launcher._build_utf8_env()
        assert env["PYTHONUTF8"] == "1"
        assert env["PYTHONIOENCODING"] == "utf-8"

    def test_launch_pm_request(self, launcher: ProcessLauncher) -> None:
        req = launcher.launch_pm(Path("."), RunMode.SINGLE)
        assert req.name == "pm"
        assert req.role == "pm"

    def test_launch_director_request(self, launcher: ProcessLauncher) -> None:
        req = launcher.launch_director(Path("."), RunMode.ONE_SHOT)
        assert req.name == "director"
        assert req.role == "director"


class TestConvenienceFunctions:
    @pytest.mark.asyncio
    async def test_launch_pm_once(self) -> None:
        with patch(
            "polaris.cells.orchestration.workflow_runtime.internal.process_launcher.ProcessLauncher.launch",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True, pid=123),
        ):
            result = await launch_pm_once(Path("."))
            assert result.success is True

    @pytest.mark.asyncio
    async def test_launch_director_once(self) -> None:
        with patch(
            "polaris.cells.orchestration.workflow_runtime.internal.process_launcher.ProcessLauncher.launch",
            new_callable=AsyncMock,
            return_value=MagicMock(success=True, pid=123),
        ):
            result = await launch_director_once(Path("."))
            assert result.success is True
