"""Pure policy for verified/retired migration-unit evidence checks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

RULE_ID = "verified_or_retired_units_require_evidence"


@dataclass(frozen=True)
class VerifiedEvidencePolicyResult:
    """Evaluation result for the verified-evidence governance policy."""

    rule_id: str
    passed: bool
    evidence: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _has_verification_evidence(unit: Mapping[str, Any]) -> bool:
    """Return true when a migration unit carries explicit verification evidence."""
    verification = unit.get("verification", {})
    if not isinstance(verification, Mapping):
        verification = {}

    evidence_fields = (
        verification.get("checks", []),
        verification.get("required_tests", []),
        verification.get("docs_updates", []),
        verification.get("graph_updates", []),
    )
    if any(isinstance(field, list) and len(field) > 0 for field in evidence_fields):
        return True
    return bool(unit.get("evidence_notes"))


def evaluate_verified_evidence(workspace: Path) -> VerifiedEvidencePolicyResult:
    """Evaluate verified/retired migration units for required evidence.

    The policy is intentionally independent of CLI, pytest, and the aggregate
    fitness runner so all entrypoints consume the same authoritative logic.

    Complexity:
        O(n) time over migration units and O(n) space for emitted messages.
    """
    ledger_path = workspace / "docs" / "migration" / "ledger.yaml"
    if not ledger_path.exists():
        return VerifiedEvidencePolicyResult(
            rule_id=RULE_ID,
            passed=False,
            violations=(f"Ledger not found: {ledger_path}",),
        )

    try:
        with ledger_path.open(encoding="utf-8") as stream:
            ledger = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        return VerifiedEvidencePolicyResult(
            rule_id=RULE_ID,
            passed=False,
            violations=(f"Failed to parse ledger.yaml: {exc}",),
        )

    if not isinstance(ledger, Mapping):
        return VerifiedEvidencePolicyResult(
            rule_id=RULE_ID,
            passed=False,
            violations=("docs/migration/ledger.yaml must contain a mapping",),
        )

    units = ledger.get("units", [])
    if not isinstance(units, list):
        return VerifiedEvidencePolicyResult(
            rule_id=RULE_ID,
            passed=False,
            violations=("docs/migration/ledger.yaml field 'units' must be a list",),
        )
    if not units:
        return VerifiedEvidencePolicyResult(
            rule_id=RULE_ID,
            passed=True,
            warnings=("No migration units found in ledger",),
        )

    evidence: list[str] = []
    violations: list[str] = []
    checked_units: list[str] = []

    for raw_unit in units:
        if not isinstance(raw_unit, Mapping):
            continue

        status = raw_unit.get("status", "")
        unit_id = str(raw_unit.get("id", "unknown"))
        if status not in ("verified", "retired"):
            continue

        checked_units.append(unit_id)
        if _has_verification_evidence(raw_unit):
            evidence.append(f"{unit_id}: has verification evidence")
        else:
            violations.append(f"{unit_id}: status={status} but missing verification evidence")

    warnings: list[str] = []
    if checked_units:
        evidence.append(f"Checked {len(checked_units)} verified/retired units")
    else:
        warnings.append("No verified/retired units found to check")

    if violations:
        warnings.append(f"{len(violations)} units lack verification evidence")

    return VerifiedEvidencePolicyResult(
        rule_id=RULE_ID,
        passed=not violations,
        evidence=tuple(evidence),
        violations=tuple(violations),
        warnings=tuple(warnings),
    )
