"""Regression tests for closed governance ledger intake policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parents[5]
_LEDGER_DIR = _BACKEND_ROOT / "docs" / "governance"


@dataclass(frozen=True)
class ClosedLedgerExpectation:
    """Expected closure markers for a governance ledger."""

    filename: str
    required_sections: tuple[str, ...]
    required_phrases: tuple[str, ...]


_CLOSED_LEDGER_EXPECTATIONS = (
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


def _read_ledger(filename: str) -> str:
    """Read a governance ledger using explicit UTF-8 decoding."""
    return (_LEDGER_DIR / filename).read_text(encoding="utf-8")


def _status_line(text: str) -> str:
    """Return the first status line from a ledger document."""
    for line in text.splitlines():
        if line.startswith("Status:"):
            return line.strip()
    raise AssertionError("ledger is missing a Status line")


def _collapse_whitespace(text: str) -> str:
    """Collapse Markdown line wrapping so phrase checks stay semantic."""
    return " ".join(text.split())


@pytest.mark.parametrize(
    "ledger",
    _CLOSED_LEDGER_EXPECTATIONS,
    ids=lambda ledger: ledger.filename,
)
def test_closed_governance_ledgers_are_intake_only(
    ledger: ClosedLedgerExpectation,
) -> None:
    """Closed convergence ledgers must not silently become active backlogs."""
    text = _read_ledger(ledger.filename)
    normalized_text = _collapse_whitespace(text)

    assert _status_line(text) == "Status: Closed (intake-only)"
    for section in ledger.required_sections:
        assert section in text
    for phrase in ledger.required_phrases:
        assert phrase in normalized_text


@pytest.mark.parametrize(
    "ledger",
    _CLOSED_LEDGER_EXPECTATIONS,
    ids=lambda ledger: ledger.filename,
)
def test_closed_governance_ledgers_keep_zero_open_counts(
    ledger: ClosedLedgerExpectation,
) -> None:
    """Closed ledgers with open-count rows must keep those rows at zero."""
    text = _read_ledger(ledger.filename)

    for line in text.splitlines():
        normalized = line.lower()
        if "| p" in normalized and " open |" in normalized:
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            assert len(cells) >= 2
            assert cells[1] == "0", f"{ledger.filename} has non-zero open count: {line}"
