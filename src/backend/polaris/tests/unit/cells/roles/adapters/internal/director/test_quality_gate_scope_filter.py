from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.adapters.internal.director.quality_gate import (
    _artifact_quality_issues_for_errors,
    _task_boundary_scope_filter_evidence,
)
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

    issues = _artifact_quality_issues_for_errors([error], (typed_issue,))

    assert issues == (typed_issue,)
