"""E2E smoke tests for Polaris CLI entry points.

These tests verify that critical CLI modules can be imported and their
--help output is produced successfully. They do NOT start services or
execute business logic.
"""

from __future__ import annotations

import os
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


def _build_env() -> dict[str, str]:
    """Return an environment dict that lets ``python -m polaris.…`` work.

    The subprocess must inherit the parent's ``PYTHONPATH`` (so that venv
    packages such as *fastapi* are discoverable) **and** have
    ``src/backend`` prepended so that the ``polaris`` package is found.
    """
    backend_dir = Path(__file__).resolve().parents[3]  # .../src/backend
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    separator = os.pathsep
    env["PYTHONPATH"] = f"{backend_dir}{separator}{existing}" if existing else str(backend_dir)
    return env


@pytest.mark.integration
@pytest.mark.parametrize(
    "entry",
    _CLI_ENTRIES,
    ids=[e.module for e in _CLI_ENTRIES],
)
def test_cli_help_smoke(entry: CliEntry) -> None:
    """Verify ``python -m <module> --help`` exits 0 and prints expected text."""
    env = _build_env()
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
        f"{entry.module!r}.\n"
        f"combined output:\n{combined}"
    )


def test_loop_director_script_help_bootstraps_from_non_repo_cwd(tmp_path: Path) -> None:
    """Direct script execution must bootstrap backend imports before polaris imports."""
    backend_dir = Path(__file__).resolve().parents[3]
    script_path = backend_dir / "polaris" / "delivery" / "cli" / "loop-director.py"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8",
        timeout=30,
    )

    assert result.returncode == 0, (
        f"loop-director.py --help exited with {result.returncode}.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Polaris Director" in f"{result.stdout}\n{result.stderr}"
