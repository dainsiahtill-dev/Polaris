"""Tests for residual → module_id attribution and unattended step planning."""

from __future__ import annotations

import unittest
from pathlib import Path

from polaris.kernelone.platform_modules.residual_attribution import (
    attribute_factory_audit_record,
    attribute_residual,
    build_factory_audits_attribution_pack,
    classify_delivery_status,
)
from polaris.kernelone.platform_modules.unattended_supervisor import plan_unattended_step


class TestResidualAttribution(unittest.TestCase):
    def test_r181_real_run_green_boundary_forces_m06(self) -> None:
        attr = attribute_residual(
            root_cause_signature="control_plane:task_runtime_not_completed",
            failure_category="control_plane",
            failure_reasons=["gate:canonical_execution=task_runtime_not_completed"],
            error_code="director.canonical_task_boundary_missing",
            director_detail="One or more TaskRuntime rows are pending, blocked, active, or failed",
            real_run_gate_ok=True,
            chain_ok=False,
            tsc_clean=True,
            m10_coverage_gap_count=0,
        )
        self.assertEqual(attr.primary_module_id, "M06_director_multi_task")
        self.assertEqual(attr.delivery_status, "DELIVERY_VERIFIED_CHAIN_CONTROL_PLANE_FAIL")
        self.assertFalse(attr.is_model_ceiling)
        self.assertIn("M10_materialization_semantic_quality", attr.forbidden_same_round)
        self.assertTrue(attr.gate_commands[0].endswith("M06_director_multi_task"))

    def test_deo_edit_blocks_maps_m03_before_m10(self) -> None:
        attr = attribute_residual(
            root_cause_signature="control_plane:deo_director_policy_denied",
            error_code="deo_director_policy_denied",
            director_detail="edit_blocks denied by directed effect policy",
            real_run_gate_ok=False,
            chain_ok=False,
        )
        self.assertEqual(attr.primary_module_id, "M03_tool_batch_deo")

    def test_tsc_only_residual_maps_m10(self) -> None:
        attr = attribute_residual(
            failure_reasons=["src/main.ts(1,1): error TS2307: Cannot find module './verify.js'"],
            tsc_clean=False,
            real_run_gate_ok=False,
            chain_ok=False,
        )
        self.assertEqual(attr.primary_module_id, "M10_materialization_semantic_quality")

    def test_run_ledger_projection_missing_maps_m08_before_m09(self) -> None:
        attr = attribute_residual(
            root_cause_signature="control_plane:run_ledger_projection_missing",
            failure_category="control_plane",
            failure_reasons=["real_run_gate.build_test_lint", "entrypoint_smoke"],
            real_run_gate_ok=False,
            chain_ok=False,
        )
        self.assertEqual(attr.primary_module_id, "M08_run_ledger_tool_lifecycle")

    def test_factory_audit_record_shape(self) -> None:
        record = {
            "project_id": "L1-01",
            "factory_run_id": "factory_x",
            "root_cause_signature": "control_plane:task_runtime_not_completed",
            "failure_category": "control_plane",
            "failure_reasons": ["gate:canonical_execution=task_runtime_not_completed"],
            "real_run_gate": {"ok": True},
            "chain_state": "fail",
            "chain": {
                "exit_code": 1,
                "factory_terminal_status": {
                    "roles": {
                        "director": {
                            "status": "failed",
                            "detail": "error_code=director.canonical_task_boundary_missing",
                        }
                    }
                },
            },
            "director_repair_coverage_gap_summary": {"coverage_gap_count": 0},
        }
        attr = attribute_factory_audit_record(record)
        self.assertEqual(attr.primary_module_id, "M06_director_multi_task")
        self.assertEqual(attr.delivery_status, "DELIVERY_VERIFIED_CHAIN_CONTROL_PLANE_FAIL")

    def test_delivery_status_classes(self) -> None:
        self.assertEqual(
            classify_delivery_status(real_run_gate_ok=True, chain_ok=True),
            "DELIVERY_AND_CHAIN_VERIFIED",
        )
        self.assertEqual(
            classify_delivery_status(
                real_run_gate_ok=True,
                chain_ok=False,
                control_plane_signature="control_plane:task_runtime_not_completed",
            ),
            "DELIVERY_VERIFIED_CHAIN_CONTROL_PLANE_FAIL",
        )


class TestUnattendedSupervisor(unittest.TestCase):
    def test_module_gate_before_cascade_before_bench(self) -> None:
        attr = attribute_residual(
            root_cause_signature="control_plane:task_runtime_not_completed",
            error_code="director.canonical_task_boundary_missing",
            real_run_gate_ok=True,
            chain_ok=False,
        )
        step0 = plan_unattended_step(attribution=attr, module_gate_ok=None, cascade_ok=None)
        self.assertEqual(step0.phase, "module_gate")
        self.assertFalse(step0.allow_l1_01_bench)

        step1 = plan_unattended_step(attribution=attr, module_gate_ok=True, cascade_ok=False)
        self.assertEqual(step1.phase, "cascade")
        self.assertFalse(step1.allow_l1_01_bench)

        step2 = plan_unattended_step(attribution=attr, module_gate_ok=True, cascade_ok=True)
        self.assertEqual(step2.phase, "isolated_l1_01")
        self.assertTrue(step2.allow_l1_01_bench)
        self.assertFalse(step2.allow_l1_02)

    def test_model_ceiling_stops(self) -> None:
        attr = attribute_residual(
            director_detail="tools_executed=0 continuous",
            failure_reasons=["no_tool_calls"],
        )
        step = plan_unattended_step(attribution=attr, module_gate_ok=True, cascade_ok=True)
        self.assertTrue(step.stop)
        self.assertEqual(step.phase, "model_ceiling")

    def test_n_batch_unlocks_l1_02_consideration(self) -> None:
        attr = attribute_residual(root_cause_signature="pass")
        step = plan_unattended_step(
            attribution=attr,
            module_gate_ok=True,
            cascade_ok=True,
            last_l1_01_four_pillars_ok=True,
            n_batch_streak=3,
        )
        self.assertTrue(step.allow_l1_02)
        self.assertEqual(step.phase, "l1_01_n_batch_ready")


class TestAttributeCliImport(unittest.TestCase):
    def test_attribute_script_exists(self) -> None:
        # Path: platform_modules/tests -> platform_modules -> kernelone -> polaris -> backend
        script = Path(__file__).resolve().parents[4] / "scripts" / "platform_modules" / "attribute_factory_audit.py"
        self.assertTrue(script.is_file(), msg=str(script))
        text = script.read_text(encoding="utf-8")
        self.assertIn("attribute_factory_audits_file", text)

    def test_pack_prefers_failed_primary(self) -> None:
        pack = build_factory_audits_attribution_pack(
            {
                "goal_audit": {},
                "records": [
                    {
                        "project_id": "L1-01-pass",
                        "all_checks_passed": True,
                        "root_cause_signature": "pass",
                        "real_run_gate": {"ok": True},
                        "chain_state": "pass",
                        "chain": {"exit_code": 0},
                    },
                    {
                        "project_id": "L1-01-fail",
                        "all_checks_passed": False,
                        "root_cause_signature": "control_plane:task_runtime_not_completed",
                        "failure_category": "control_plane",
                        "failure_reasons": ["canonical_task_boundary_missing"],
                        "real_run_gate": {"ok": True},
                        "chain_state": "fail",
                        "chain": {
                            "exit_code": 1,
                            "factory_terminal_status": {
                                "failure": {"code": "director.canonical_task_boundary_missing"},
                            },
                        },
                    },
                ],
            }
        )
        self.assertEqual(pack["failed_record_count"], 1)
        self.assertEqual(pack["primary"]["primary_module_id"], "M06_director_multi_task")
        self.assertEqual(pack["primary"]["delivery_status"], "DELIVERY_VERIFIED_CHAIN_CONTROL_PLANE_FAIL")


if __name__ == "__main__":
    unittest.main()
