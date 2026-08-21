"""Structured delivery-depth evidence for Director repair and audit."""

from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.control_plane.run_ledger.public import summarize_failed_gate_evidence_context_slot
from polaris.cells.roles.adapters.internal.director.quality_gate import (
    _build_materialization_quality_failure_evidence_context,
    _build_materialization_quality_workspace_evidence_context,
)
from polaris.cells.roles.kernel.internal.llm_caller.context_audit._request_core import (
    _delivery_depth_contract_summary,
)
from polaris.kernelone.events.final_request_evidence import summarize_workspace_quality_evidence_context_slot


def _depth_error() -> str:
    return (
        "delivery_depth_contract_failed: implementation depth metrics: "
        "prod_files=3, prod_lines=466, test_files=1, test_assertions=22; "
        "minimums={'min_prod_files': 7, 'min_prod_lines': 650, 'min_test_files': 2}"
    )


def test_quality_failure_context_projects_numeric_depth_evidence() -> None:
    evidence = _build_materialization_quality_failure_evidence_context(
        artifact_quality_errors=[_depth_error()],
        missing_target_files=[],
        repair_target_files=["main.go"],
        changed_files=["main.go"],
        repair_attempt=2,
    )

    assert evidence["quality_metrics"] == {
        "prod_files": 3,
        "prod_lines": 466,
        "test_files": 1,
        "test_assertions": 22,
    }
    assert evidence["quality_minimums"] == {
        "min_prod_files": 7,
        "min_prod_lines": 650,
        "min_test_files": 2,
    }
    summary = summarize_failed_gate_evidence_context_slot(evidence)
    assert summary["quality_metrics"]["prod_files"] == 3
    assert summary["quality_minimums"]["min_prod_files"] == 7
    assert summary["repair_target_file_count"] == 1


def test_workspace_and_delivery_contract_summaries_preserve_policy_numbers() -> None:
    workspace_evidence = _build_materialization_quality_workspace_evidence_context(
        artifact_quality_errors=[_depth_error()],
        missing_target_files=[],
        repair_target_files=["main.go"],
        changed_files=[],
        repair_attempt=1,
    )
    workspace_summary = summarize_workspace_quality_evidence_context_slot(workspace_evidence)
    assert workspace_summary["quality_metrics"]["test_files"] == 1
    assert workspace_summary["quality_minimums"]["min_test_files"] == 2

    request = SimpleNamespace(
        context={
            "delivery_depth_contract": {
                "schema_version": "factory.delivery-depth-contract.v1",
                "level": 3,
                "minimums": {
                    "min_prod_files": 7,
                    "min_test_files": 2,
                    "descriptive_note": "must not leak",
                },
                "acceptance_contract": {
                    "deterministic_checks": ["go_compile"],
                    "required_behavior_tests": ["normal", "boundary", "invalid"],
                },
            }
        }
    )
    assert _delivery_depth_contract_summary(request) == {
        "schema_version": "factory.delivery-depth-contract.v1",
        "level": 3,
        "minimums": {"min_prod_files": 7, "min_test_files": 2},
        "deterministic_checks": ["go_compile"],
        "required_behavior_test_count": 3,
    }
