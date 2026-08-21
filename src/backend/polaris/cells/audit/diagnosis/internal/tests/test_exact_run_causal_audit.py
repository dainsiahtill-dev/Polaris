"""Regression tests for exact-run causal attribution."""

from __future__ import annotations

from pathlib import Path

import jsonschema
import yaml
from polaris.cells.audit.diagnosis.internal.exact_run_causal_audit import (
    build_exact_run_causal_report,
)


def _factory(*, status: str = "failed", failed_stages: list[str] | None = None) -> dict[str, object]:
    return {
        "available": True,
        "status": status,
        "failed_stages": failed_stages or [],
        "chain_completed": status == "completed",
    }


def _ledger(
    *,
    ok: bool,
    integrity_ok: bool = True,
    outcome_ok: bool = True,
    revision_issues: list[str] | None = None,
    failed_required: list[str] | None = None,
    missing_required: list[str] | None = None,
    task_boundary_ok: bool = True,
    tool_lifecycle_ok: bool = True,
    tool_failure_evidence: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "available": True,
        "ok": ok,
        "status": "completed" if ok else "failed",
        "failed_control_plane_events": [],
        "run_projection": {
            "integrity_ok": integrity_ok,
            "outcome_ok": outcome_ok,
            "historical_failed_gate_count": 7,
            "gate_revisions": {"issues": revision_issues or [], "resolved_forks": []},
        },
        "evidence_policy": {
            "failed_required_modalities": failed_required or [],
            "missing_required_modalities": missing_required or [],
        },
        "evidence_modalities": {
            "qa": {"ok": True, "total": 2, "failed": 0},
            "command": {"ok": not bool(failed_required), "total": 2},
        },
        "task_boundary": {
            "ok": task_boundary_ok,
            "historical_failed_count": 3,
            "latest_by_task": {"TASK-1": {"status": "completed_verified"}},
        },
        "tool_lifecycle": {
            "ok": tool_lifecycle_ok,
            "effect_receipt_count": 2,
            "failed_count": 5,
            "failure_evidence": tool_failure_evidence or [],
        },
    }


def test_gate_revision_fork_is_primary_even_when_physical_delivery_is_green() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-1",
        project_id="L3-21",
        factory_projection=_factory(failed_stages=["quality_gate"]),
        ledger_projection=_ledger(
            ok=False,
            integrity_ok=False,
            outcome_ok=False,
            revision_issues=["gate_revision_chain_fork_or_stale:218"],
        ),
        terminal_task_runtime_projection=None,
    )

    assert report["root_cause_code"] == "control_plane.run_ledger.gate_revision_fork_after_runtime_reentry"
    assert report["responsible_cell"] == "control_plane.run_ledger"
    assert report["retry_boundary"] == "same_run_quality_gate_only"
    assert report["pm_ce_restart_allowed"] is False
    assert report["target_project_defect"] is False
    assert report["authority_contradiction_detected"] is True


def test_historical_failures_do_not_override_current_delivery_verified() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-2",
        project_id="L3-21",
        factory_projection=_factory(status="completed"),
        ledger_projection=_ledger(ok=True),
        terminal_task_runtime_projection=None,
    )

    assert report["current_status"] == "DELIVERY_VERIFIED"
    assert report["project_id"] == "L3-21"
    assert report["root_cause_code"] == ""
    assert report["historical_error_count"] == 10


def test_real_failed_verifier_is_target_defect_and_retries_only_that_verifier() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-3",
        project_id="L3-21",
        factory_projection=_factory(failed_stages=["quality_gate"]),
        ledger_projection=_ledger(ok=False, outcome_ok=False, failed_required=["command"]),
        terminal_task_runtime_projection=None,
    )

    assert report["root_cause_code"] == "director.runtime.generated_project_verifier_failed"
    assert report["responsible_cell"] == "director.runtime"
    assert report["retry_boundary"] == "same_failed_verifier_only"
    assert report["target_project_defect"] is True
    assert report["next_action"]["preserve"] == {
        "pm": True,
        "chief_engineer": True,
        "completed_director_artifacts": True,
    }
    assert "restart_pm" in report["next_action"]["prohibited_actions"]
    assert report["platform_residual_attribution"]["primary_module_id"] == "M09_four_pillars_gates"


def test_failed_verifier_with_plannable_repair_routes_only_same_director_task() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-plannable-repair",
        project_id="L1-01",
        factory_projection=_factory(failed_stages=["quality_gate"]),
        ledger_projection=_ledger(ok=False, outcome_ok=False, failed_required=["command"]),
        terminal_task_runtime_projection=None,
        repair_evidence={
            "residual_errors": ["src/main.ts(4,3): error TS4104: readonly value cannot be assigned"],
            "director_runtime_repair_coverage": {
                "total_diagnostics": 1,
                "covered_diagnostic_count": 1,
                "uncovered_diagnostic_count": 0,
            },
            "plan_probe_preaudit": {
                "status": "covered_plannable",
                "plannable_source_tools": ["deterministic_typescript_readonly_assignment_repair"],
                "items": [
                    {
                        "status": "covered_plannable",
                        "changed_paths": ["src/main.ts"],
                    }
                ],
            },
        },
    )

    assert report["root_cause_code"] == "director.runtime.deterministic_repair_available"
    assert report["retry_boundary"] == "same_director_task_repair_only"
    assert report["pm_ce_restart_allowed"] is False
    assert report["next_action"]["repair"]["changed_paths"] == ["src/main.ts"]
    assert report["next_action"]["preserve"]["completed_director_artifacts"] is True


def test_coverage_match_without_patch_is_not_misreported_as_repairable() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-covered-unplannable",
        project_id="L1-01",
        factory_projection=_factory(failed_stages=["quality_gate"]),
        ledger_projection=_ledger(ok=False, outcome_ok=False, failed_required=["command"]),
        terminal_task_runtime_projection=None,
        repair_evidence={
            "residual_errors": [
                "src/render/gardenCanvas.ts(96,7): error TS2322: Type 'Timeout' is not assignable to type 'number'."
            ],
            "director_runtime_repair_coverage": {
                "total_diagnostics": 1,
                "covered_diagnostic_count": 1,
                "uncovered_diagnostic_count": 0,
                "items": [
                    {
                        "diagnostic": {
                            "path": "src/render/gardenCanvas.ts",
                            "code": "typescript_ts2322",
                        }
                    }
                ],
            },
            "plan_probe_preaudit": {
                "status": "coverage_matched_but_unplannable",
                "plannable_source_tools": [],
                "covered_unplannable_source_tools": ["deterministic_typescript_strict_null_repair"],
                "items": [],
            },
        },
    )

    assert report["root_cause_code"] == "director.runtime.repair_coverage_matched_but_unplannable"
    assert report["retry_boundary"] == "same_director_task_repair_only"
    assert report["repair_diagnosis"]["coverage_is_not_planning"] is True
    assert report["repair_diagnosis"]["plannable_source_tools"] == []
    assert report["next_action"]["suspected_files"] == ["src/render/gardenCanvas.ts"]
    assert "restart_pm" in report["next_action"]["prohibited_actions"]
    assert "restart_chief_engineer" in report["next_action"]["prohibited_actions"]


def test_uncovered_verifier_diagnostic_is_a_governed_repair_coverage_gap() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-repair-gap",
        project_id="L1-01",
        factory_projection=_factory(failed_stages=["quality_gate"]),
        ledger_projection=_ledger(ok=False, outcome_ok=False, failed_required=["command"]),
        terminal_task_runtime_projection=None,
        repair_evidence={
            "residual_errors": ["src/main.ts(1,1): error TS9999: unknown generic diagnostic"],
            "director_runtime_repair_coverage": {
                "total_diagnostics": 1,
                "covered_diagnostic_count": 0,
                "uncovered_diagnostic_count": 1,
            },
            "plan_probe_preaudit": {
                "status": "coverage_gap",
                "coverage_gap_count": 1,
                "plannable_source_tools": [],
            },
        },
    )

    assert report["root_cause_code"] == "director.runtime.repair_coverage_gap"
    assert report["responsible_cell"] == "director.runtime"
    assert report["retry_boundary"] == "same_director_task_repair_only"
    assert report["target_project_defect"] is True


def test_role_failure_requires_final_provider_request_evidence() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-4",
        project_id="L3-21",
        factory_projection=_factory(failed_stages=["director_dispatch"]),
        ledger_projection=_ledger(ok=True),
        terminal_task_runtime_projection=None,
    )

    assert report["root_cause_code"] == "context.engine.final_provider_request_evidence_missing"
    assert report["responsible_cell"] == "context.engine"
    assert report["retry_boundary"] == "collect_same_run_final_request_evidence_only"
    assert report["evidence_gaps"] == ["final_provider_request_unavailable_for_role_or_tool_failure"]
    assert report["root_cause_candidates"][1]["root_cause_code"] == "director.runtime.stage_failed"


def test_control_plane_failure_preserves_physical_director_artifacts() -> None:
    ledger = _ledger(ok=False)
    ledger["failed_control_plane_events"] = ["factory-director-mat-settle:factory-run:TASK-1"]
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-control-plane-reconcile",
        project_id="L1-01",
        factory_projection=_factory(failed_stages=["director_dispatch"]),
        ledger_projection=ledger,
        terminal_task_runtime_projection=None,
    )

    assert report["root_cause_code"] == "control_plane.run_ledger.failed_control_plane_event"
    assert report["retry_boundary"] == "same_run_control_plane_reconcile_only"
    assert report["next_action"]["preserve"] == {
        "pm": True,
        "chief_engineer": True,
        "completed_director_artifacts": True,
    }
    assert "restart_completed_director_tasks" in report["next_action"]["prohibited_actions"]


def test_missing_entrypoint_boundary_is_precise_and_reprojects_without_director_restart() -> None:
    ledger = _ledger(ok=False)
    ledger["failed_control_plane_events"] = ["MISSING_ENTRYPOINT_TARGET"]
    ledger["task_boundary"] = {
        "ok": False,
        "historical_failed_count": 1,
        "latest": {
            "failure_class": "MISSING_ENTRYPOINT_TARGET",
            "missing_entrypoint_targets": ["src/index.js"],
            "task_id": "TASK-3",
        },
        "failed": [{"task_id": "TASK-3", "failure_class": "MISSING_ENTRYPOINT_TARGET"}],
    }
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-entrypoint-boundary",
        project_id="L1-01",
        factory_projection=_factory(failed_stages=["director_dispatch"]),
        ledger_projection=ledger,
        terminal_task_runtime_projection=None,
    )

    assert report["root_cause_code"] == "control_plane.run_ledger.task_boundary_missing_entrypoint_target"
    assert report["retry_boundary"] == "same_run_task_boundary_reproject_only"
    assert report["root_cause_candidates"][0]["evidence_refs"] == ["src/index.js"]
    assert report["next_action"]["preserve"]["completed_director_artifacts"] is True
    assert report["next_action"]["failed_task_ids"] == ["TASK-3"]
    assert report["platform_residual_attribution"]["primary_module_id"] == "M06_director_multi_task"


def test_role_failure_rejects_snapshot_from_wrong_role() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-ce-role-mismatch",
        project_id="L3-21",
        factory_projection=_factory(failed_stages=["chief_engineer_review"]),
        ledger_projection=_ledger(ok=True),
        terminal_task_runtime_projection=None,
        provider_request_audits=[
            {
                "ok": True,
                "role": "director",
                "context_snapshot_ref": "ec851e95f353eb000dab334b",
                "tool_names": ["read_file"],
            }
        ],
    )

    assert report["root_cause_code"] == "context.engine.final_provider_request_evidence_missing"
    details = report["evidence_chain"]["final_provider_request"]["details"]
    assert details["required_role"] == "chief_engineer"
    assert details["required_role_available"] is False


def test_tool_failure_projects_typed_class_and_suspected_files_into_next_action() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-tool-failure",
        project_id="L3-21",
        factory_projection=_factory(failed_stages=["director_dispatch"]),
        ledger_projection=_ledger(
            ok=False,
            tool_lifecycle_ok=False,
            tool_failure_evidence=[
                {
                    "failure_class": "TOOL_RESULT_FAILED",
                    "reason": "edit_file returned no effect",
                    "suspected_files": ["src/main.ts"],
                }
            ],
        ),
        terminal_task_runtime_projection=None,
        provider_request_audits=[
            {
                "ok": True,
                "role": "director",
                "context_snapshot_ref": "ec851e95f353eb000dab334b",
                "tool_names": ["edit_file"],
            }
        ],
    )

    assert report["root_cause_code"] == "roles.kernel.tool_lifecycle_incomplete"
    assert report["evidence_chain"]["failure_evidence"]["details"] == {
        "row_count": 1,
        "failure_classes": ["TOOL_RESULT_FAILED"],
        "suspected_files": ["src/main.ts"],
    }
    assert report["next_action"]["suspected_files"] == ["src/main.ts"]
    assert report["platform_residual_attribution"]["primary_module_id"] == "M03_tool_batch_deo"


def test_structured_provider_timeout_attributes_provider_runtime_with_same_stage_retry() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-ce-timeout",
        project_id="L3-21",
        factory_projection=_factory(failed_stages=["chief_engineer_review"]),
        ledger_projection=_ledger(ok=True),
        terminal_task_runtime_projection=None,
        provider_request_audits=[
            {
                "ok": True,
                "role": "chief_engineer",
                "context_snapshot_ref": "ec851e95f353eb000dab334b",
                "tool_names": ["read_file"],
            }
        ],
        structured_failure_signals=[
            {
                "error_code": "provider_stream_timeout",
                "role": "chief_engineer",
                "stage": "chief_engineer_review",
                "task_id": "TASK-2",
                "context_snapshot_ref": "ec851e95f353eb000dab334b",
            }
        ],
    )

    assert report["root_cause_code"] == "provider_stream_timeout"
    assert report["responsible_cell"] == "llm.provider_runtime"
    assert report["retry_boundary"] == "same_ce_stage"
    assert report["pm_restart_allowed"] is False
    assert report["ce_restart_allowed"] is True


def test_broken_final_request_context_outranks_provider_timeout() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-ce-broken-context",
        project_id="L3-21",
        factory_projection=_factory(failed_stages=["chief_engineer_review"]),
        ledger_projection=_ledger(ok=True),
        terminal_task_runtime_projection=None,
        provider_request_audits=[
            {
                "ok": True,
                "role": "chief_engineer",
                "context_snapshot_ref": "ec851e95f353eb000dab334b",
                "tool_names": [],
                "evidence_coverage_pass": False,
                "role_identity_ok": True,
                "missing_required_refs": ["pm_contract"],
                "missing_required_tools": ["read_file"],
            }
        ],
        structured_failure_signals=[
            {
                "error_code": "provider_stream_timeout",
                "role": "chief_engineer",
                "stage": "chief_engineer_review",
                "context_snapshot_ref": "ec851e95f353eb000dab334b",
            }
        ],
    )

    assert report["root_cause_code"] == "context.engine.final_provider_request_context_invalid"
    assert report["responsible_cell"] == "context.engine"
    assert report["retry_boundary"] == "same_ce_stage"
    role_context = report["evidence_chain"]["role_context_coverage"]["details"]["roles"]
    assert role_context["chief_engineer"]["missing_required_refs"] == ["pm_contract"]
    assert role_context["chief_engineer"]["missing_required_tools"] == ["read_file"]


def test_running_factory_projection_is_not_synthesized_as_terminal_failure() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-running",
        project_id="L3-22",
        factory_projection=_factory(status="running"),
        ledger_projection=_ledger(ok=False, outcome_ok=False, failed_required=["command"]),
        terminal_task_runtime_projection=None,
    )

    assert report["current_status"] == "RUNNING"
    assert report["terminal"] is False
    assert report["root_cause_code"] == ""
    assert report["target_project_defect"] is False


def test_impossible_edit_only_file_deficit_is_contract_scope_contradiction() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-depth",
        project_id="L3-22",
        factory_projection=_factory(failed_stages=["quality_gate"]),
        ledger_projection=_ledger(ok=False, outcome_ok=False, failed_required=["command"]),
        terminal_task_runtime_projection=None,
        provider_request_audits=[
            {
                "ok": True,
                "role": "director",
                "context_snapshot_ref": "ec851e95f353eb000dab334b",
                "tool_names": ["edit_file"],
                "target_scope_summary": {"allowed_write_path_count": 4},
                "file_deficits": [
                    {"metric": "prod_files", "actual": 3, "required": 7},
                    {"metric": "test_files", "actual": 1, "required": 2},
                ],
            }
        ],
    )

    assert report["root_cause_code"] == "director.tasking.delivery_contract_scope_contradiction"
    assert report["responsible_cell"] == "director.tasking"
    assert report["retry_boundary"] == "same_contract_projection_only"
    assert report["pm_ce_restart_allowed"] is False
    assert report["target_project_defect"] is False
    assert report["authority_contradiction_detected"] is True
    final_request = report["evidence_chain"]["final_provider_request"]
    assert final_request["details"]["context_snapshot_refs"] == ["ec851e95f353eb000dab334b"]
    assert final_request["details"]["offered_tools"] == ["edit_file"]


def test_infeasible_ce_authority_outranks_downstream_director_symptom() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-depth",
        project_id="L3-22",
        factory_projection=_factory(failed_stages=["quality_gate"]),
        ledger_projection=_ledger(ok=False, outcome_ok=False, failed_required=["command"]),
        terminal_task_runtime_projection=None,
        chief_engineer_authority_feasibility={
            "available": True,
            "ok": False,
            "actual": {"prod_files": 3, "test_files": 1},
            "minimums": {"min_prod_files": 7, "min_test_files": 2},
            "deficits": [
                {"metric": "prod_files", "actual": 3, "required": 7, "deficit": 4},
                {"metric": "test_files", "actual": 1, "required": 2, "deficit": 1},
            ],
        },
        provider_request_audits=[
            {
                "ok": True,
                "role": "director",
                "context_snapshot_ref": "ec851e95f353eb000dab334b",
                "tool_names": ["edit_file"],
                "file_deficits": [{"metric": "test_files", "actual": 1, "required": 2}],
            }
        ],
    )

    assert report["root_cause_code"] == ("chief_engineer.blueprint.delivery_depth_completion_contract_infeasible")
    assert report["responsible_cell"] == "chief_engineer.blueprint"
    assert report["retry_boundary"] == "same_ce_stage"
    assert report["pm_restart_allowed"] is False
    assert report["ce_restart_allowed"] is True
    assert report["target_project_defect"] is False
    assert report["evidence_completeness"]["complete"] is True
    assert report["evidence_completeness"]["required_links"] == [
        "factory_terminal",
        "run_ledger",
        "chief_engineer_authority_feasibility",
    ]
    assert report["next_action"]["failed_verifier_modalities"] == []


def test_file_deficit_with_write_file_remains_normal_failed_verifier() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-repairable-depth",
        project_id="L3-22",
        factory_projection=_factory(failed_stages=["quality_gate"]),
        ledger_projection=_ledger(ok=False, outcome_ok=False, failed_required=["command"]),
        terminal_task_runtime_projection=None,
        provider_request_audits=[
            {
                "ok": True,
                "role": "director",
                "context_snapshot_ref": "ec851e95f353eb000dab334b",
                "tool_names": ["edit_file", "write_file"],
                "file_deficits": [{"metric": "prod_files", "actual": 3, "required": 7}],
            }
        ],
    )

    assert report["root_cause_code"] == "director.runtime.generated_project_verifier_failed"
    assert report["target_project_defect"] is True


def test_report_has_stable_diagnosis_id_and_schema_validates_for_running_and_failed() -> None:
    schema_path = (
        Path(__file__).resolve().parents[6] / "docs" / "governance" / "schemas" / "exact-run-causal-audit.schema.yaml"
    )
    schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    running = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-running-schema",
        project_id="L3-22",
        factory_projection=_factory(status="running"),
        ledger_projection=_ledger(ok=False),
        terminal_task_runtime_projection=None,
    )
    failed = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-failed-schema",
        project_id="L3-22",
        factory_projection=_factory(failed_stages=["quality_gate"]),
        ledger_projection=_ledger(ok=False, outcome_ok=False, failed_required=["command"]),
        terminal_task_runtime_projection=None,
    )

    jsonschema.validate(running, schema)
    jsonschema.validate(failed, schema)
    repeated = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-failed-schema",
        project_id="L3-22",
        factory_projection=_factory(failed_stages=["quality_gate"]),
        ledger_projection=_ledger(ok=False, outcome_ok=False, failed_required=["command"]),
        terminal_task_runtime_projection=None,
    )
    assert failed["diagnosis_id"] == repeated["diagnosis_id"]
    assert len(failed["diagnosis_id"]) == 24
