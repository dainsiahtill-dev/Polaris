"""Tests for director tooling execution via execution_broker.

This suite verifies that the director executors correctly use the execution_broker
for process execution instead of subprocess.run().
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Backend root is two levels up from tests/
_BACKEND_ROOT = Path(__file__).resolve().parents[1]


class TestExecutionBrokerIntegration:
    """Verify director executors use execution_broker correctly."""

    def test_executor_core_uses_execution_facade(self) -> None:
        """Verify executor_core.py imports and uses ExecutionFacade."""
        from polaris.cells.director.execution.internal.tools.executor_core import (
            get_shared_execution_facade,
            ProcessSpec,
        )

        # Verify imports are available
        assert callable(get_shared_execution_facade)
        assert ProcessSpec is not None

    def test_executor_uses_execution_facade(self) -> None:
        """Verify executor.py imports and uses ExecutionFacade."""
        from polaris.cells.director.execution.internal.tools.executor import (
            get_shared_execution_facade,
            ProcessSpec,
        )

        # Verify imports are available
        assert callable(get_shared_execution_facade)
        assert ProcessSpec is not None

    def test_no_subprocess_imports_in_executor_core(self) -> None:
        """Verify subprocess is not imported in executor_core.py."""
        source_file = _BACKEND_ROOT / "polaris" / "cells" / "director" / "execution" / "internal" / "tools" / "executor_core.py"
        content = source_file.read_text(encoding="utf-8")

        # Should not have subprocess import
        assert "import subprocess" not in content
        assert "from subprocess import" not in content

    def test_no_subprocess_imports_in_executor(self) -> None:
        """Verify subprocess is not imported in executor.py."""
        source_file = _BACKEND_ROOT / "polaris" / "cells" / "director" / "execution" / "internal" / "tools" / "executor.py"
        content = source_file.read_text(encoding="utf-8")

        # Should not have subprocess import
        assert "import subprocess" not in content
        assert "from subprocess import" not in content


class TestExecutionBrokerMetadata:
    """Verify execution_broker metadata is correctly set."""

    def test_metadata_includes_cell_name(self) -> None:
        """Verify ProcessSpec includes cell='director' in metadata."""
        source_file = _BACKEND_ROOT / "polaris" / "cells" / "director" / "execution" / "internal" / "tools" / "executor_core.py"
        content = source_file.read_text(encoding="utf-8")

        # Should include cell metadata
        assert '"cell": "director"' in content

    def test_metadata_includes_tool_name(self) -> None:
        """Verify ProcessSpec includes tool_name in metadata."""
        source_file = _BACKEND_ROOT / "polaris" / "cells" / "director" / "execution" / "internal" / "tools" / "executor_core.py"
        content = source_file.read_text(encoding="utf-8")

        # Should include tool_name metadata
        assert '"tool_name": tool' in content or "'tool_name': tool" in content

    def test_metadata_includes_workspace(self) -> None:
        """Verify ProcessSpec includes workspace in metadata."""
        source_file = _BACKEND_ROOT / "polaris" / "cells" / "director" / "execution" / "internal" / "tools" / "executor_core.py"
        content = source_file.read_text(encoding="utf-8")

        # Should include workspace metadata
        assert '"workspace":' in content


class TestUtf8Encoding:
    """Verify UTF-8 encoding configuration is preserved."""

    def test_utf8_env_used_in_executor_core(self) -> None:
        """Verify build_utf8_env is used in executor_core.py."""
        source_file = _BACKEND_ROOT / "polaris" / "cells" / "director" / "execution" / "internal" / "tools" / "executor_core.py"
        content = source_file.read_text(encoding="utf-8")

        # Should use build_utf8_env
        assert "build_utf8_env()" in content

    def test_utf8_env_used_in_executor(self) -> None:
        """Verify build_utf8_env is used in executor.py."""
        source_file = _BACKEND_ROOT / "polaris" / "cells" / "director" / "execution" / "internal" / "tools" / "executor.py"
        content = source_file.read_text(encoding="utf-8")

        # Should use build_utf8_env
        assert "build_utf8_env()" in content


class TestTimeoutHandling:
    """Verify timeout handling is correctly migrated."""

    def test_asyncio_timeout_used_in_executor_core(self) -> None:
        """Verify asyncio.TimeoutError is caught instead of subprocess.TimeoutExpired."""
        source_file = _BACKEND_ROOT / "polaris" / "cells" / "director" / "execution" / "internal" / "tools" / "executor_core.py"
        content = source_file.read_text(encoding="utf-8")

        # Should catch asyncio.TimeoutError
        assert "asyncio.TimeoutError" in content
        # Should not catch subprocess.TimeoutExpired
        assert "subprocess.TimeoutExpired" not in content

    def test_asyncio_timeout_used_in_executor(self) -> None:
        """Verify asyncio.TimeoutError is caught instead of subprocess.TimeoutExpired."""
        source_file = _BACKEND_ROOT / "polaris" / "cells" / "director" / "execution" / "internal" / "tools" / "executor.py"
        content = source_file.read_text(encoding="utf-8")

        # Should catch asyncio.TimeoutError
        assert "asyncio.TimeoutError" in content
        # Should not catch subprocess.TimeoutExpired
        assert "subprocess.TimeoutExpired" not in content


@pytest.mark.asyncio
async def test_execution_facade_run_process_basic() -> None:
    """Integration test: verify ExecutionFacade.run_process works for director tooling."""
    from polaris.kernelone.runtime.execution_facade import (
        ExecutionFacade,
        ProcessSpec,
        get_shared_execution_facade,
    )
    from polaris.kernelone.runtime.execution_runtime import ExecutionStatus

    # Get the shared facade
    facade = get_shared_execution_facade()

    # Create a simple process spec
    spec = ProcessSpec(
        name="test-director-tool",
        args=["python", "-c", "print('hello from execution_broker')"],
        cwd=None,
        timeout_seconds=5.0,
        metadata={
            "cell": "director",
            "tool_name": "test_tool",
            "workspace": ".",
        },
    )

    # Run the process
    result = await facade.run_process(spec, collect_output=True)

    # Verify results
    assert result.status == ExecutionStatus.SUCCESS
    assert any("hello from execution_broker" in line for line in result.stdout_lines)
    assert result.snapshot.ok is True


@pytest.mark.asyncio
async def test_execution_facade_run_process_timeout() -> None:
    """Integration test: verify ExecutionFacade.run_process handles timeout correctly."""
    from polaris.kernelone.runtime.execution_facade import (
        ProcessSpec,
        get_shared_execution_facade,
    )
    from polaris.kernelone.runtime.execution_runtime import ExecutionStatus

    # Get the shared facade
    facade = get_shared_execution_facade()

    # Create a process that will hang
    spec = ProcessSpec(
        name="test-timeout",
        args=["python", "-c", "import time; time.sleep(10)"],
        cwd=None,
        timeout_seconds=0.5,  # Very short timeout
        metadata={
            "cell": "director",
            "tool_name": "test_timeout_tool",
        },
    )

    # Run the process with a short wait timeout
    result = await facade.run_process(spec, collect_output=True, wait_timeout=1.0)

    # Verify timeout is detected
    assert result.status == ExecutionStatus.TIMED_OUT
    assert result.snapshot.ok is False


@pytest.mark.asyncio
async def test_execution_facade_run_process_stderr_capture() -> None:
    """Integration test: verify stderr is captured correctly."""
    from polaris.kernelone.runtime.execution_facade import (
        ProcessSpec,
        get_shared_execution_facade,
    )

    facade = get_shared_execution_facade()

    spec = ProcessSpec(
        name="test-stderr",
        args=["python", "-c", "import sys; print('error output', file=sys.stderr); print('normal output')"],
        cwd=None,
        timeout_seconds=5.0,
        metadata={"cell": "director"},
    )

    result = await facade.run_process(spec, collect_output=True)

    # Verify stderr is captured
    assert any("error output" in line for line in result.stderr_lines)
    assert any("normal output" in line for line in result.stdout_lines)
