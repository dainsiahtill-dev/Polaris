"""Tests for verified/retired migration evidence policy wiring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from docs.governance.ci.scripts import fitness_rule_checker
from docs.governance.ci.scripts.check_verified_evidence import VerifiedEvidenceChecker, main


def _write_ledger(workspace: Path, units: list[dict[str, Any]]) -> None:
    """Write a migration ledger fixture into the temporary workspace."""
    ledger_path = workspace / "docs" / "migration" / "ledger.yaml"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(yaml.safe_dump({"units": units}, sort_keys=False), encoding="utf-8")


def test_fitness_runner_uses_canonical_verified_evidence_policy(tmp_path: Path) -> None:
    """The aggregate fitness runner must match the canonical evidence policy."""
    _write_ledger(
        tmp_path,
        [
            {
                "id": "MIG-VERIFIED-001",
                "status": "verified",
                "verification": {"checks": ["python -m pytest tests/test_contract.py"]},
            },
            {
                "id": "MIG-RETIRED-001",
                "status": "retired",
                "evidence_notes": "Retired after public cell contract replaced the legacy path.",
            },
        ],
    )

    canonical = VerifiedEvidenceChecker(tmp_path).check_verified_evidence()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_verified_evidence()

    assert canonical.passed is True
    assert aggregate.passed is True
    assert aggregate.rule_id == canonical.rule_id
    assert aggregate.violations == canonical.violations
    assert aggregate.warnings == canonical.warnings
    assert "Checked 2 verified/retired units" in aggregate.evidence


def test_fitness_runner_reports_missing_verified_evidence(tmp_path: Path) -> None:
    """The aggregate runner must preserve canonical missing-evidence failures."""
    _write_ledger(
        tmp_path,
        [
            {
                "id": "MIG-MISSING-001",
                "status": "verified",
                "verification": {"checks": []},
            }
        ],
    )

    canonical = VerifiedEvidenceChecker(tmp_path).check_verified_evidence()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_verified_evidence()

    assert canonical.passed is False
    assert aggregate.passed is False
    assert aggregate.rule_id == canonical.rule_id
    assert aggregate.violations == canonical.violations
    assert any("missing verification evidence" in violation for violation in aggregate.violations)


def test_verified_evidence_cli_json_outputs_one_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The CLI JSON mode must be machine-readable without human-format prelude."""
    _write_ledger(
        tmp_path,
        [
            {
                "id": "MIG-JSON-001",
                "status": "verified",
                "evidence_notes": "Verified by the CLI JSON fixture.",
            }
        ],
    )

    exit_code = main(["--workspace", str(tmp_path), "--json"])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["rule_id"] == "verified_or_retired_units_require_evidence"
    assert payload["passed"] is True
    assert output.lstrip().startswith("{")
