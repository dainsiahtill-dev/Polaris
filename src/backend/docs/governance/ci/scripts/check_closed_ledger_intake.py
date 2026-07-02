"""Check closed governance ledger intake policy.

This gate prevents closed convergence ledgers from silently becoming active
backlogs again. New findings must be opened as new evidence-backed intake
items, not by reopening closed rows.

Rule ID: closed_governance_ledgers_intake_only
Severity: high

Usage:
    python docs/governance/ci/scripts/check_closed_ledger_intake.py
    python docs/governance/ci/scripts/check_closed_ledger_intake.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
BACKEND_ROOT = SCRIPT_DIR.parent.parent.parent.parent
RULE_ID = "closed_governance_ledgers_intake_only"

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"


@dataclass
class FitnessCheckResult:
    """Result of a governance fitness check."""

    rule_id: str
    passed: bool
    evidence: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timestamp: str = ""
    duration_ms: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def format(self) -> str:
        """Format the result for console output."""
        status = f"{GREEN}PASS{RESET}" if self.passed else f"{RED}FAIL{RESET}"
        lines = [f"[{self.rule_id}] {status}", f"  Duration: {self.duration_ms:.2f}ms"]
        if self.evidence:
            lines.append("  Evidence:")
            lines.extend(f"    - {item}" for item in self.evidence)
        if self.violations:
            lines.append("  Violations:")
            lines.extend(f"    - {item}" for item in self.violations)
        if self.warnings:
            lines.append("  Warnings:")
            lines.extend(f"    - {item}" for item in self.warnings)
        return "\n".join(lines)


@dataclass(frozen=True)
class ClosedLedgerExpectation:
    """Expected closure markers for a governance ledger."""

    filename: str
    required_sections: tuple[str, ...]
    required_phrases: tuple[str, ...]


CLOSED_LEDGER_EXPECTATIONS = (
    ClosedLedgerExpectation(
        filename="POLARIS_EXECUTION_CONTRACT_GAP_LEDGER.md",
        required_sections=("## Current Closure State", "## Reopen and Intake Rules"),
        required_phrases=(
            "Closed rows must not be reopened",
            "New execution facts must converge on the existing chain",
        ),
    ),
    ClosedLedgerExpectation(
        filename="POLARIS_LEGACY_SHIM_CONVERGENCE_LEDGER_20260630.md",
        required_sections=("## Current Count", "## Operating Rule"),
        required_phrases=(
            "New gaps must be opened as a new LS item",
            "New legacy/shim findings must be added as a new",
        ),
    ),
    ClosedLedgerExpectation(
        filename="POLARIS_KFS_DIRECT_WRITE_CONVERGENCE_LEDGER_20260701.md",
        required_sections=("## Current Count", "## Closure and Intake Rules"),
        required_phrases=(
            "Do not reopen a closed KFS row",
            "Open a new `KFS-*` item",
        ),
    ),
    ClosedLedgerExpectation(
        filename="POLARIS_BENCH_UNBLOCKING_RUNTIME_LEDGER_20260630.md",
        required_sections=("## Current Count", "## Closure and Intake Rules"),
        required_phrases=(
            "Do not use this ledger as a catch-all",
            "A new `RB-*` item may be opened only",
        ),
    ),
    ClosedLedgerExpectation(
        filename="POLARIS_CATALOG_BOUNDARY_DEBT_LEDGER_20260701.md",
        required_sections=("## Current Closure State",),
        required_phrases=(
            "This ledger is closed for the current catalog-boundary convergence pass",
            "New `CB-*` or `CD-*` items must include",
        ),
    ),
)


def _collapse_whitespace(text: str) -> str:
    """Collapse Markdown line wrapping so phrase checks stay semantic."""
    return " ".join(text.split())


def _status_line(text: str) -> str | None:
    """Return the first status line from a ledger document."""
    for line in text.splitlines():
        if line.startswith("Status:"):
            return line.strip()
    return None


def _open_count_violations(filename: str, text: str) -> list[str]:
    """Return violations for non-zero open-count rows in a closed ledger."""
    violations: list[str] = []
    for line in text.splitlines():
        normalized = line.lower()
        if "| p" not in normalized or " open |" not in normalized:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        if cells[1] != "0":
            violations.append(f"{filename} has non-zero open count: {line}")
    return violations


class ClosedLedgerIntakeChecker:
    """Checker for closed governance ledger intake policy."""

    def __init__(
        self,
        workspace: Path | None = None,
        expectations: tuple[ClosedLedgerExpectation, ...] = CLOSED_LEDGER_EXPECTATIONS,
    ) -> None:
        self.workspace = workspace or BACKEND_ROOT
        self.expectations = expectations
        self.start_time = time.time()

    def _elapsed_ms(self) -> float:
        """Return elapsed checker time in milliseconds."""
        return (time.time() - self.start_time) * 1000

    def _ledger_path(self, ledger: ClosedLedgerExpectation) -> Path:
        """Return the path to a ledger document under the workspace."""
        return self.workspace / "docs" / "governance" / ledger.filename

    def check_closed_ledgers(self) -> FitnessCheckResult:
        """Validate that closed governance ledgers remain intake-only."""
        result = FitnessCheckResult(rule_id=RULE_ID, passed=True)

        for ledger in self.expectations:
            path = self._ledger_path(ledger)
            if not path.exists():
                result.passed = False
                result.violations.append(f"{ledger.filename} is missing")
                continue

            text = path.read_text(encoding="utf-8")
            normalized_text = _collapse_whitespace(text)
            status = _status_line(text)
            if status != "Status: Closed (intake-only)":
                result.passed = False
                result.violations.append(f"{ledger.filename} must be 'Status: Closed (intake-only)', got {status!r}")

            for section in ledger.required_sections:
                if section not in text:
                    result.passed = False
                    result.violations.append(f"{ledger.filename} is missing section {section!r}")

            for phrase in ledger.required_phrases:
                if phrase not in normalized_text:
                    result.passed = False
                    result.violations.append(f"{ledger.filename} is missing intake phrase {phrase!r}")

            open_count_violations = _open_count_violations(ledger.filename, text)
            if open_count_violations:
                result.passed = False
                result.violations.extend(open_count_violations)

            if not open_count_violations:
                result.evidence.append(f"{ledger.filename}: closed intake policy present")

        result.duration_ms = self._elapsed_ms()
        return result


def main() -> int:
    """Run the closed ledger intake checker from the command line."""
    parser = argparse.ArgumentParser(description="Check closed governance ledger intake policy")
    parser.add_argument(
        "--workspace",
        type=str,
        default=None,
        help="Backend workspace root (default: src/backend inferred from script path)",
    )
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    args = parser.parse_args()

    workspace = Path(args.workspace) if args.workspace else None
    result = ClosedLedgerIntakeChecker(workspace).check_closed_ledgers()

    if args.json:
        print(
            json.dumps(
                {
                    "rule_id": result.rule_id,
                    "passed": result.passed,
                    "evidence": result.evidence,
                    "violations": result.violations,
                    "warnings": result.warnings,
                    "timestamp": result.timestamp,
                    "duration_ms": result.duration_ms,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(result.format())

    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
