from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.adapters.internal.director.quality_gate import (
    _filter_missing_workspace_file_errors_to_task_write_scope,
    _missing_materialization_quality_repair_target_files,
    _semantic_exporter_scope_discrepancy_evidence,
    _task_boundary_scope_filter_evidence,
)
from polaris.kernelone.quality import artifact_quality_issues_for_errors
from polaris.kernelone.quality.file_ownership_ledger import record_file_owners


def test_scope_filter_evidence_includes_file_ownership_handoff_requests(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    record_file_owners(
        workspace,
        workspace,
        [{"step_id": "S4", "target_file": "src/index.js"}],
        "PM-0001-1",
    )

    evidence = _task_boundary_scope_filter_evidence(
        {
            "task_id": "PM-0001-2-step-3",
            "target_files": ["tests/behavior.test.js"],
        },
        target_files=["src/index.js", "src/missing.js"],
        reason="quality_repair_targets_outside_current_task_target_files",
        workspace=workspace,
        cache_root=workspace,
    )

    requests = evidence["ownership_handoff_requests"]
    assert requests[0]["target_file"] == "src/index.js"
    assert requests[0]["owner_found"] is True
    assert requests[0]["owner_step_id"] == "S4"
    assert requests[0]["owner_parent"] == "PM-0001-1"
    assert requests[0]["recommended_route"] == "owner_task_retry"
    assert requests[1]["target_file"] == "src/missing.js"
    assert requests[1]["owner_found"] is False
    assert requests[1]["status"] == "owner_unknown"
    assert evidence["owner_task_retry_handoff_requests"] == [requests[0]]
    assert evidence["unresolved_owner_handoff_requests"] == [requests[1]]

    scope_authority = evidence["scope_authority"]
    assert scope_authority["schema_version"] == "scope-authority-decision/1"
    assert scope_authority["authority"] == "kernelone.quality.scope_authority"
    assert scope_authority["requesting_task_id"] == "PM-0001-2-step-3"
    assert scope_authority["task_declared_write_targets"] == ["tests/behavior.test.js"]
    assert scope_authority["out_of_scope_repair_target_files"] == ["src/index.js", "src/missing.js"]
    assert scope_authority["handoff_request_count"] == 2
    assert scope_authority["owner_found_count"] == 1
    assert scope_authority["owner_unknown_count"] == 1
    assert scope_authority["recommended_routes"] == ["owner_task_retry", "scope_authority_resolution"]


def test_semantic_exporter_scope_discrepancy_projects_owner_handoff_requests(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    record_file_owners(
        workspace,
        workspace,
        [{"step_id": "S4", "target_file": "src/index.js"}],
        "PM-0001-1",
    )
    scope_filter = _task_boundary_scope_filter_evidence(
        {
            "task_id": "PM-0001-2-step-3",
            "target_files": ["tests/behavior.test.js"],
        },
        target_files=["src/index.js", "src/missing.js"],
        reason="quality_repair_targets_outside_current_task_target_files",
        workspace=workspace,
        cache_root=workspace,
    )

    evidence = _semantic_exporter_scope_discrepancy_evidence(
        task={
            "task_id": "PM-0001-2-step-3",
            "target_files": ["tests/behavior.test.js"],
            "module_interface_contract": {"exports": ["scoreWish"]},
        },
        semantic_exporter_targets=["src/index.js"],
        repair_target_files=["src/index.js"],
        artifact_quality_errors=["Artifact quality scan failed: exported symbol lives outside task scope"],
        task_scope_filter_evidence=scope_filter,
    )

    assert evidence["recommended_owner"] == "chief_engineer"
    assert evidence["recommended_route"] == "pending_design_interface_contract"
    assert evidence["ownership_handoff_requests"] == scope_filter["ownership_handoff_requests"]
    assert evidence["owner_task_retry_handoff_requests"] == [scope_filter["ownership_handoff_requests"][0]]
    assert evidence["unresolved_owner_handoff_requests"] == [scope_filter["ownership_handoff_requests"][1]]
    assert evidence["scope_authority"] == scope_filter["scope_authority"]
    assert evidence["triage_summary"]["ownership_handoff_request_count"] == 2
    assert evidence["triage_summary"]["owner_task_retry_handoff_request_count"] == 1
    assert evidence["triage_summary"]["unresolved_owner_handoff_request_count"] == 1


def test_scope_filter_evidence_normalizes_workspace_prefixed_targets(tmp_path: Path) -> None:
    workspace = tmp_path / "L2-08"
    workspace.mkdir()

    evidence = _task_boundary_scope_filter_evidence(
        {
            "task_id": "TASK-2",
            "target_files": ["L2-08/tests/behavior.test.js"],
        },
        target_files=["L2-08/src/index.js"],
        reason="quality_repair_targets_outside_current_task_target_files",
        workspace=str(workspace),
        cache_root=str(workspace),
    )

    scope_authority = evidence["scope_authority"]
    assert scope_authority["task_declared_write_targets"] == ["tests/behavior.test.js"]
    assert scope_authority["out_of_scope_repair_target_files"] == ["src/index.js"]


def test_artifact_quality_issue_merge_preserves_structured_issue_when_raw_differs() -> None:
    error = "Artifact quality scan failed: src/app.ts(7,3): error TS2304: Cannot find name 'Widget'."
    typed_issue = {
        "code": "typescript_ts2304",
        "message": "Cannot find name 'Widget'.",
        "path": "src/app.ts",
        "line": 7,
        "column": 3,
        "source": "typescript_diagnostic",
        "metadata": {"raw": "tsc-json:src/app.ts:7:3:TS2304"},
    }

    issues = artifact_quality_issues_for_errors([error], (typed_issue,))

    assert issues == (typed_issue,)


def test_deferred_scope_filter_record_preserves_typed_artifact_quality_issue(tmp_path: Path) -> None:
    error = "Artifact quality scan failed: No such file or directory: src/index.js"
    typed_issue = {
        "code": "missing_workspace_file",
        "message": "Missing workspace file src/index.js",
        "path": "src/index.js",
        "source": "artifact_quality_scan",
        "metadata": {"raw": error},
    }
    context: dict[str, object] = {}

    retained = _filter_missing_workspace_file_errors_to_task_write_scope(
        [error],
        task={"task_id": "TASK-2", "target_files": ["tests/behavior.test.js"]},
        workspace_full=str(tmp_path),
        context=context,
        issue_payloads=(typed_issue,),
    )

    assert retained == []
    records = context["director_task_boundary_deferred_quality_errors"]
    assert isinstance(records, list)
    record = records[0]
    assert record["artifact_quality_errors"] == [error]
    assert record["target_files"] == ["src/index.js"]
    assert record["artifact_quality_issues"] == [typed_issue]


def test_materialization_missing_targets_prefer_typed_declared_target_issue(tmp_path: Path) -> None:
    typed_issue = {
        "code": "declared_target_missing",
        "message": "declared target missing",
        "path": "src/index.js",
        "source": "artifact_quality_scanner",
        "metadata": {"raw": "typed-only diagnostic"},
    }

    targets = _missing_materialization_quality_repair_target_files(
        {"target_files": ["src/index.js"]},
        str(tmp_path),
        artifact_quality_errors=["typed-only diagnostic"],
        artifact_quality_issues=(typed_issue,),
    )

    assert targets == ["src/index.js"]


def test_scope_filter_uses_typed_missing_workspace_issue_when_raw_text_is_not_parseable(
    tmp_path: Path,
) -> None:
    typed_issue = {
        "code": "missing_workspace_file",
        "message": "typed missing workspace file",
        "path": "src/index.js",
        "source": "artifact_quality_scanner",
        "metadata": {"raw": "typed-only diagnostic"},
    }
    context: dict[str, object] = {}

    retained = _filter_missing_workspace_file_errors_to_task_write_scope(
        ["typed-only diagnostic"],
        task={"task_id": "TASK-2", "target_files": ["tests/behavior.test.js"]},
        workspace_full=str(tmp_path),
        context=context,
        issue_payloads=(typed_issue,),
    )

    assert retained == []
    records = context["director_task_boundary_deferred_quality_errors"]
    assert isinstance(records, list)
    assert records[0]["target_files"] == ["src/index.js"]


def test_scope_filter_does_not_treat_unrelated_typed_issue_path_as_missing_target(
    tmp_path: Path,
) -> None:
    typed_issue = {
        "code": "typescript_ts2304",
        "message": "Cannot find name 'Widget'.",
        "path": "src/index.js",
        "source": "typescript_compiler",
        "metadata": {"raw": "typed-only diagnostic"},
    }
    context: dict[str, object] = {}

    retained = _filter_missing_workspace_file_errors_to_task_write_scope(
        ["typed-only diagnostic"],
        task={"task_id": "TASK-2", "target_files": ["tests/behavior.test.js"]},
        workspace_full=str(tmp_path),
        context=context,
        issue_payloads=(typed_issue,),
    )

    assert retained == ["typed-only diagnostic"]
    assert "director_task_boundary_deferred_quality_errors" not in context
