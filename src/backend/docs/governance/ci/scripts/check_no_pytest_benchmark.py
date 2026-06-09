#!/usr/bin/env python3
"""Governance gate: benchmark / matrix scoring MUST run via the agentic-eval CLI.

Policy (AGENTS.md §10 ; docs/blueprints/SCOUT_BENCHMARK_MATRIX_BLUEPRINT_20260609.md):

    Any matrix test / benchmark MUST be expressed as agentic-eval CASE JSON and run
    through the agentic-eval CLI (``--suite ...``). Encoding a benchmark / matrix
    *scoring run* as a pytest test is forbidden.

This gate FAILS if any ``test_*.py`` under ``src/backend/polaris`` EXECUTES a
benchmark / matrix suite — i.e. calls a ``run_*_suite(...)`` entrypoint or
instantiates ``UnifiedBenchmarkRunner`` and calls ``.run_suite(...)``.

Importing benchmark MODELS / VALIDATORS / helpers for ordinary component unit
tests is allowed; only *running a scoring suite/matrix from pytest* is banned.

Run:
    python docs/governance/ci/scripts/check_no_pytest_benchmark.py [--root <polaris_dir>]
Exit code 0 = clean, 1 = violations found.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Patterns that indicate a benchmark / matrix SCORING RUN (not a mere import).
_BANNED_PATTERNS: tuple[str, ...] = (
    r"\brun_agentic_benchmark_suite\s*\(",
    r"\brun_tool_calling_matrix_suite\s*\(",
    r"\brun_speculation_matrix_suite\s*\(",
    r"\brun_context_projection_matrix_suite\s*\(",
    r"\brun_projection_adaptive_matrix_suite\s*\(",
    r"\brun_strategy_benchmark_suite\s*\(",
    r"\brun_context_benchmark_suite\s*\(",
    r"\brun_scout_matrix_suite\s*\(",
    r"\.run_suite\s*\(",
)
_BANNED_RE = re.compile("|".join(_BANNED_PATTERNS))

# Directories whose tests are governance/architecture meta-checks (they ENFORCE
# policy, they are not benchmarks) and are therefore exempt from the scan.
_EXEMPT_PARTS: frozenset[str] = frozenset({"architecture", "evaluation_security"})


def find_violations(polaris_root: Path) -> list[tuple[str, int, str]]:
    """Return (path, line_no, line_text) for every banned suite-execution in pytest."""
    violations: list[tuple[str, int, str]] = []
    for path in sorted(polaris_root.rglob("test_*.py")):
        if _EXEMPT_PARTS & set(path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if _BANNED_RE.search(line):
                violations.append((str(path), line_no, line.strip()))
    return violations


def _default_polaris_root() -> Path:
    # docs/governance/ci/scripts -> parents[4] == src/backend
    return Path(__file__).resolve().parents[4] / "polaris"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ban pytest-based benchmark/matrix execution.")
    parser.add_argument("--root", default=None, help="Path to the polaris package root.")
    args = parser.parse_args(argv)

    polaris_root = Path(args.root).resolve() if args.root else _default_polaris_root()
    if not polaris_root.is_dir():
        print(f"[check_no_pytest_benchmark] polaris root not found: {polaris_root}", file=sys.stderr)
        return 2

    violations = find_violations(polaris_root)
    if not violations:
        print("[check_no_pytest_benchmark] OK — no pytest-based benchmark/matrix execution found.")
        return 0

    print("[check_no_pytest_benchmark] FAIL — benchmark/matrix scoring must run via the agentic-eval CLI, not pytest:")
    for path, line_no, line in violations:
        print(f"  {path}:{line_no}: {line}")
    print(
        "\nMove these into agentic-eval CASE JSON under "
        "polaris/cells/llm/evaluation/fixtures/agentic_benchmark/cases/ and run via "
        "`python -m polaris.delivery.cli.agentic_eval --suite <suite>`."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
