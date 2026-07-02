"""Tests for the closed governance ledger intake policy checker."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml
from docs.governance.ci.scripts import fitness_rule_checker
from docs.governance.ci.scripts.check_closed_ledger_intake import (
    CLOSED_LEDGER_EXPECTATIONS,
    ClosedLedgerExpectation,
    ClosedLedgerIntakeChecker,
)

_FITNESS_RULES_PATH = Path(__file__).resolve().parents[2] / "fitness-rules.yaml"


def _write_ledger(workspace: Path, ledger: ClosedLedgerExpectation, text: str) -> Path:
    """Write a ledger document into a temporary backend workspace."""
    ledger_dir = workspace / "docs" / "governance"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    path = ledger_dir / ledger.filename
    path.write_text(text, encoding="utf-8")
    return path


def _load_fitness_rule(rule_id: str) -> Mapping[str, object]:
    """Load one rule declaration from the governance fitness-rule registry."""
    with _FITNESS_RULES_PATH.open(encoding="utf-8") as stream:
        rules_doc = yaml.safe_load(stream)
    if not isinstance(rules_doc, Mapping):
        raise AssertionError(f"{_FITNESS_RULES_PATH} must contain a mapping")

    rules = rules_doc.get("rules")
    if not isinstance(rules, list):
        raise AssertionError(f"{_FITNESS_RULES_PATH} must contain a rules list")

    for rule in rules:
        if isinstance(rule, Mapping) and rule.get("id") == rule_id:
            return rule
    raise AssertionError(f"missing fitness rule declaration: {rule_id}")


def test_live_closed_governance_ledgers_are_intake_only() -> None:
    """Live closed convergence ledgers must not become active backlogs."""
    result = ClosedLedgerIntakeChecker().check_closed_ledgers()

    assert result.passed is True
    assert result.violations == []
    assert len(result.evidence) == len(CLOSED_LEDGER_EXPECTATIONS)


def test_fitness_rule_checker_runs_closed_ledger_rule() -> None:
    """The unified fitness-rule entrypoint must know the closed-ledger rule."""
    result = fitness_rule_checker.run_rule("closed_governance_ledgers_intake_only")

    assert result.passed is True
    assert result.rule_id == "closed_governance_ledgers_intake_only"
    assert len(result.evidence) == len(CLOSED_LEDGER_EXPECTATIONS)


def test_default_fitness_rule_suite_includes_closed_ledger_rule() -> None:
    """The default fitness suite must include every declared rule method."""
    assert "closed_governance_ledgers_intake_only" in fitness_rule_checker.DEFAULT_RULE_IDS
    checker = fitness_rule_checker.get_checker()
    for rule_id in fitness_rule_checker.DEFAULT_RULE_IDS:
        assert hasattr(checker, f"check_{rule_id}"), rule_id


def test_closed_ledger_fitness_rule_declaration_matches_default_suite() -> None:
    """The YAML rule declaration and default runner must stay wired together."""
    rule = _load_fitness_rule("closed_governance_ledgers_intake_only")

    assert rule.get("severity") == "high"
    assert rule.get("current_status") == "enforced_non_regressive"
    assert "closed_governance_ledgers_intake_only" in fitness_rule_checker.DEFAULT_RULE_IDS

    evidence = rule.get("evidence")
    assert isinstance(evidence, list)
    expected_evidence = {f"docs/governance/{ledger.filename}" for ledger in CLOSED_LEDGER_EXPECTATIONS}
    assert expected_evidence.issubset(set(evidence))

    desired_automation = rule.get("desired_automation")
    assert isinstance(desired_automation, list)
    assert any("check_closed_ledger_intake.py --json" in step for step in desired_automation)


@pytest.mark.parametrize("status", ("Status: Active", "Status: Closed"))
def test_checker_rejects_non_intake_status(tmp_path: Path, status: str) -> None:
    """Closed ledgers must use the explicit intake-only status."""
    ledger = CLOSED_LEDGER_EXPECTATIONS[0]
    _write_ledger(
        tmp_path,
        ledger,
        f"""# Test Ledger

{status}

## Current Closure State

Closed rows must not be reopened.
New execution facts must converge on the existing chain.

| Class | Count | Meaning |
| --- | ---: | --- |
| P0 open | 0 | none |
""",
    )

    result = ClosedLedgerIntakeChecker(tmp_path, expectations=(ledger,)).check_closed_ledgers()

    assert result.passed is False
    assert any("Closed (intake-only)" in violation for violation in result.violations)


def test_checker_rejects_non_zero_open_count(tmp_path: Path) -> None:
    """Closed ledgers cannot carry non-zero P-level open-count rows."""
    ledger = CLOSED_LEDGER_EXPECTATIONS[0]
    _write_ledger(
        tmp_path,
        ledger,
        """# Test Ledger

Status: Closed (intake-only)

## Current Closure State

Closed rows must not be reopened.
New execution facts must converge on the existing chain.

| Class | Count | Meaning |
| --- | ---: | --- |
| P0 open | 1 | regression |
""",
    )

    result = ClosedLedgerIntakeChecker(tmp_path, expectations=(ledger,)).check_closed_ledgers()

    assert result.passed is False
    assert any("non-zero open count" in violation for violation in result.violations)


def test_checker_rejects_missing_intake_phrase(tmp_path: Path) -> None:
    """Closed ledgers must explain how new evidence-backed intake works."""
    ledger = CLOSED_LEDGER_EXPECTATIONS[0]
    _write_ledger(
        tmp_path,
        ledger,
        """# Test Ledger

Status: Closed (intake-only)

## Current Closure State

Closed rows must not be reopened.

| Class | Count | Meaning |
| --- | ---: | --- |
| P0 open | 0 | none |
""",
    )

    result = ClosedLedgerIntakeChecker(tmp_path, expectations=(ledger,)).check_closed_ledgers()

    assert result.passed is False
    assert any("missing intake phrase" in violation for violation in result.violations)
