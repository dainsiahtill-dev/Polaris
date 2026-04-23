#!/usr/bin/env python3
"""Generate an engineering metrics dashboard for Polaris.

Outputs a Markdown file with:
- Test collection count
- Coverage percentage
- Ruff error count
- MyPy error count
- Fitness Rules status distribution

Usage:
    python scripts/engineering_dashboard.py
    python scripts/engineering_dashboard.py --output docs/engineering_dashboard.md
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import yaml


def run_cmd(cmd: list[str], cwd: Path | None = None, timeout: int = 300) -> tuple[int, str, str]:
    """Run a shell command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
            encoding="utf-8",
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return 127, "", f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "Command timed out"


def get_test_count(repo_root: Path) -> int | str:
    """Count collected tests via pytest --collect-only."""
    _rc, stdout, stderr = run_cmd(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=repo_root,
        timeout=180,
    )
    # pytest may return non-zero when collection errors exist, but still reports count
    combined = stdout + "\n" + stderr
    for line in combined.splitlines():
        line_lower = line.lower()
        if "collected" in line_lower:
            # Parse "14907 tests collected" or "14907 collected"
            parts = line.split()
            for i, part in enumerate(parts):
                if part.lower() == "collected" and i >= 1:
                    try:
                        return int(parts[i - 1])
                    except ValueError:
                        continue
                if part.lower() == "collected" and i + 1 < len(parts):
                    try:
                        return int(parts[i + 1])
                    except ValueError:
                        continue
            # Fallback: first token might be the number
            try:
                return int(parts[0])
            except (ValueError, IndexError):
                pass
    return "N/A"


def get_coverage(repo_root: Path) -> float | str:
    """Get coverage percentage from coverage.xml or pytest-cov output."""
    coverage_xml = repo_root / "coverage.xml"
    if coverage_xml.exists():
        try:
            tree = ET.parse(coverage_xml)
            root = tree.getroot()
            line_rate = root.get("line-rate")
            if line_rate is not None:
                return float(line_rate) * 100
        except ET.ParseError:
            pass

    # Fallback: try running pytest with cov
    _rc, stdout, stderr = run_cmd(
        [sys.executable, "-m", "pytest", "--cov=src/backend/polaris", "--cov-report=term-missing", "-q"],
        cwd=repo_root,
        timeout=300,
    )
    combined = stdout + stderr
    for line in reversed(combined.splitlines()):
        if "TOTAL" in line:
            parts = line.split()
            for part in parts:
                clean = part.replace("%", "").strip()
                try:
                    val = float(clean)
                    if 0 <= val <= 100:
                        return val
                except ValueError:
                    continue
    return "N/A"


def get_ruff_errors(repo_root: Path) -> int | str:
    """Count ruff errors."""
    rc, stdout, stderr = run_cmd(
        [sys.executable, "-m", "ruff", "check", "src/backend/polaris", "tests", "scripts"],
        cwd=repo_root,
        timeout=120,
    )
    combined = stdout + stderr
    count = 0
    for line in combined.splitlines():
        if line.strip() and not line.startswith("All checks passed"):
            # ruff outputs one line per violation
            count += 1
    if rc == 0:
        return 0
    return count if count > 0 else "N/A"


def get_mypy_errors(repo_root: Path) -> int | str:
    """Count mypy errors."""
    rc, stdout, stderr = run_cmd(
        [sys.executable, "-m", "mypy", "src/backend/polaris"],
        cwd=repo_root,
        timeout=300,
    )
    combined = stdout + stderr
    count = 0
    for line in combined.splitlines():
        if ": error:" in line:
            count += 1
    if rc == 0:
        return 0
    return count if count > 0 else "N/A"


def get_fitness_rules_distribution(repo_root: Path) -> dict[str, int]:
    """Count fitness rules by status."""
    fitness_path = repo_root / "src" / "backend" / "docs" / "governance" / "ci" / "fitness-rules.yaml"
    if not fitness_path.exists():
        return {}

    try:
        with fitness_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return {}

    distribution: dict[str, int] = {}
    for rule in data.get("rules", []):
        status = rule.get("current_status", "unknown")
        distribution[status] = distribution.get(status, 0) + 1
    return distribution


def generate_dashboard(
    test_count: int | str,
    coverage: float | str,
    ruff_errors: int | str,
    mypy_errors: int | str,
    fitness_distribution: dict[str, int],
) -> str:
    """Generate the Markdown dashboard content."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    coverage_str = f"{coverage:.1f}%" if isinstance(coverage, float) else str(coverage)
    coverage_badge = ""
    if isinstance(coverage, float):
        if coverage >= 80 or coverage >= 50:
            coverage_badge = "![Coverage](docs/assets/badges/coverage.svg)"
        else:
            coverage_badge = "![Coverage](docs/assets/badges/coverage.svg)"

    lines = [
        "# Engineering Metrics Dashboard",
        "",
        f"> Auto-generated on {now}",
        "",
        "## Overview",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Tests Collected | {test_count} |",
        f"| Coverage | {coverage_str} {coverage_badge} |",
        f"| Ruff Errors | {ruff_errors} |",
        f"| MyPy Errors | {mypy_errors} |",
        "",
        "## Fitness Rules Status Distribution",
        "",
        "| Status | Count |",
        "|--------|-------|",
    ]

    for status, count in sorted(fitness_distribution.items()):
        lines.append(f"| {status} | {count} |")

    total_rules = sum(fitness_distribution.values())
    lines.append(f"| **Total** | **{total_rules}** |")
    lines.append("")

    # Coverage goal tracking
    lines.append("## Coverage Goal Tracking")
    lines.append("")
    lines.append("| Threshold | Status |")
    lines.append("|-----------|--------|")
    if isinstance(coverage, float):
        lines.append(f"| >= 50% | {'Pass' if coverage >= 50 else 'Fail'} |")
        lines.append(f"| >= 80% | {'Pass' if coverage >= 80 else 'Fail'} |")
    else:
        lines.append("| >= 50% | Unknown |")
        lines.append("| >= 80% | Unknown |")
    lines.append("")

    lines.append("## CI Jobs")
    lines.append("")
    lines.append("| Job | Command |")
    lines.append("|-----|---------|")
    lines.append("| Test | `pytest --cov=src/backend/polaris --cov-report=xml` |")
    lines.append("| Lint | `ruff check src/backend/polaris tests scripts` |")
    lines.append("| Type Check | `mypy src/backend/polaris` |")
    lines.append("")

    lines.append("---")
    lines.append("*Generated by `scripts/engineering_dashboard.py`*")
    lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate engineering metrics dashboard")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/engineering_dashboard.md"),
        help="Output Markdown file path",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Skip slow commands (pytest, mypy) and use cached/placeholder values",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent
    print("Collecting engineering metrics...")

    test_count: int | str
    coverage: float | str
    ruff_errors: int | str
    mypy_errors: int | str

    if args.quick:
        test_count = "N/A (quick mode)"
        coverage = "N/A (quick mode)"
        ruff_errors = "N/A (quick mode)"
        mypy_errors = "N/A (quick mode)"
    else:
        print("  - Counting tests...")
        test_count = get_test_count(repo_root)
        print(f"     -> {test_count} tests")

        print("  - Reading coverage...")
        coverage = get_coverage(repo_root)
        print(f"     -> {coverage}")

        print("  - Running ruff...")
        ruff_errors = get_ruff_errors(repo_root)
        print(f"     -> {ruff_errors} errors")

        print("  - Running mypy...")
        mypy_errors = get_mypy_errors(repo_root)
        print(f"     -> {mypy_errors} errors")

    print("  - Reading fitness rules...")
    fitness_distribution = get_fitness_rules_distribution(repo_root)
    print(f"     -> {fitness_distribution}")

    markdown = generate_dashboard(test_count, coverage, ruff_errors, mypy_errors, fitness_distribution)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown, encoding="utf-8")
    print(f"\nDashboard written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
