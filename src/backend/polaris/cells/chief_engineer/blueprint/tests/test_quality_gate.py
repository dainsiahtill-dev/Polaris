"""Tests for the deterministic quality-gate evaluator."""

from __future__ import annotations

import unittest

from polaris.cells.chief_engineer.blueprint.internal.quality_gate import (
    evaluate_quality_gate,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    RiskRecordV1,
    RiskSeverity,
    RiskStatus,
)


class TestQualityGate(unittest.TestCase):
    def test_empty_target_files_is_blocker(self) -> None:
        gate = evaluate_quality_gate({})
        self.assertFalse(gate.passed)
        self.assertIn(
            "target_files is empty; handoff requires at least one target file.",
            gate.blockers,
        )
        self.assertIn(
            "acceptance_criteria is empty; handoff requires QA-evaluable acceptance.",
            gate.blockers,
        )

    def test_missing_checklist_is_warning(self) -> None:
        gate = evaluate_quality_gate(
            {
                "target_files": ["src/main.py"],
                "acceptance_criteria": ["a", "b", "c", "d"],
            }
        )
        self.assertIn("execution_checklist is empty; Director will lack ordered steps.", gate.warnings)
        self.assertTrue(gate.passed)
        self.assertEqual(gate.warning_count, 2)

    def test_short_recommendations_is_info(self) -> None:
        gate = evaluate_quality_gate(
            {
                "target_files": ["a"],
                "acceptance_criteria": ["a"],
                "recommendations": ["only one"],
            }
        )
        self.assertTrue(any("recommendations is short" in note for note in gate.info))
        self.assertTrue(gate.passed)

    def test_open_blocker_risk_pushes_to_blocker(self) -> None:
        risks = [
            RiskRecordV1(
                risk_id="r1",
                task_id="t1",
                title="schema migration data loss",
                severity=RiskSeverity.BLOCKER,
                owner="chief_engineer",
                mitigation="dual-write",
                status=RiskStatus.OPEN,
                detected_at="2026-06-17T00:00:00Z",
            )
        ]
        gate = evaluate_quality_gate(
            {
                "target_files": ["a.py"],
                "acceptance_criteria": ["a"],
            },
            risks=risks,
        )
        self.assertFalse(gate.passed)
        self.assertTrue(any("r1" in blocker for blocker in gate.blockers))

    def test_open_high_risk_is_warning(self) -> None:
        risks = [
            RiskRecordV1(
                risk_id="r2",
                task_id="t1",
                title="rate limit edge case",
                severity=RiskSeverity.HIGH,
                owner="chief_engineer",
                mitigation="circuit breaker",
                status=RiskStatus.OPEN,
                detected_at="2026-06-17T00:00:00Z",
            )
        ]
        gate = evaluate_quality_gate(
            {
                "target_files": ["a.py"],
                "acceptance_criteria": ["a"],
            },
            risks=risks,
        )
        self.assertTrue(gate.passed)
        self.assertTrue(any("r2" in warning for warning in gate.warnings))

    def test_resolved_risk_does_not_count(self) -> None:
        risks = [
            RiskRecordV1(
                risk_id="r3",
                task_id="t1",
                title="t",
                severity=RiskSeverity.CRITICAL,
                owner="chief_engineer",
                mitigation="m",
                status=RiskStatus.RESOLVED,
                detected_at="2026-06-17T00:00:00Z",
            )
        ]
        gate = evaluate_quality_gate(
            {
                "target_files": ["a.py"],
                "acceptance_criteria": ["a"],
            },
            risks=risks,
        )
        self.assertTrue(gate.passed)
        self.assertEqual(gate.blocker_count, 0)

    def test_pure_pass(self) -> None:
        gate = evaluate_quality_gate(
            {
                "target_files": ["a.py"],
                "acceptance_criteria": ["a", "b"],
                "execution_checklist": ["step 1", "step 2"],
                "dependencies": [],
                "recommendations": ["r1", "r2"],
            }
        )
        self.assertTrue(gate.passed)
        self.assertEqual(gate.blocker_count, 0)

    def test_risk_dicts_coerced_from_blueprint_risk_register(self) -> None:
        # evaluate_quality_gate must coerce risk DICTS (not just RiskRecordV1),
        # both from the `risks=` arg and the embedded blueprint["risk_register"].
        gate = evaluate_quality_gate(
            {
                "target_files": ["a.py"],
                "acceptance_criteria": ["a"],
                "risk_register": [
                    {
                        "risk_id": "r-dict",
                        "task_id": "t1",
                        "title": "embedded blocker",
                        "severity": "blocker",
                        "owner": "ce",
                        "mitigation": "m",
                        "status": "open",
                        "detected_at": "2026-06-17T00:00:00Z",
                    }
                ],
            }
        )
        self.assertFalse(gate.passed)
        self.assertTrue(any("r-dict" in b for b in gate.blockers))

    def test_malformed_risk_dict_is_skipped_not_crash(self) -> None:
        # A risk dict with an invalid severity must be skipped, not raise.
        gate = evaluate_quality_gate(
            {"target_files": ["a.py"], "acceptance_criteria": ["a"]},
            risks=[{"severity": "bogus", "status": "open", "title": "x"}],
        )
        self.assertTrue(gate.passed)

    def test_evaluated_at_override(self) -> None:
        gate = evaluate_quality_gate(
            {"target_files": ["a"], "acceptance_criteria": ["a"]},
            evaluated_at="2026-01-01T00:00:00Z",
        )
        self.assertEqual(gate.evaluated_at, "2026-01-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
