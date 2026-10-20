"""Factory feasibility projections cannot mint CE/interface authority facts."""

from __future__ import annotations

from typing import Any

from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
    workspace_quality_interface_discrepancy_evidence,
    workspace_quality_repair_summary_projection,
    workspace_quality_summary_requires_task_boundary_triage,
)


def _summary() -> dict[str, Any]:
    return {
        "stage": "runtime_plan_probe_unplannable",
        "repair_target_files": ["src/value.ts"],
        "plan_probe_preaudit": {
            "status": "coverage_matched_but_unplannable",
            "plannable_source_tools": [],
            "covered_unplannable_diagnostic_count": 1,
            "covered_unplannable_source_tools": ["private_registered_rule"],
            "covered_unplannable_diagnostics": [{"path": "src/value.ts", "message": "member unavailable"}],
        },
    }


def test_readonly_probe_never_becomes_factory_permission_or_ce_triage() -> None:
    summary = _summary()
    assert workspace_quality_summary_requires_task_boundary_triage(summary) is False
    assert workspace_quality_interface_discrepancy_evidence(summary) == {}
    projected = workspace_quality_repair_summary_projection(summary)
    assert projected["plan_probe_preaudit"] == summary["plan_probe_preaudit"]
    assert projected.get("task_boundary_triage_required") is False
    assert not projected.get("interface_discrepancy_evidence")


def test_real_scope_discrepancy_remains_distinct_from_feasibility() -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate._scope_scan import (
        _semantic_exporter_scope_discrepancy_evidence,
    )

    scope = {
        "deferred": True,
        "out_of_scope_repair_target_files": ["src/provider.ts"],
        "task_declared_write_targets": ["tests/consumer.ts"],
    }
    evidence = _semantic_exporter_scope_discrepancy_evidence(
        task={"id": "TASK-1", "target_files": ["tests/consumer.ts"]},
        semantic_exporter_targets=["src/provider.ts"],
        repair_target_files=["tests/consumer.ts"],
        artifact_quality_errors=["Actual exporter belongs to another task"],
        task_scope_filter_evidence=scope,
    )
    summary = {
        "repair_target_files": ["tests/consumer.ts"],
        "task_boundary_scope_filter": scope,
        "interface_discrepancy_evidence": evidence,
    }
    assert workspace_quality_summary_requires_task_boundary_triage(summary) is True
    assert workspace_quality_interface_discrepancy_evidence(summary) == evidence
    projected = workspace_quality_repair_summary_projection(summary)
    assert projected["triage_stage"] == "task_boundary_scope_conflict"
    assert evidence["llm_fallback_blocked"] is True
    assert evidence["reason"] == "semantic_exporter_owner_outside_current_task_scope"
    # A merely deferred foreign diagnostic does not block the owned subset.
    assert (
        workspace_quality_summary_requires_task_boundary_triage(
            {"repair_target_files": ["tests/consumer.ts"], "task_boundary_scope_filter": scope}
        )
        is False
    )
