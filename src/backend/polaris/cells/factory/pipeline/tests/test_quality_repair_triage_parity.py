"""Owned target inventory cannot substitute for explicit local repair routing."""

from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
    workspace_quality_summary_requires_task_boundary_triage,
)


def test_unauthed_named_unplannable_targets_require_existing_triage() -> None:
    assert (
        workspace_quality_summary_requires_task_boundary_triage(
            {
                "stage": "runtime_plan_probe_unplannable",
                "repair_target_files": ["src/component.ts"],
                "plan_probe_preaudit": {"status": "coverage_matched_but_unplannable", "plannable_source_tools": []},
            }
        )
        is True
    )


def test_explicit_local_retry_authorization_does_not_reenter_triage() -> None:
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
