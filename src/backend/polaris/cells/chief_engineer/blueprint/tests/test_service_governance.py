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
    attach_governance_to_blueprint,
    build_blueprint_governance,
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
        self.assertIn(
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
