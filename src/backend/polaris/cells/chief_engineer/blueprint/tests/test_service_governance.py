"""End-to-end service tests for the Tier-1 governance surface.

Exercises the public service functions: register_risk, register_tech_debt,
generate_task_blueprint (with governance attached), attach_governance_to_blueprint.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from polaris.cells.chief_engineer.blueprint.public.contracts import (
    ChiefEngineerBlueprintErrorV1,
    GenerateTaskBlueprintCommandV1,
    ListRisksQueryV1,
    ListTechDebtQueryV1,
    RegisterRiskCommandV1,
    RegisterTechDebtCommandV1,
    RiskSeverity,
    RiskStatus,
    TechDebtSeverity,
    TechDebtStatus,
    UpdateRiskStatusCommandV1,
    UpdateTechDebtStatusCommandV1,
)
from polaris.cells.chief_engineer.blueprint.public.service import (
    assert_handoff_ready,
    attach_governance_to_blueprint,
    build_blueprint_governance,
    build_ce_handoff_decision,
    evaluate_handoff_decision,
    evaluate_handoff_decision_for_blueprint,
    generate_task_blueprint,
    get_blueprint_governance,
    get_blueprint_status,
    list_risks,
    list_tech_debt,
    register_risk,
    register_tech_debt,
    summarize_risks,
    summarize_tech_debt,
    update_risk_status,
    update_tech_debt_status,
    validate_director_handoff_from_payload,
)
from polaris.kernelone.storage import resolve_logical_path


def _blueprint_json_path(workspace: str, blueprint_id: str) -> Path:
    """Resolve the real on-disk path of a persisted blueprint.

    ``resolve_logical_path`` may remap ``workspace`` to a kernelone cache
    root, so a naive ``Path(workspace)/"runtime"/...`` join points at the
    wrong location. Resolve the same logical domain to find the real file.
    """
    return Path(resolve_logical_path(workspace, "runtime/blueprints")) / f"{blueprint_id}.json"


class TestServiceGovernance(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_generate_blueprint_attaches_governance(self) -> None:
        result = generate_task_blueprint(
            GenerateTaskBlueprintCommandV1(
                task_id="task-1",
                workspace=self.workspace,
                objective="ship the user-profile page",
                context={
                    "task_title": "User Profile Page",
                    "acceptance_criteria": ["renders name from /api/me"],
                    "execution_checklist": ["read", "write", "test"],
                    "target_files": ["src/pages/profile.py"],
                },
            )
        )
        self.assertTrue(result.ok)
        path = _blueprint_json_path(self.workspace, result.blueprint_id or "")
        self.assertTrue(path.exists())
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["blueprint_hash"], result.blueprint_hash)
        self.assertIn("governance", data)
        self.assertIn("quality_gate", data["governance"])
        self.assertIn("rollback", data["governance"])
        self.assertIn("risk_summary", data["governance"])
        self.assertIn("tech_debt_summary", data["governance"])
        # The blueprint has only 1 acceptance criterion, so no "dependencies" warning
        self.assertTrue(data["governance"]["quality_gate"]["passed"])

    def test_open_blocker_risk_blocks_handoff(self) -> None:
        register_risk(
            RegisterRiskCommandV1(
                task_id="task-2",
                title="data loss possible",
                severity=RiskSeverity.BLOCKER,
                owner="chief_engineer",
                mitigation="dual-write",
                workspace=self.workspace,
            )
        )
        result = generate_task_blueprint(
            GenerateTaskBlueprintCommandV1(
                task_id="task-2",
                workspace=self.workspace,
                objective="migrate the users table",
                context={
                    "task_title": "User Table Migration",
                    "acceptance_criteria": ["data intact post-migration"],
                    "execution_checklist": ["snapshot", "migrate", "verify"],
                    "target_files": ["migrations/001_users.py"],
                },
            )
        )
        path = _blueprint_json_path(self.workspace, result.blueprint_id or "")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertFalse(data["handoff_ready"])
        self.assertGreaterEqual(data["governance"]["quality_gate"]["blocker_count"], 1)
        # preconditions list SATISFIED checks; with an open blocker risk the
        # "no_blocker_risks_open" check is NOT satisfied, so it must be absent.
        self.assertNotIn(
            "no_blocker_risks_open",
            data["governance"]["rollback"]["preconditions"],
        )

    def test_resolving_risk_unblocks_handoff(self) -> None:
        record = register_risk(
            RegisterRiskCommandV1(
                task_id="task-3",
                title="data loss",
                severity=RiskSeverity.CRITICAL,
                owner="chief_engineer",
                mitigation="dual-write",
                workspace=self.workspace,
            )
        )
        update_risk_status(
            UpdateRiskStatusCommandV1(
                workspace=self.workspace,
                risk_id=record.risk_id,
                status=RiskStatus.MITIGATING,
                note="feature flag staged",
            )
        )
        result = generate_task_blueprint(
            GenerateTaskBlueprintCommandV1(
                task_id="task-3",
                workspace=self.workspace,
                objective="ship the page",
                context={
                    "task_title": "T3",
                    "acceptance_criteria": ["a"],
                    "execution_checklist": ["x"],
                    "target_files": ["a.py"],
                },
            )
        )
        path = _blueprint_json_path(self.workspace, result.blueprint_id or "")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        # Open critical would block; "mitigating" does not, so the gate should pass
        self.assertTrue(data["handoff_ready"])

    def test_tech_debt_round_trip(self) -> None:
        record = register_tech_debt(
            RegisterTechDebtCommandV1(
                title="manual sql",
                description="bypasses ORM",
                severity=TechDebtSeverity.SEVERE,
                surface="src/db.py",
                owner="chief_engineer",
                workspace=self.workspace,
            )
        )
        listed = list_tech_debt(
            ListTechDebtQueryV1(
                workspace=self.workspace,
                surface="src/db.py",
            )
        )
        self.assertEqual([r.debt_id for r in listed], [record.debt_id])
        update_tech_debt_status(
            UpdateTechDebtStatusCommandV1(
                workspace=self.workspace,
                debt_id=record.debt_id,
                status=TechDebtStatus.SCHEDULED,
                note="sprint 14",
            )
        )
        summary = summarize_tech_debt(self.workspace)
        self.assertEqual(summary["by_status"]["scheduled"], 1)

    def test_build_blueprint_governance_pure_call(self) -> None:
        blueprint = {
            "blueprint_id": "ce_test",
            "task_id": "task-4",
            "target_files": ["a.py"],
            "acceptance_criteria": ["a"],
            "recommendations": ["r1", "r2"],
        }
        summary = build_blueprint_governance(self.workspace, "ce_test", blueprint)
        self.assertEqual(summary.blueprint_id, "ce_test")
        self.assertTrue(summary.quality_gate.passed)
        self.assertEqual(summary.risk_summary["total"], 0)
        self.assertTrue(summary.rollback.enabled)

    def test_get_blueprint_governance_reflects_resolved_risk(self) -> None:
        # Register an open blocker risk, generate a blueprint (gate blocks),
        # then resolve the risk and re-read governance: the gate should pass
        # without regenerating the blueprint.
        risk = register_risk(
            RegisterRiskCommandV1(
                task_id="task-9",
                title="data loss",
                severity=RiskSeverity.BLOCKER,
                owner="chief_engineer",
                mitigation="dual-write",
                workspace=self.workspace,
            )
        )
        result = generate_task_blueprint(
            GenerateTaskBlueprintCommandV1(
                task_id="task-9",
                workspace=self.workspace,
                objective="ship",
                context={
                    "task_title": "T9",
                    "acceptance_criteria": ["a"],
                    "execution_checklist": ["x"],
                    "target_files": ["a.py"],
                },
            )
        )
        blueprint_id = result.blueprint_id or ""
        gov_before = get_blueprint_governance(self.workspace, blueprint_id)
        assert gov_before is not None
        self.assertFalse(gov_before.quality_gate.passed)

        update_risk_status(
            UpdateRiskStatusCommandV1(
                workspace=self.workspace,
                risk_id=risk.risk_id,
                status=RiskStatus.RESOLVED,
                note="migrated safely",
            )
        )
        gov_after = get_blueprint_governance(self.workspace, blueprint_id)
        assert gov_after is not None
        self.assertTrue(gov_after.quality_gate.passed)

    def test_get_blueprint_governance_missing_returns_none(self) -> None:
        self.assertIsNone(get_blueprint_governance(self.workspace, "ce_does_not_exist"))

    def test_handoff_enforcement_flag_parsing(self) -> None:
        import os

        from polaris.cells.chief_engineer.blueprint.internal.handoff import (
            handoff_enforcement_enabled,
        )

        flag = "KERNELONE_CE_HANDOFF_ENFORCEMENT"
        original = os.environ.get(flag)
        try:
            for truthy in ("1", "true", "TRUE", "yes", "on", "  On  "):
                os.environ[flag] = truthy
                self.assertTrue(handoff_enforcement_enabled(), truthy)
            for falsy in ("0", "false", "no", "off", "", "maybe"):
                os.environ[flag] = falsy
                self.assertFalse(handoff_enforcement_enabled(), falsy)
            # Default OFF when unset.
            os.environ.pop(flag, None)
            self.assertFalse(handoff_enforcement_enabled())
        finally:
            if original is None:
                os.environ.pop(flag, None)
            else:
                os.environ[flag] = original

    def test_handoff_decision_allows_clean_blueprint(self) -> None:
        decision = evaluate_handoff_decision(
            self.workspace,
            blueprint={
                "blueprint_id": "ce_ok",
                "task_id": "task-ok",
                "target_files": ["a.py"],
                "acceptance_criteria": ["a"],
            },
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "handoff_ready")
        self.assertEqual(decision.blocker_count, 0)

    def test_strict_ce_handoff_decision_requires_hash_bindings(self) -> None:
        decision = build_ce_handoff_decision(
            self.workspace,
            blueprint={
                "blueprint_id": "ce_strict_missing",
                "task_id": "task-strict",
                "target_files": ["a.py"],
                "acceptance_criteria": ["a"],
            },
        )
        payload = decision.to_dict()
        self.assertEqual(payload["schema_version"], "polaris.ce_handoff_decision.v1")
        self.assertFalse(payload["allowed"])
        self.assertTrue(
            any("execution_profile_hash" in blocker for blocker in payload["blockers"]),
            payload["blockers"],
        )
        self.assertTrue(payload["decision_hash"])

    def test_strict_ce_handoff_decision_allows_complete_bindings(self) -> None:
        decision = build_ce_handoff_decision(
            self.workspace,
            blueprint={
                "blueprint_id": "ce_strict_ok",
                "task_id": "task-strict-ok",
                "target_files": ["a.py"],
                "acceptance_criteria": ["a"],
                "pm_contract_hash": "contract-hash",
                "blueprint_hash": "blueprint-hash",
                "execution_profile_hash": "profile-hash",
            },
        )
        payload = decision.to_dict()
        self.assertTrue(payload["allowed"])
        self.assertEqual(payload["bindings"]["pm_contract_hash"], "contract-hash")
        self.assertEqual(payload["bindings"]["blueprint_hash"], "blueprint-hash")
        self.assertEqual(payload["bindings"]["execution_profile_hash"], "profile-hash")
        self.assertTrue(payload["decision_id"].startswith("ce-handoff-"))

    def test_handoff_decision_blocks_on_missing_contract(self) -> None:
        decision = evaluate_handoff_decision(
            self.workspace,
            blueprint={"blueprint_id": "ce_bad", "task_id": "task-bad", "target_files": []},
        )
        self.assertFalse(decision.allowed)
        self.assertGreaterEqual(decision.blocker_count, 1)

    def test_handoff_decision_blocks_on_open_blocker_risk(self) -> None:
        register_risk(
            RegisterRiskCommandV1(
                task_id="task-risk",
                title="data loss",
                severity=RiskSeverity.BLOCKER,
                owner="ce",
                mitigation="m",
                workspace=self.workspace,
            )
        )
        decision = evaluate_handoff_decision(
            self.workspace,
            blueprint={
                "blueprint_id": "ce_r",
                "task_id": "task-risk",
                "target_files": ["a.py"],
                "acceptance_criteria": ["a"],
            },
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.open_blocker_risk_count, 1)
        # The open blocker risk is also surfaced as a quality-gate blocker.
        self.assertGreaterEqual(decision.blocker_count, 1)
        self.assertTrue(any("risk" in b for b in decision.blockers))

    def test_assert_handoff_ready_raises_when_blocked(self) -> None:
        with self.assertRaises(ChiefEngineerBlueprintErrorV1) as ctx:
            assert_handoff_ready(
                self.workspace,
                blueprint={"blueprint_id": "ce_x", "task_id": "t", "target_files": []},
            )
        self.assertEqual(ctx.exception.code, "handoff_blocked")
        self.assertIn("blocker", ctx.exception.details.get("reason", ""))

    def test_evaluate_handoff_decision_for_blueprint_reads_persisted(self) -> None:
        result = generate_task_blueprint(
            GenerateTaskBlueprintCommandV1(
                task_id="task-hd",
                workspace=self.workspace,
                objective="ship",
                context={
                    "task_title": "HD",
                    "acceptance_criteria": ["a"],
                    "execution_checklist": ["x"],
                    "target_files": ["a.py"],
                },
            )
        )
        decision = evaluate_handoff_decision_for_blueprint(self.workspace, result.blueprint_id or "")
        assert decision is not None
        self.assertTrue(decision.allowed)
        # Missing blueprint -> fail-closed None.
        self.assertIsNone(evaluate_handoff_decision_for_blueprint(self.workspace, "ce_missing"))

    def test_validate_director_handoff_from_payload_is_shared_gate(self) -> None:
        result = generate_task_blueprint(
            GenerateTaskBlueprintCommandV1(
                task_id="task-shared-handoff",
                workspace=self.workspace,
                objective="ship",
                context={
                    "task_title": "Shared Handoff",
                    "acceptance_criteria": ["a"],
                    "execution_checklist": ["x"],
                    "target_files": ["a.py"],
                },
            )
        )
        validation = validate_director_handoff_from_payload(
            self.workspace,
            {
                "task_id": "task-shared-handoff",
                "metadata": {"chief_engineer_blueprint_id": result.blueprint_id},
            },
        )
        self.assertEqual(validation["schema_version"], "chief_engineer.director_handoff_validation.v1")
        self.assertTrue(validation["allowed"])
        self.assertTrue(validation["legacy_allowed"])
        self.assertEqual(validation["blueprint_id"], result.blueprint_id)
        self.assertEqual(validation["task_id"], "task-shared-handoff")
        self.assertIsInstance(validation["decision_payload"], dict)
        self.assertIsInstance(validation["strict_decision_payload"], dict)

    def test_validate_director_handoff_from_payload_blocks_task_mismatch(self) -> None:
        result = generate_task_blueprint(
            GenerateTaskBlueprintCommandV1(
                task_id="task-owned",
                workspace=self.workspace,
                objective="ship",
                context={
                    "task_title": "Task Owned",
                    "acceptance_criteria": ["a"],
                    "execution_checklist": ["x"],
                    "target_files": ["a.py"],
                },
            )
        )
        validation = validate_director_handoff_from_payload(
            self.workspace,
            {
                "task_id": "task-other",
                "metadata": {"chief_engineer_blueprint_id": result.blueprint_id},
            },
        )
        self.assertFalse(validation["allowed"])
        self.assertEqual(validation["blueprint_task_id"], "task-owned")
        self.assertIn("belongs to task-owned", validation["reason"])

    def test_attach_governance_writes_back(self) -> None:
        result = generate_task_blueprint(
            GenerateTaskBlueprintCommandV1(
                task_id="task-5",
                workspace=self.workspace,
                objective="ship",
                context={
                    "task_title": "T5",
                    "acceptance_criteria": ["a"],
                    "execution_checklist": ["x"],
                    "target_files": ["a.py"],
                },
            )
        )
        path = _blueprint_json_path(self.workspace, result.blueprint_id or "")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        # Force a stale view, then re-attach
        data.pop("governance", None)
        attach_governance_to_blueprint(self.workspace, result.blueprint_id or "", data)
        with open(path, encoding="utf-8") as handle:
            data2 = json.load(handle)
        self.assertIn("governance", data2)

    def test_list_risks_filters(self) -> None:
        register_risk(
            RegisterRiskCommandV1(
                task_id="a",
                title="t1",
                severity=RiskSeverity.LOW,
                owner="chief_engineer",
                mitigation="m",
                workspace=self.workspace,
            )
        )
        register_risk(
            RegisterRiskCommandV1(
                task_id="b",
                title="t2",
                severity=RiskSeverity.HIGH,
                owner="chief_engineer",
                mitigation="m",
                workspace=self.workspace,
            )
        )
        listed = list_risks(ListRisksQueryV1(workspace=self.workspace, task_id="a"))
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].task_id, "a")
        self.assertEqual(summarize_risks(self.workspace)["total"], 2)

    def test_get_blueprint_status_unchanged(self) -> None:
        generate_task_blueprint(
            GenerateTaskBlueprintCommandV1(
                task_id="task-6",
                workspace=self.workspace,
                objective="x",
                context={
                    "task_title": "T6",
                    "acceptance_criteria": ["a"],
                    "execution_checklist": ["x"],
                    "target_files": ["a.py"],
                },
            )
        )
        from polaris.cells.chief_engineer.blueprint.public.contracts import (
            GetBlueprintStatusQueryV1,
        )

        status = get_blueprint_status(GetBlueprintStatusQueryV1(task_id="task-6", workspace=self.workspace))
        self.assertTrue(status.ok)
        self.assertEqual(status.status, "generated")


if __name__ == "__main__":
    unittest.main()
