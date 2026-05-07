"""Unit tests for orchestration.pm_planning service execution_broker integration.

These tests verify that pm_planning service correctly uses the execution_broker
cell for process lifecycle management, as required by the migration specification.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.cells.orchestration.pm_planning.service import PMService
from polaris.cells.runtime.execution_broker.public.contracts import (
    ExecutionProcessHandleV1,
    ExecutionProcessLaunchResultV1,
)
from polaris.kernelone.storage import StorageLayout


class TestPMServiceExecutionBroker:
    """Tests verifying PMService uses execution_broker correctly."""

    @pytest.fixture
    def mock_broker(self) -> MagicMock:
        """Create a mock execution broker service."""
        broker = MagicMock()
        broker.launch_process = AsyncMock()
        broker.resolve_runtime_process = MagicMock()
        return broker

    @pytest.fixture
    def mock_settings(self, tmp_path: Path) -> MagicMock:
        """Create mock settings for PMService."""
        settings = MagicMock()
        settings.workspace = str(tmp_path)
        settings.runtime_base = str(tmp_path / "runtime")
        settings.json_log_path = None
        settings.pm_script_path = str(tmp_path / "pm_script.py")
        settings.timeout = 0
        settings.pm.model = "test-model"
        settings.llm.model = "test-model"
        settings.llm.timeout = 300
        settings.pm.agents_approval_mode = "manual"
        settings.pm.agents_approval_timeout = 30
        settings.pm.max_failures = 3
        settings.pm.max_blocked = 5
        settings.pm.max_same = 2
        settings.pm.blocked_strategy = "skip"
        settings.pm.blocked_degrade_max_retries = 1
        settings.pm.show_output = False
        settings.pm.runs_director = False
        settings.director_script_path = tmp_path / "loop-director.py"
        settings.loop_module_dir = str(tmp_path / "modules")
        settings.runtime.ramdisk_root = None
        settings.director = MagicMock()
        settings.director.execution_mode = "single"
        settings.director.max_parallel_tasks = 1
        settings.director.ready_timeout_seconds = 30
        settings.director.claim_timeout_seconds = 10
        settings.director.phase_timeout_seconds = 300
        settings.director.complete_timeout_seconds = 600
        settings.director.task_timeout_seconds = 120
        return settings

    @pytest.mark.asyncio
    async def test_spawn_process_uses_execution_broker(
        self,
        mock_broker: MagicMock,
        mock_settings: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify _spawn_process calls execution_broker.launch_process with correct command."""
        # Create a dummy pm script to prevent real subprocess spawn
        pm_script = tmp_path / "pm_script.py"
        pm_script.write_text("import sys; print('pm-test'); sys.exit(0)", encoding="utf-8")

        # Setup mock process that simulates a running process
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.poll = MagicMock(return_value=None)

        # Setup mock launch result
        mock_handle = ExecutionProcessHandleV1(
            execution_id="test-exec-id-001",
            pid=12345,
            name="pm-service",
            workspace=str(tmp_path),
            log_path=str(tmp_path / "pm.log"),
            metadata={"service": "pm_planning"},
        )
        mock_broker.launch_process.return_value = ExecutionProcessLaunchResultV1(
            success=True,
            handle=mock_handle,
        )
        mock_broker.resolve_runtime_process.return_value = mock_process

        # Patch get_execution_broker_service to return our mock
        with patch(
            "polaris.cells.orchestration.pm_planning.service.get_execution_broker_service",
            return_value=mock_broker,
        ):
            service = PMService(settings=mock_settings)
            service._storage = StorageLayout(
                Path(mock_settings.workspace),
                Path(mock_settings.runtime_base),
            )

            cmd = [sys.executable, str(pm_script), "--workspace", str(tmp_path)]
            log_path = str(tmp_path / "pm.log")

            handle = await service._spawn_process(cmd, log_path)

            # Verify broker was called
            mock_broker.launch_process.assert_called_once()
            call_args = mock_broker.launch_process.call_args

            # Extract the command from call args
            command = call_args[0][0]  # First positional argument

            # Verify command structure
            assert command.name == "pm-service"
            assert len(command.args) >= 2
            assert command.args[0] == sys.executable
            assert command.args[1] == str(pm_script)

            # Verify metadata includes required fields
            assert command.metadata["service"] == "pm_planning"
            assert "workspace" in command.metadata
            assert command.workspace == str(tmp_path)

            # Verify handle is returned correctly
            assert handle.process is mock_process
            assert handle.execution_id == "test-exec-id-001"
            assert handle.pid == 12345

    @pytest.mark.asyncio
    async def test_spawn_process_fails_when_broker_fails(
        self,
        mock_broker: MagicMock,
        mock_settings: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify _spawn_process raises RuntimeError when broker launch fails."""
        pm_script = tmp_path / "pm_script.py"
        pm_script.write_text("import sys; sys.exit(1)", encoding="utf-8")

        # Setup mock to return failure
        mock_broker.launch_process.return_value = ExecutionProcessLaunchResultV1(
            success=False,
            error_message="Broker launch failed: test error",
        )

        with patch(
            "polaris.cells.orchestration.pm_planning.service.get_execution_broker_service",
            return_value=mock_broker,
        ):
            service = PMService(settings=mock_settings)
            service._storage = StorageLayout(
                Path(mock_settings.workspace),
                Path(mock_settings.runtime_base),
            )

            cmd = [sys.executable, str(pm_script)]
            log_path = str(tmp_path / "pm.log")

            with pytest.raises(RuntimeError, match="Broker launch failed"):
                await service._spawn_process(cmd, log_path)

    @pytest.mark.asyncio
    async def test_spawn_process_includes_utf8_env(
        self,
        mock_broker: MagicMock,
        mock_settings: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify _spawn_process sets PYTHONIOENCODING=utf-8 in env."""
        pm_script = tmp_path / "pm_script.py"
        pm_script.write_text("import sys; sys.exit(0)", encoding="utf-8")

        mock_process = MagicMock()
        mock_process.pid = 99999
        mock_process.poll = MagicMock(return_value=None)

        mock_handle = ExecutionProcessHandleV1(
            execution_id="test-exec-id-002",
            pid=99999,
            name="pm-service",
            workspace=str(tmp_path),
        )
        mock_broker.launch_process.return_value = ExecutionProcessLaunchResultV1(
            success=True,
            handle=mock_handle,
        )
        mock_broker.resolve_runtime_process.return_value = mock_process

        with patch(
            "polaris.cells.orchestration.pm_planning.service.get_execution_broker_service",
            return_value=mock_broker,
        ):
            service = PMService(settings=mock_settings)
            service._storage = StorageLayout(
                Path(mock_settings.workspace),
                Path(mock_settings.runtime_base),
            )

            cmd = [sys.executable, str(pm_script)]
            await service._spawn_process(cmd, str(tmp_path / "pm.log"))

            call_args = mock_broker.launch_process.call_args
            command = call_args[0][0]

            # Verify UTF-8 encoding is set (case-insensitive check)
            assert "PYTHONIOENCODING" in command.env
            assert command.env["PYTHONIOENCODING"].upper() == "UTF-8"

            # Verify workspace env var is set
            assert "KERNELONE_WORKSPACE" in command.env
            assert command.env["KERNELONE_WORKSPACE"] == str(tmp_path)

    @pytest.mark.asyncio
    async def test_spawn_process_sets_timeout(
        self,
        mock_broker: MagicMock,
        mock_settings: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify _spawn_process respects KERNELONE_PM_PROCESS_TIMEOUT_SECONDS."""
        pm_script = tmp_path / "pm_script.py"
        pm_script.write_text("import sys; sys.exit(0)", encoding="utf-8")

        mock_process = MagicMock()
        mock_process.pid = 11111
        mock_process.poll = MagicMock(return_value=None)

        mock_handle = ExecutionProcessHandleV1(
            execution_id="test-exec-id-003",
            pid=11111,
            name="pm-service",
            workspace=str(tmp_path),
        )
        mock_broker.launch_process.return_value = ExecutionProcessLaunchResultV1(
            success=True,
            handle=mock_handle,
        )
        mock_broker.resolve_runtime_process.return_value = mock_process

        with (
            patch(
                "polaris.cells.orchestration.pm_planning.service.get_execution_broker_service",
                return_value=mock_broker,
            ),
            patch.dict("os.environ", {"KERNELONE_PM_PROCESS_TIMEOUT_SECONDS": "7200"}),
        ):
            service = PMService(settings=mock_settings)
            service._storage = StorageLayout(
                Path(mock_settings.workspace),
                Path(mock_settings.runtime_base),
            )

            cmd = [sys.executable, str(pm_script)]
            await service._spawn_process(cmd, str(tmp_path / "pm.log"))

            call_args = mock_broker.launch_process.call_args
            command = call_args[0][0]

            # Verify timeout is set to 7200 seconds
            assert command.timeout_seconds == 7200.0

    def test_build_command_uses_bounded_pm_planning_timeout(
        self,
        mock_settings: MagicMock,
    ) -> None:
        """PM planning should not disable the LLM timeout when settings.timeout is unset."""
        service = PMService(settings=mock_settings)

        cmd = service._build_command(loop_mode=False)
        timeout_index = cmd.index("--timeout") + 1

        assert cmd[timeout_index] == "60"

    def test_build_command_respects_explicit_pm_planning_timeout_env(
        self,
        mock_settings: MagicMock,
    ) -> None:
        """An explicit PM planning timeout env var should override global defaults."""
        with patch.dict("os.environ", {"KERNELONE_PM_PLANNING_TIMEOUT_SECONDS": "17"}):
            service = PMService(settings=mock_settings)

            cmd = service._build_command(loop_mode=False)

        timeout_index = cmd.index("--timeout") + 1
        assert cmd[timeout_index] == "17"

    def test_build_command_respects_settings_timeout(
        self,
        mock_settings: MagicMock,
    ) -> None:
        """A positive global settings timeout remains the primary configured PM SLA."""
        mock_settings.timeout = 120
        service = PMService(settings=mock_settings)

        cmd = service._build_command(loop_mode=False)
        timeout_index = cmd.index("--timeout") + 1

        assert cmd[timeout_index] == "120"

    def test_build_command_passes_absolute_director_path(
        self,
        mock_settings: MagicMock,
        tmp_path: Path,
    ) -> None:
        """PMService must not let PM CLI inherit a cwd-sensitive Director path."""
        director_script = tmp_path / "canonical-loop-director.py"
        mock_settings.pm.runs_director = True
        mock_settings.director_script_path = director_script
        service = PMService(settings=mock_settings)

        cmd = service._build_command(loop_mode=False)

        assert "--run-director" in cmd
        director_path_index = cmd.index("--director-path") + 1
        director_path = Path(cmd[director_path_index])
        assert director_path == director_script
        assert director_path.is_absolute()


class TestPMServiceNoDirectSubprocess:
    """Tests verifying no direct subprocess usage in PMService."""

    def test_no_subprocess_import(self) -> None:
        """Verify service.py does not import subprocess directly."""
        import polaris.cells.orchestration.pm_planning.service as service_module

        # Check module-level imports
        module_dict = vars(service_module)

        # subprocess should not be in module namespace
        assert "subprocess" not in module_dict
        assert "Popen" not in module_dict
        assert "run" not in module_dict

    def test_no_direct_subprocess_patterns(self, tmp_path: Path) -> None:
        """Verify _spawn_process uses broker, not direct subprocess."""
        # Read the source file to check for subprocess usage
        source_file = Path(__file__).parent.parent / "service.py"
        source_code = source_file.read_text(encoding="utf-8")

        # These patterns should NOT appear as actual code (not in comments)
        forbidden_patterns = [
            "subprocess.Popen(",
            "subprocess.run(",
            "subprocess.call(",
            "subprocess.check_call(",
            "subprocess.check_output(",
            "from subprocess import",
            "import subprocess",
        ]

        # Check each line (excluding comments)
        for line in source_code.splitlines():
            stripped = line.strip()
            # Skip comment lines
            if stripped.startswith("#"):
                continue
            # Check for forbidden patterns
            for pattern in forbidden_patterns:
                assert pattern not in line, f"Found forbidden pattern '{pattern}' in line: {line}"


class TestProcessHandleExecutionId:
    """Tests verifying ProcessHandle correctly tracks execution_id."""

    def test_process_handle_stores_execution_id(self) -> None:
        """Verify ProcessHandle can store and retrieve execution_id."""
        from polaris.cells.orchestration.pm_planning.service import ProcessHandle

        mock_process = MagicMock()
        mock_process.pid = 54321
        mock_process.poll = MagicMock(return_value=None)  # None = still running

        handle = ProcessHandle(
            process=mock_process,
            log_handle=None,
            log_path="/path/to/log",
            started_at=1234567890.0,
            mode="test-mode",
            execution_id="exec-123",
        )

        assert handle.execution_id == "exec-123"
        assert handle.pid == 54321
        # Note: is_running uses process.poll(), which we mocked to return None
        assert handle.is_running is True  # poll() returning None means process is running

    def test_process_handle_execution_id_defaults_to_none(self) -> None:
        """Verify ProcessHandle.execution_id defaults to None."""
        from polaris.cells.orchestration.pm_planning.service import ProcessHandle

        handle = ProcessHandle()
        assert handle.execution_id is None
