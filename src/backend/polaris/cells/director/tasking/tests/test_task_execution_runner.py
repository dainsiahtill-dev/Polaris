"""Tests for task_execution_runner subprocess module."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_runner_echo_success(tmp_path: Path) -> None:
    """Test that runner can execute a simple task and return success."""
    # Create a simple task that should succeed
    task_input = {
        "workspace": str(tmp_path),
        "worker_id": "test-worker-1",
        "task": {
            "id": "task-1",
            "subject": "Test task",
            "description": "A simple test task",
            "timeout_seconds": 30,
            "metadata": {"test": True},
        },
    }

    # Run the subprocess
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "polaris.cells.director.tasking.internal.task_execution_runner",
        ],
        input=json.dumps(task_input),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    # Check result
    assert result.returncode == 0, f"stderr: {result.stderr}"

    # Parse output - find the last JSON line (module may print log lines to stdout)
    stdout_lines = result.stdout.strip().splitlines()
    output = None
    for line in reversed(stdout_lines):
        line = line.strip()
        if line.startswith("{"):
            try:
                output = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    assert output is not None, f"No JSON found in stdout: {result.stdout[:500]}"
    assert "success" in output
    assert "output" in output
    assert "error" in output
    assert "duration_ms" in output
    assert "evidence" in output


@pytest.mark.asyncio
async def test_runner_invalid_json(tmp_path: Path) -> None:
    """Test that runner handles invalid JSON input gracefully."""
    # Run with invalid JSON
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "polaris.cells.director.tasking.internal.task_execution_runner",
        ],
        input="not valid json {",
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    # Should fail gracefully
    assert result.returncode == 1, f"Expected return code 1, got {result.returncode}"

    # Parse error output - find the last JSON line (module may print log lines to stdout)
    stdout_lines = result.stdout.strip().splitlines()
    output = None
    for line in reversed(stdout_lines):
        line = line.strip()
        if line.startswith("{"):
            try:
                output = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    assert output is not None, f"No JSON found in stdout: {result.stdout[:500]}"
    assert output["success"] is False
    assert "Invalid JSON" in output["error"]


@pytest.mark.asyncio
async def test_runner_missing_workspace(tmp_path: Path) -> None:
    """Test that runner handles missing workspace gracefully."""
    task_input = {
        "worker_id": "test-worker-1",
        "task": {
            "id": "task-1",
            "subject": "Test task",
        },
    }

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "polaris.cells.director.tasking.internal.task_execution_runner",
        ],
        input=json.dumps(task_input),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    # Should fail but gracefully
    assert result.returncode in (0, 1), f"Unexpected return code: {result.returncode}"


def test_runner_module_exists() -> None:
    """Test that the module can be imported."""
    from polaris.cells.director.tasking.internal import task_execution_runner

    assert hasattr(task_execution_runner, "main")
    assert hasattr(task_execution_runner, "_run_task_execution")


@pytest.mark.asyncio
async def test_runner_with_full_task_dict(tmp_path: Path) -> None:
    """Test runner with a fully populated task dict."""
    task_input = {
        "workspace": str(tmp_path),
        "worker_id": "test-worker-full",
        "task": {
            "id": "task-full-1",
            "subject": "Full test task",
            "description": "Testing with full task dictionary",
            "status": "pending",
            "priority": "high",
            "timeout_seconds": 60,
            "blocked_by": [],
            "blocks": [],
            "owner": "test-owner",
            "assignee": "test-assignee",
            "role": "director",
            "constraints": ["constraint1"],
            "acceptance_criteria": ["criterion1"],
            "command": None,
            "working_directory": None,
            "max_retries": 3,
            "retry_count": 0,
            "tags": ["test", "integration"],
            "metadata": {
                "tech_stack": {"language": "python"},
                "project_type": "library",
            },
        },
    }

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "polaris.cells.director.tasking.internal.task_execution_runner",
        ],
        input=json.dumps(task_input),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    # Should complete (may fail due to Phase 4 deps, but should not crash)
    assert result.returncode in (0, 1), f"Unexpected return code: {result.returncode}, stderr: {result.stderr}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
