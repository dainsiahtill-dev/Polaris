from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.adapters.internal.director.quality_gate import (
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
