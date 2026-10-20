"""Read-only planner feasibility never supplies task ownership authority."""

from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
    workspace_quality_summary_requires_task_boundary_triage,
)


def test_named_unplannable_targets_do_not_require_synthetic_triage() -> None:
    assert (
        workspace_quality_summary_requires_task_boundary_triage(
            {
                "stage": "runtime_plan_probe_unplannable",
                "repair_target_files": ["src/component.ts"],
                "plan_probe_preaudit": {"status": "coverage_matched_but_unplannable", "plannable_source_tools": []},
            }
        )
        is False
    )


def test_retired_retry_annotation_does_not_change_feasibility_semantics() -> None:
    assert (
        workspace_quality_summary_requires_task_boundary_triage(
            {
                "stage": "runtime_plan_probe_unplannable",
                "repair_target_files": ["src/component.ts"],
                "task_boundary_interface_discrepancy_retry_authorized": True,
            }
        )
        is False
    )


def test_ordinary_owned_diagnostic_keeps_director_local_fallback() -> None:
    assert (
        workspace_quality_summary_requires_task_boundary_triage(
            {
                "stage": "quality_repair",
                "repair_target_files": ["src/component.ts"],
            }
        )
        is False
    )
