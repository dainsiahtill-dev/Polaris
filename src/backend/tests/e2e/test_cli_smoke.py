"""E2E smoke tests for Polaris CLI entry points.

These tests verify that critical CLI modules can be imported and their
--help output is produced successfully. They do NOT start services or
execute business logic.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest


class CliEntry(NamedTuple):
    """Specification for a CLI smoke test case."""

    module: str
    args: list[str]
    expected_in_output: str


# ---------------------------------------------------------------------------
# CLI entry-point catalog
# ---------------------------------------------------------------------------
_CLI_ENTRIES: list[CliEntry] = [
    CliEntry(
        module="polaris.delivery.server",
        args=["--help"],
        expected_in_output="Polaris Backend Server",
    ),
    CliEntry(
        module="polaris.delivery.cli.pm.cli",
        args=["--help"],
        expected_in_output="Polaris PM Loop",
    ),
    CliEntry(
        module="polaris.delivery.cli.director.cli_thin",
        args=["--help"],
        expected_in_output="Polaris Director - Thin CLI Adapter",
    ),
    CliEntry(
        module="polaris.cells.architect.design.internal.architect_cli",
        args=["--help"],
        expected_in_output="Architect Role Agent",
    ),
    CliEntry(
        module="polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli",
        args=["--help"],
        expected_in_output="Chief_Engineer Role Agent",
    ),
    CliEntry(
        module="polaris.delivery.cli",
        args=["console", "--help"],
        expected_in_output="Polaris terminal console",
    ),
]


def _resolve_python_path() -> str:
    """Return the PYTHONPATH needed so that ``polaris`` is importable.

    When pytest runs from the repo root, ``src/backend`` is already on
    ``sys.path`` (via ``conftest.py``).  We replicate that for the
    subprocess so that ``python -m polaris.…`` works identically.
    """
    backend_dir = Path(__file__).resolve().parents[2]  # …/src/backend
    return str(backend_dir)


@pytest.mark.integration
@pytest.mark.parametrize(
    "entry",
    _CLI_ENTRIES,
    ids=[e.module for e in _CLI_ENTRIES],
)
def test_cli_help_smoke(entry: CliEntry) -> None:
    """Verify ``python -m <module> --help`` exits 0 and prints expected text."""
    env = {"PYTHONPATH": _resolve_python_path()}
    cmd = [sys.executable, "-m", entry.module, *entry.args]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8",
    )

    assert result.returncode == 0, (
        f"CLI {entry.module!r} exited with {result.returncode}.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    combined = f"{result.stdout}\n{result.stderr}"
    assert entry.expected_in_output in combined, (
        f"Expected text {entry.expected_in_output!r} not found in output of "
        f"{entry.module!r}.\ncombined output:\n{combined}"
    )
