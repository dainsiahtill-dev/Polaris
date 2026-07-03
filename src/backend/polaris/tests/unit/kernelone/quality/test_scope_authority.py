"""Tests for the read-only ScopeAuthority projection."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.quality.file_ownership_ledger import record_file_owners
from polaris.kernelone.quality.scope_authority import build_scope_authority_decision


def test_scope_authority_projects_owner_handoffs(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    record_file_owners(
        workspace,
        workspace,
        [{"step_id": "S4", "target_file": "src/index.js"}],
        "PM-0001-1",
    )

    decision = build_scope_authority_decision(
        workspace=workspace,
        cache_root=workspace,
        task_declared_write_targets=["tests/behavior.test.js"],
        out_of_scope_repair_target_files=["./src/index.js", "src/missing.js", "src/index.js"],
        requesting_task_id="PM-0001-2-step-3",
        reason="quality_repair_targets_outside_current_task_target_files",
    ).to_dict()

    assert decision["schema_version"] == "scope-authority-decision/1"
    assert decision["task_declared_write_targets"] == ["tests/behavior.test.js"]
    assert decision["out_of_scope_repair_target_files"] == ["src/index.js", "src/missing.js"]
    assert decision["handoff_request_count"] == 2
    assert decision["owner_found_count"] == 1
    assert decision["owner_unknown_count"] == 1
    assert decision["recommended_routes"] == ["owner_task_retry", "scope_authority_resolution"]
    assert decision["ownership_handoff_requests"][0]["owner_step_id"] == "S4"


def test_scope_authority_without_workspace_still_records_defer_decision() -> None:
    decision = build_scope_authority_decision(
        workspace="",
        cache_root="",
        task_declared_write_targets=["tests/behavior.test.js"],
        out_of_scope_repair_target_files=["src/index.js"],
        requesting_task_id="TASK-2",
        reason="scope_filter",
    ).to_dict()

    assert decision["out_of_scope_repair_target_files"] == ["src/index.js"]
    assert decision["ownership_handoff_requests"] == []
    assert decision["handoff_request_count"] == 0
    assert decision["owner_found_count"] == 0
    assert decision["owner_unknown_count"] == 0
    assert decision["recommended_routes"] == []
