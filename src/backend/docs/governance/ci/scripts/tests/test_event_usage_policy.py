"""Tests for KernelOne event usage policy wiring."""

from __future__ import annotations

from pathlib import Path

from docs.governance.ci.scripts import fitness_rule_checker
from docs.governance.ci.scripts.check_cell_kernelone_05 import CellKernelone05Checker
from docs.governance.ci.scripts.event_usage_policy import evaluate_event_usage


def _write_canonical_events(workspace: Path) -> None:
    """Write the minimal canonical KernelOne event module fixture."""
    events_dir = workspace / "polaris" / "kernelone" / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    (events_dir / "fact_events.py").write_text(
        "def emit_fact_event(name: str) -> None:\n    return None\n",
        encoding="utf-8",
    )


def _write_cell_source(workspace: Path, relative_path: str, content: str) -> None:
    """Write a Cell source fixture into a temporary workspace."""
    path = workspace / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_event_usage_entrypoints_use_canonical_policy(tmp_path: Path) -> None:
    """The standalone and aggregate entrypoints must match the policy."""
    _write_canonical_events(tmp_path)
    _write_cell_source(
        tmp_path,
        "polaris/cells/runtime/example/internal/events.py",
        "def emit_event(payload: dict[str, str]) -> None:\n    return None\n",
    )

    policy = evaluate_event_usage(tmp_path)
    standalone = CellKernelone05Checker(tmp_path).check()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_event_usage()

    assert policy.passed is False
    assert standalone.passed is False
    assert aggregate.passed is False
    assert aggregate.rule_id == policy.rule_id == standalone.rule_id
    assert aggregate.violations == list(policy.violations) == standalone.violations
    assert any("Local event emitter" in violation for violation in aggregate.violations)


def test_event_usage_policy_reports_non_canonical_import_warning(tmp_path: Path) -> None:
    """Non-canonical role event imports should be warnings, not duplicate logic."""
    _write_canonical_events(tmp_path)
    _write_cell_source(
        tmp_path,
        "polaris/cells/roles/kernel/internal/consumer.py",
        "from polaris.cells.roles.session.internal.events import SessionEvent\n",
    )

    policy = evaluate_event_usage(tmp_path)
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_event_usage()

    assert policy.passed is True
    assert aggregate.passed is True
    assert aggregate.warnings == list(policy.warnings)
    assert aggregate.warnings == ["Non-canonical event import in polaris/cells/roles/kernel/internal/consumer.py"]


def test_event_usage_policy_requires_canonical_kernelone_events(tmp_path: Path) -> None:
    """Missing canonical KernelOne event API is a hard failure."""
    result = evaluate_event_usage(tmp_path)

    assert result.passed is False
    assert result.violations == ("Canonical events not found in kernelone/events/",)
