#!/usr/bin/env python3
"""Cells-KernelOne Integration Gate

Verifies P0 and P1 integration checkpoints from:
- CELLS_KERNELONE_INTEGRATION_BLUEPRINT_20260403.md
- CELLS_KERNELONE_INTEGRATION_PLAN_20260403.md

Exit codes:
    0 - All checks passed
    1 - One or more checks failed
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    title: str
    severity: str  # P0, P1
    passed: bool
    message: str
    duration_ms: float


@dataclass(frozen=True)
class GateResult:
    ok: bool
    total_checks: int
    passed_checks: int
    failed_checks: int
    p0_checks: int
    p0_passed: int
    p1_checks: int
    p1_passed: int
    checks: list[CheckResult]
    duration_seconds: float


def _measure_ms(fn, *args, **kwargs):
    start = time.monotonic()
    result = fn(*args, **kwargs)
    return result, (time.monotonic() - start) * 1000


# ---------------------------------------------------------------------------
# P0 Critical Checks
# ---------------------------------------------------------------------------


def check_cr1_director_tools_deleted() -> tuple[bool, str]:
    """CR-1: director/execution/internal/tools/ must be deleted or empty."""
    path = BACKEND_ROOT / "polaris/cells/director/execution/internal/tools"
    if not path.exists():
        return True, "directory does not exist (already deleted)"

    # Check if only __pycache__ remains
    py_files = list(path.glob("*.py"))
    if not py_files:
        return True, "only __pycache__ remains"

    file_names = [f.name for f in py_files]
    return False, f"directory still contains .py files: {file_names}"


def check_cr2_provider_runtime_singleton() -> tuple[bool, str]:
    """CR-2: provider_runtime must delegate to infrastructure singleton."""
    path = BACKEND_ROOT / "polaris/cells/llm/provider_runtime/internal/providers.py"
    if not path.exists():
        return True, "providers.py does not exist (fully removed)"

    content = path.read_text(encoding="utf-8")

    # Must NOT contain "return ProviderManager()" which creates a new instance
    if "return ProviderManager()" in content:
        return False, "providers.py still returns new ProviderManager() instance"

    # Must delegate to infrastructure (not kernelone directly per blueprint)
    if "from polaris.infrastructure.llm.providers" not in content:
        return False, "providers.py does not import from polaris.infrastructure.llm.providers"

    return True, "correctly delegates to infrastructure.llm.providers"


# ---------------------------------------------------------------------------
# P1 High Priority Checks
# ---------------------------------------------------------------------------


def check_h2_dangerous_patterns_exists() -> tuple[bool, str]:
    """H-2: kernelone/security/dangerous_patterns.py must exist."""
    path = BACKEND_ROOT / "polaris/kernelone/security/dangerous_patterns.py"
    if not path.exists():
        return False, "polaris/kernelone/security/dangerous_patterns.py does not exist"
    return True, "exists"


def check_h3_storage_paths_exists() -> tuple[bool, str]:
    """H-3: kernelone/storage/paths.py must exist."""
    path = BACKEND_ROOT / "polaris/kernelone/storage/paths.py"
    if not path.exists():
        return False, "polaris/kernelone/storage/paths.py does not exist"
    return True, "exists"


def check_h4_tool_compaction_exists() -> tuple[bool, str]:
    """H-4: kernelone/tool/compaction.py must exist."""
    path = BACKEND_ROOT / "polaris/kernelone/tool/compaction.py"
    if not path.exists():
        return False, "polaris/kernelone/tool/compaction.py does not exist"
    return True, "exists"


def check_h5_fact_events_exists() -> tuple[bool, str]:
    """H-5: kernelone/events/fact_events.py must exist."""
    path = BACKEND_ROOT / "polaris/kernelone/events/fact_events.py"
    if not path.exists():
        return False, "polaris/kernelone/events/fact_events.py does not exist"
    return True, "exists"


def check_h6_session_events_exists() -> tuple[bool, str]:
    """H-6: kernelone/events/session_events.py must exist."""
    path = BACKEND_ROOT / "polaris/kernelone/events/session_events.py"
    if not path.exists():
        return False, "polaris/kernelone/events/session_events.py does not exist"
    return True, "exists"


# ---------------------------------------------------------------------------
# Gate execution
# ---------------------------------------------------------------------------


def _run_checks() -> GateResult:
    checks: list[CheckResult] = []

    # Define all checks with their metadata
    check_definitions: list[tuple[str, str, str, callable]] = [
        # CR-1 and CR-2 are P0
        ("CR-1", "Director tools deleted", "P0", check_cr1_director_tools_deleted),
        ("CR-2", "ProviderManager singleton", "P0", check_cr2_provider_runtime_singleton),
        # H-2 through H-6 are P1
        ("H-2", "dangerous_patterns exists", "P1", check_h2_dangerous_patterns_exists),
        ("H-3", "storage/paths.py exists", "P1", check_h3_storage_paths_exists),
        ("H-4", "tool/compaction.py exists", "P1", check_h4_tool_compaction_exists),
        ("H-5", "events/fact_events.py exists", "P1", check_h5_fact_events_exists),
        ("H-6", "events/session_events.py exists", "P1", check_h6_session_events_exists),
    ]

    start = time.monotonic()

    for check_id, title, severity, fn in check_definitions:
        (passed, message), duration_ms = _measure_ms(fn)
        checks.append(
            CheckResult(
                check_id=check_id,
                title=title,
                severity=severity,
                passed=passed,
                message=message,
                duration_ms=round(duration_ms, 2),
            )
        )

    duration_seconds = time.monotonic() - start

    # Aggregate results
    p0_checks = [c for c in checks if c.severity == "P0"]
    p1_checks = [c for c in checks if c.severity == "P1"]
    passed_checks = [c for c in checks if c.passed]

    return GateResult(
        ok=all(c.passed for c in checks),
        total_checks=len(checks),
        passed_checks=len(passed_checks),
        failed_checks=len(checks) - len(passed_checks),
        p0_checks=len(p0_checks),
        p0_passed=len([c for c in p0_checks if c.passed]),
        p1_checks=len(p1_checks),
        p1_passed=len([c for c in p1_checks if c.passed]),
        checks=checks,
        duration_seconds=round(duration_seconds, 3),
    )


def _format_result(result: GateResult) -> str:
    lines = [
        "=" * 60,
        "Cells-KernelOne Integration Gate",
        "=" * 60,
        "",
        f"Result: {'PASS' if result.ok else 'FAIL'}",
        f"Checks: {result.passed_checks}/{result.total_checks} passed",
        f"  P0: {result.p0_passed}/{result.p0_checks} passed",
        f"  P1: {result.p1_passed}/{result.p1_checks} passed",
        "",
        "-" * 60,
        "Details:",
        "",
    ]

    for check in result.checks:
        status = "PASS" if check.passed else "FAIL"
        lines.append(f"  [{status}] {check.check_id} {check.title}")
        if not check.passed:
            lines.append(f"        -> {check.message}")

    lines.extend(
        [
            "",
            "-" * 60,
            f"Duration: {result.duration_seconds}s",
            "=" * 60,
        ]
    )

    return "\n".join(lines)


def _write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Cells-KernelOne integration gate.",
    )
    parser.add_argument(
        "--report",
        default="workspace/meta/governance_reports/cells_kernelone_gate_report.json",
        help="JSON report output path (relative to backend root).",
    )
    parser.add_argument(
        "--print-report",
        action="store_true",
        help="Print JSON report to stdout.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON only.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = _run_checks()

    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print(_format_result(result))

    report_path = Path(str(args.report)).expanduser()
    if not report_path.is_absolute():
        report_path = (BACKEND_ROOT / report_path).resolve()
    _write_report(report_path, asdict(result))

    if not result.ok:
        failed = [c for c in result.checks if not c.passed]
        print(
            f"\nFailed checks: {[c.check_id for c in failed]}",
            file=sys.stderr,
        )

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
