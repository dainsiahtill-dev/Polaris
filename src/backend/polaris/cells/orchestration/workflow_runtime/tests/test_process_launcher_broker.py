"""Tests for the ProcessLauncher using execution_broker.

These tests verify that the ProcessLauncher correctly routes process
lifecycle through execution_broker rather than using subprocess directly.

Test metadata:
    cell: workflow_runtime
    layer: unit
    covers: polaris/cells/orchestration/workflow_runtime/internal/process_launcher.py
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.cells.orchestration.workflow_runtime.internal.process_launcher import (
    _LAUNCH_TIMEOUT_SECONDS as LAUNCH_TIMEOUT_SECONDS,
    ProcessLauncher,
)
from polaris.cells.orchestration.workflow_runtime.public.process_launch import (
    ProcessLaunchRequest,
    RunMode,
)
from polaris.cells.runtime.execution_broker.public.contracts import (
    ExecutionProcessHandleV1,
    ExecutionProcessLaunchResultV1,
    ExecutionProcessStatusV1,
    ExecutionProcessWaitResultV1,
    LaunchExecutionProcessCommandV1,
)

# --- Fixtures ---


@pytest.fixture
def mock_broker() -> MagicMock:
    """Create a mock ExecutionBrokerService."""
    broker = MagicMock()
    broker.launch_process = AsyncMock()
    broker.wait_process = AsyncMock()
    broker.terminate_process = AsyncMock()
    broker.list_active_processes = MagicMock(return_value=[])
    return broker


@pytest.fixture
def launcher(mock_broker: MagicMock) -> ProcessLauncher:
    """Create a ProcessLauncher with mock broker."""
    return ProcessLauncher(broker=mock_broker)


@pytest.fixture
def valid_request(tmp_path: Path) -> ProcessLaunchRequest:
    """Create a valid ProcessLaunchRequest."""
    return ProcessLaunchRequest(
        mode=RunMode.ONE_SHOT,
        command=["python", "-c", "print('hello')"],
        workspace=tmp_path,
        name="test-process",
        role="test",
    )


@pytest.fixture
def mock_handle() -> ExecutionProcessHandleV1:
    """Create a mock ExecutionProcessHandleV1."""
    return ExecutionProcessHandleV1(
        execution_id="test-exec-id-123",
        pid=12345,
        name="test-process",
        workspace=".",
    )


@pytest.fixture
def mock_launch_result(mock_handle: ExecutionProcessHandleV1) -> ExecutionProcessLaunchResultV1:
    """Create a mock successful launch result."""
    return ExecutionProcessLaunchResultV1(
        success=True,
        handle=mock_handle,
        error_message=None,
    )


# --- Tests for execution_broker migration ---


class TestProcessLauncherUsesExecutionBroker:
    """Tests verifying ProcessLauncher routes through execution_broker."""

    @pytest.mark.asyncio
    async def test_launcher_uses_execution_broker_not_subprocess(
        self,
        launcher: ProcessLauncher,
        valid_request: ProcessLaunchRequest,
        mock_broker: MagicMock,
        mock_launch_result: ExecutionProcessLaunchResultV1,
    ) -> None:
        """Verify launch() uses broker.launch_process() not subprocess.Popen."""
        mock_broker.launch_process.return_value = mock_launch_result

        # Run the launch
        result = await launcher.launch(valid_request)

        # Verify broker was called
        mock_broker.launch_process.assert_called_once()
        call_args = mock_broker.launch_process.call_args

        # Verify it's a LaunchExecutionProcessCommandV1
        command = call_args[0][0]
        assert isinstance(command, LaunchExecutionProcessCommandV1)

        # Verify command has correct attributes
        assert command.name == valid_request.name
        assert command.timeout_seconds == LAUNCH_TIMEOUT_SECONDS
        assert command.args == tuple(valid_request.command)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_launch_sends_correct_command_to_broker(
        self,
        launcher: ProcessLauncher,
        valid_request: ProcessLaunchRequest,
        mock_broker: MagicMock,
        mock_launch_result: ExecutionProcessLaunchResultV1,
    ) -> None:
        """Verify launch() constructs correct LaunchExecutionProcessCommandV1."""
        mock_broker.launch_process.return_value = mock_launch_result

        result = await launcher.launch(valid_request)

        assert result.success is True
        assert result.pid is not None, "result.pid should not be None"
        handle = mock_launch_result.handle
        assert handle is not None, "mock_launch_result.handle should not be None"
        assert handle.pid is not None, "handle.pid should not be None"
        assert result.pid == handle.pid

        # Get the command passed to broker
        call_args = mock_broker.launch_process.call_args
        command: LaunchExecutionProcessCommandV1 = call_args[0][0]

        # Verify command structure
        assert command.name == "test-process"
        assert command.timeout_seconds == 300  # _LAUNCH_TIMEOUT_SECONDS
        assert command.workspace == str(valid_request.workspace)
        command_metadata = command.metadata if isinstance(command.metadata, dict) else {}
        assert command_metadata.get("role") == "test"

    @pytest.mark.asyncio
    async def test_launch_includes_log_path_in_command(
        self,
        launcher: ProcessLauncher,
        tmp_path: Path,
        mock_broker: MagicMock,
        mock_launch_result: ExecutionProcessLaunchResultV1,
    ) -> None:
        """Verify log_path is passed to execution broker command."""
        mock_broker.launch_process.return_value = mock_launch_result

        log_file = tmp_path / "test.log"
        request = ProcessLaunchRequest(
            mode=RunMode.ONE_SHOT,
            command=["echo", "test"],
            workspace=tmp_path,
            name="log-test",
            role="test",
            log_path=log_file,
        )

        await launcher.launch(request)

        call_args = mock_broker.launch_process.call_args
        command: LaunchExecutionProcessCommandV1 = call_args[0][0]
        assert command.log_path == str(log_file)

    @pytest.mark.asyncio
    async def test_wait_for_uses_broker_wait_process(
        self,
        launcher: ProcessLauncher,
        mock_broker: MagicMock,
        mock_handle: ExecutionProcessHandleV1,
    ) -> None:
        """Verify wait_for() uses broker.wait_process() not subprocess.wait()."""
        # First launch to set up active process
        mock_launch_result = ExecutionProcessLaunchResultV1(
            success=True,
            handle=mock_handle,
            error_message=None,
        )
        mock_broker.launch_process.return_value = mock_launch_result

        request = ProcessLaunchRequest(
            mode=RunMode.ONE_SHOT,
            command=["sleep", "0.1"],
            workspace=Path("."),
            name="wait-test",
            role="test",
        )
        await launcher.launch(request)

        # Mock wait result
        mock_wait_result = ExecutionProcessWaitResultV1(
            handle=mock_handle,
            success=True,
            status=ExecutionProcessStatusV1.SUCCESS,
            exit_code=0,
            error_message=None,
        )
        mock_broker.wait_process.return_value = mock_wait_result

        process_handle = {"id": mock_handle.execution_id}
        await launcher.wait_for(process_handle, timeout=5.0)

        mock_broker.wait_process.assert_called_once()
        call_args = mock_broker.wait_process.call_args
        assert call_args[1]["timeout_seconds"] == 5.0

    @pytest.mark.asyncio
    async def test_terminate_uses_broker_terminate_process(
        self,
        launcher: ProcessLauncher,
        mock_broker: MagicMock,
        mock_handle: ExecutionProcessHandleV1,
    ) -> None:
        """Verify terminate() uses broker.terminate_process() not subprocess.terminate()."""
        # First launch
        mock_launch_result = ExecutionProcessLaunchResultV1(
            success=True,
            handle=mock_handle,
            error_message=None,
        )
        mock_broker.launch_process.return_value = mock_launch_result

        request = ProcessLaunchRequest(
            mode=RunMode.ONE_SHOT,
            command=["sleep", "10"],
            workspace=Path("."),
            name="terminate-test",
            role="test",
        )
        await launcher.launch(request)

        mock_broker.terminate_process.return_value = True

        process_handle = {"id": mock_handle.execution_id}
        await launcher.terminate(process_handle, timeout=5.0)

        mock_broker.terminate_process.assert_called_once()
        call_args = mock_broker.terminate_process.call_args
        assert call_args[1]["timeout_seconds"] == 5.0


class TestUTF8Environment:
    """Tests for UTF-8 environment enforcement."""

    def test_launch_sets_pythonutf8(self, launcher: ProcessLauncher) -> None:
        """Verify _build_utf8_env sets PYTHONUTF8=1."""
        env = launcher._build_utf8_env()
        assert env.get("PYTHONUTF8") == "1"

    def test_launch_sets_pythonioencoding(self, launcher: ProcessLauncher) -> None:
        """Verify _build_utf8_env sets PYTHONIOENCODING=utf-8."""
        env = launcher._build_utf8_env()
        assert env.get("PYTHONIOENCODING") == "utf-8"

    def test_launch_applies_env_overrides(self, launcher: ProcessLauncher) -> None:
        """Verify environment overrides are applied."""
        overrides = {"MY_VAR": "my_value", "PYTHONUTF8": "0"}
        env = launcher._build_utf8_env(overrides)
        assert env.get("MY_VAR") == "my_value"
        assert env.get("PYTHONUTF8") == "0"  # Override takes precedence


class TestLaunchTimeoutConfiguration:
    """Tests for _LAUNCH_TIMEOUT_SECONDS configuration."""

    def test_launch_timeout_is_300_seconds(self) -> None:
        """Verify default launch timeout is 300 seconds."""
        assert LAUNCH_TIMEOUT_SECONDS == 300

    @pytest.mark.asyncio
    async def test_launch_timeout_passed_to_broker(
        self,
        launcher: ProcessLauncher,
        valid_request: ProcessLaunchRequest,
        mock_broker: MagicMock,
        mock_launch_result: ExecutionProcessLaunchResultV1,
    ) -> None:
        """Verify timeout is correctly passed to execution broker."""
        mock_broker.launch_process.return_value = mock_launch_result

        await launcher.launch(valid_request)

        call_args = mock_broker.launch_process.call_args
        command: LaunchExecutionProcessCommandV1 = call_args[0][0]
        assert command.timeout_seconds == 300


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_launch_failure_returns_error_result(
        self,
        launcher: ProcessLauncher,
        valid_request: ProcessLaunchRequest,
        mock_broker: MagicMock,
    ) -> None:
        """Verify broker launch failure is properly handled."""
        mock_broker.launch_process.return_value = ExecutionProcessLaunchResultV1(
            success=False,
            handle=None,
            error_message="Broker error: process limit exceeded",
        )

        result = await launcher.launch(valid_request)

        assert result.success is False
        assert result.error_message is not None and "Broker error" in result.error_message

    @pytest.mark.asyncio
    async def test_wait_for_timeout_error(
        self,
        launcher: ProcessLauncher,
        mock_broker: MagicMock,
        mock_handle: ExecutionProcessHandleV1,
    ) -> None:
        """Verify TimeoutError from broker is handled."""
        # Setup
        mock_launch_result = ExecutionProcessLaunchResultV1(
            success=True,
            handle=mock_handle,
            error_message=None,
        )
        mock_broker.launch_process.return_value = mock_launch_result

        request = ProcessLaunchRequest(
            mode=RunMode.ONE_SHOT,
            command=["sleep", "100"],
            workspace=Path("."),
            name="timeout-test",
            role="test",
        )
        await launcher.launch(request)

        # Make wait_process raise TimeoutError
        mock_broker.wait_process.side_effect = TimeoutError("Process timed out")

        process_handle = {"id": mock_handle.execution_id}
        result = await launcher.wait_for(process_handle)

        assert result.success is False
        assert result.error_message is not None and "Timeout" in result.error_message

    def test_launch_history_max_size(self, launcher: ProcessLauncher) -> None:
        """Verify launch history is capped at 100 entries."""
        assert launcher._max_history == 100


class TestMetadata:
    """Tests for command metadata."""

    @pytest.mark.asyncio
    async def test_metadata_includes_role(
        self,
        launcher: ProcessLauncher,
        mock_broker: MagicMock,
        mock_launch_result: ExecutionProcessLaunchResultV1,
    ) -> None:
        """Verify role is included in command metadata."""
        mock_broker.launch_process.return_value = mock_launch_result

        request = ProcessLaunchRequest(
            mode=RunMode.LOOP,
            command=["python", "-c", "pass"],
            workspace=Path("."),
            name="metadata-test",
            role="director",
        )
        await launcher.launch(request)

        call_args = mock_broker.launch_process.call_args
        command: LaunchExecutionProcessCommandV1 = call_args[0][0]
        assert command.metadata["role"] == "director"

    @pytest.mark.asyncio
    async def test_metadata_includes_workspace(
        self,
        launcher: ProcessLauncher,
        mock_broker: MagicMock,
        mock_launch_result: ExecutionProcessLaunchResultV1,
        tmp_path: Path,
    ) -> None:
        """Verify workspace is included in command metadata."""
        mock_broker.launch_process.return_value = mock_launch_result

        request = ProcessLaunchRequest(
            mode=RunMode.SINGLE,
            command=["echo", "test"],
            workspace=tmp_path,
            name="ws-test",
            role="pm",
        )
        await launcher.launch(request)

        call_args = mock_broker.launch_process.call_args
        command: LaunchExecutionProcessCommandV1 = call_args[0][0]
        assert command.metadata["workspace"] == str(tmp_path)
