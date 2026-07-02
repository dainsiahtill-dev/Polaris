"""Pure policy checks for closed governance ledger intake rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

RULE_ID = "closed_governance_ledgers_intake_only"


@dataclass(frozen=True)
class ClosedLedgerExpectation:
    """Expected closure markers for a governance ledger."""

    filename: str
    required_sections: tuple[str, ...]
    required_phrases: tuple[str, ...]


@dataclass(frozen=True)
class ClosedLedgerIntakeReport:
    """Pure validation report for closed governance ledger intake policy."""

    passed: bool
    evidence: tuple[str, ...] = field(default_factory=tuple)
    violations: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)


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


def _ledger_path(workspace: Path, ledger: ClosedLedgerExpectation) -> Path:
    """Return the path to a ledger document under the workspace."""
    return workspace / "docs" / "governance" / ledger.filename


def evaluate_closed_ledger_intake(
    workspace: Path,
    expectations: tuple[ClosedLedgerExpectation, ...] = CLOSED_LEDGER_EXPECTATIONS,
) -> ClosedLedgerIntakeReport:
    """Validate that closed governance ledgers remain intake-only.

    Args:
        workspace: Backend workspace root, usually ``src/backend``.
        expectations: Ledger closure contracts to enforce.

    Returns:
        Immutable report with evidence and violations. The check is O(L + N)
        where L is the number of configured ledgers and N is total ledger text
        size; memory usage is O(N) for the loaded Markdown text.
    """
    evidence: list[str] = []
    violations: list[str] = []

    for ledger in expectations:
        path = _ledger_path(workspace, ledger)
        if not path.exists():
            violations.append(f"{ledger.filename} is missing")
            continue

        text = path.read_text(encoding="utf-8")
        normalized_text = _collapse_whitespace(text)
        status = _status_line(text)
        if status != "Status: Closed (intake-only)":
            violations.append(f"{ledger.filename} must be 'Status: Closed (intake-only)', got {status!r}")

        for section in ledger.required_sections:
            if section not in text:
                violations.append(f"{ledger.filename} is missing section {section!r}")

        for phrase in ledger.required_phrases:
            if phrase not in normalized_text:
                violations.append(f"{ledger.filename} is missing intake phrase {phrase!r}")

        open_count_violations = _open_count_violations(ledger.filename, text)
        violations.extend(open_count_violations)

        if not open_count_violations:
            evidence.append(f"{ledger.filename}: closed intake policy present")

    return ClosedLedgerIntakeReport(
        passed=not violations,
        evidence=tuple(evidence),
        violations=tuple(violations),
    )
