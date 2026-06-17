"""Tests for the Release Readiness / Change-Advisory synthesis."""

from __future__ import annotations

import tempfile
import unittest

from polaris.cells.chief_engineer.blueprint.public.contracts import (
    GenerateTaskBlueprintCommandV1,
    IncidentSeverity,
    RegisterPostMortemCommandV1,
    RegisterRiskCommandV1,
    RegisterTechDebtCommandV1,
    RegisterTechRadarCommandV1,
    ReleaseDecision,
    RiskSeverity,
    TechDebtSeverity,
    TechRadarRing,
)
from polaris.cells.chief_engineer.blueprint.public.service import (
    assess_release_readiness,
    generate_task_blueprint,
    register_post_mortem,
    register_risk,
    register_tech_debt,
    register_tech_radar,
)


class TestReleaseReadiness(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_clean_workspace_is_go(self) -> None:
        decision = assess_release_readiness(self.workspace)
        self.assertEqual(decision.decision, ReleaseDecision.GO)
        self.assertEqual(decision.blocker_count, 0)
        self.assertEqual(decision.warning_count, 0)

    def test_open_blocker_risk_is_no_go(self) -> None:
        register_risk(
            RegisterRiskCommandV1(
                task_id="t1",
                title="data loss",
                severity=RiskSeverity.BLOCKER,
                owner="ce",
                mitigation="m",
                workspace=self.workspace,
            )
        )
        decision = assess_release_readiness(self.workspace)
        self.assertEqual(decision.decision, ReleaseDecision.NO_GO)
        self.assertTrue(any("risk" in b for b in decision.blockers))
        self.assertEqual(decision.signals["risk"]["open_critical_or_blocker"], 1)

    def test_open_high_risk_only_is_conditional(self) -> None:
        register_risk(
            RegisterRiskCommandV1(
                task_id="t1",
                title="edge case",
                severity=RiskSeverity.HIGH,
                owner="ce",
                mitigation="m",
                workspace=self.workspace,
            )
        )
        decision = assess_release_readiness(self.workspace)
        self.assertEqual(decision.decision, ReleaseDecision.CONDITIONAL_GO)
        self.assertEqual(decision.blocker_count, 0)
        self.assertGreaterEqual(decision.warning_count, 1)

    def test_open_sev1_incident_is_no_go(self) -> None:
        register_post_mortem(
            RegisterPostMortemCommandV1(
                title="prod outage",
                severity=IncidentSeverity.SEV1,
                occurred_at="t",
                owner="ce",
                workspace=self.workspace,
            )
        )
        decision = assess_release_readiness(self.workspace)
        self.assertEqual(decision.decision, ReleaseDecision.NO_GO)
        self.assertEqual(decision.signals["post_mortem"]["open_sev1"], 1)

    def test_stack_policy_violation_is_no_go(self) -> None:
        register_tech_radar(
            RegisterTechRadarCommandV1(
                library="jquery",
                ring=TechRadarRing.DEPRECATED,
                owner="ce",
                workspace=self.workspace,
            )
        )
        decision = assess_release_readiness(self.workspace, libraries=["jQuery"])
        self.assertEqual(decision.decision, ReleaseDecision.NO_GO)
        self.assertEqual(decision.signals["stack_policy"]["violations"], 1)

    def test_unpaid_fatal_tech_debt_is_no_go(self) -> None:
        register_tech_debt(
            RegisterTechDebtCommandV1(
                title="hardcoded secret",
                description="",
                severity=TechDebtSeverity.FATAL,
                surface="src/x.py",
                owner="ce",
                workspace=self.workspace,
            )
        )
        decision = assess_release_readiness(self.workspace)
        self.assertEqual(decision.decision, ReleaseDecision.NO_GO)
        self.assertEqual(decision.signals["tech_debt"]["unpaid_fatal"], 1)

    def test_blueprint_quality_gate_blocker_is_no_go(self) -> None:
        # A blueprint missing acceptance_criteria fails the quality gate.
        result = generate_task_blueprint(
            GenerateTaskBlueprintCommandV1(
                task_id="t-bp",
                workspace=self.workspace,
                objective="ship",
                context={"task_title": "BP", "target_files": ["a.py"]},  # no acceptance
            )
        )
        decision = assess_release_readiness(self.workspace, blueprint_ids=[result.blueprint_id or ""])
        self.assertEqual(decision.decision, ReleaseDecision.NO_GO)
        self.assertEqual(decision.signals["quality_gate"]["blocked"], 1)

    def test_risk_not_double_counted_with_blueprint_gate(self) -> None:
        # A blocker risk on a release-candidate blueprint's task must count ONCE
        # (in the risk signal), not also in the quality_gate signal — the gate
        # folds the same risk in, so it is deduped via open_blocker_risk_count.
        register_risk(
            RegisterRiskCommandV1(
                task_id="t-dup",
                title="data loss",
                severity=RiskSeverity.BLOCKER,
                owner="ce",
                mitigation="m",
                workspace=self.workspace,
            )
        )
        result = generate_task_blueprint(
            GenerateTaskBlueprintCommandV1(
                task_id="t-dup",
                workspace=self.workspace,
                objective="ship",
                context={
                    "task_title": "BP",
                    "acceptance_criteria": ["a"],  # contract-complete: only the risk blocks
                    "execution_checklist": ["x"],
                    "target_files": ["a.py"],
                },
            )
        )
        decision = assess_release_readiness(self.workspace, blueprint_ids=[result.blueprint_id or ""])
        self.assertEqual(decision.decision, ReleaseDecision.NO_GO)
        # The risk is counted once (risk signal), NOT again in quality_gate.
        self.assertEqual(decision.signals["risk"]["open_critical_or_blocker"], 1)
        self.assertEqual(decision.signals["quality_gate"]["blocked"], 0)
        self.assertEqual(decision.blocker_count, 1)

    def test_contract_blocker_still_flagged_in_quality_gate(self) -> None:
        # A genuine contract blocker (missing acceptance) IS flagged in quality_gate
        # even with no risks present.
        result = generate_task_blueprint(
            GenerateTaskBlueprintCommandV1(
                task_id="t-c",
                workspace=self.workspace,
                objective="ship",
                context={"task_title": "BP", "target_files": ["a.py"]},  # no acceptance
            )
        )
        decision = assess_release_readiness(self.workspace, blueprint_ids=[result.blueprint_id or ""])
        self.assertEqual(decision.decision, ReleaseDecision.NO_GO)
        self.assertEqual(decision.signals["quality_gate"]["blocked"], 1)
        self.assertEqual(decision.signals["risk"]["open_critical_or_blocker"], 0)

    def test_missing_blueprint_is_blocker(self) -> None:
        decision = assess_release_readiness(self.workspace, blueprint_ids=["ce_missing"])
        self.assertEqual(decision.decision, ReleaseDecision.NO_GO)
        self.assertTrue(any("not found" in b for b in decision.blockers))

    def test_conditional_go_multi_signal_aggregation(self) -> None:
        # Three warning-level signals across different sources => CONDITIONAL_GO
        # with warning_count == 3 and zero blockers.
        register_risk(
            RegisterRiskCommandV1(
                task_id="t1",
                title="edge case",
                severity=RiskSeverity.HIGH,
                owner="ce",
                mitigation="m",
                workspace=self.workspace,
            )
        )
        register_post_mortem(
            RegisterPostMortemCommandV1(
                title="degraded",
                severity=IncidentSeverity.SEV2,
                occurred_at="t",
                owner="ce",
                workspace=self.workspace,
            )
        )
        register_tech_debt(
            RegisterTechDebtCommandV1(
                title="manual escaping",
                description="",
                severity=TechDebtSeverity.SEVERE,
                surface="src/db.py",
                owner="ce",
                workspace=self.workspace,
            )
        )
        decision = assess_release_readiness(self.workspace)
        self.assertEqual(decision.decision, ReleaseDecision.CONDITIONAL_GO)
        self.assertEqual(decision.blocker_count, 0)
        self.assertEqual(decision.warning_count, 3)
        self.assertEqual(decision.signals["risk"]["open_high"], 1)
        self.assertEqual(decision.signals["post_mortem"]["open_sev2"], 1)
        self.assertEqual(decision.signals["tech_debt"]["unpaid_severe"], 1)

    def test_missing_blueprint_signal_structure(self) -> None:
        decision = assess_release_readiness(self.workspace, blueprint_ids=["ce_missing"])
        self.assertEqual(decision.decision, ReleaseDecision.NO_GO)
        self.assertEqual(decision.signals["quality_gate"]["assessed"], 1)
        self.assertEqual(decision.signals["quality_gate"]["blocked"], 1)
        self.assertTrue(any("not found" in b for b in decision.blockers))

    def test_signals_always_present(self) -> None:
        decision = assess_release_readiness(self.workspace)
        for key in ("risk", "quality_gate", "post_mortem", "stack_policy", "tech_debt"):
            self.assertIn(key, decision.signals)
        # to_dict round-trips.
        data = decision.to_dict()
        self.assertEqual(data["decision"], "go")
        self.assertIn("signals", data)


if __name__ == "__main__":
    unittest.main()
