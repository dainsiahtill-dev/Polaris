"""Assemble the default Director Runtime repair rule registry."""

from __future__ import annotations

from collections.abc import Sequence

from ..contracts import RepairDiagnostic
from ._models import RepairCoverageReport, RepairRuleRegistry
from ._rules_pre_typescript import pre_typescript_repair_rules
from ._rules_typescript import typescript_repair_rules


def default_repair_rule_registry() -> RepairRuleRegistry:
    """Return the initial Director Runtime repair rule registry."""

    return RepairRuleRegistry(
        (
            *pre_typescript_repair_rules(),
            *typescript_repair_rules(),
        )
    )


def build_repair_coverage_report(
    diagnostics: Sequence[RepairDiagnostic],
    registry: RepairRuleRegistry | None = None,
) -> RepairCoverageReport:
    """Build a coverage report for diagnostics using the configured registry."""

    return (registry or default_repair_rule_registry()).coverage(diagnostics)
