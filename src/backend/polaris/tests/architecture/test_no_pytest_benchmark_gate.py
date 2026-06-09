"""Governance gate: benchmark / matrix scoring must run via the agentic-eval CLI.

This is a structural fitness check (NOT a benchmark): it runs
``docs/governance/ci/scripts/check_no_pytest_benchmark.py`` and fails if any
``test_*.py`` executes a benchmark/matrix suite. Policy: AGENTS.md §10.5 and
``docs/blueprints/SCOUT_BENCHMARK_MATRIX_BLUEPRINT_20260609.md``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
GATE_SCRIPT = BACKEND_ROOT / "docs" / "governance" / "ci" / "scripts" / "check_no_pytest_benchmark.py"


def _utf8_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def test_no_pytest_benchmark_execution() -> None:
    assert GATE_SCRIPT.is_file(), f"missing gate script: {GATE_SCRIPT}"
    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "--root", str(BACKEND_ROOT / "polaris")],
        capture_output=True,
        text=True,
        env=_utf8_env(),
    )
    assert result.returncode == 0, (
        "Benchmark/matrix scoring must run via the agentic-eval CLI, not pytest.\n" + result.stdout + result.stderr
    )
