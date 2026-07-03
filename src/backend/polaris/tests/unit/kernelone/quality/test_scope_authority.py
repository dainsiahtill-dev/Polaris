"""Tests for the read-only ScopeAuthority projection."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.quality.file_ownership_ledger import record_file_owners
from polaris.kernelone.quality.scope_authority import (
    build_scope_authority_decision,
    normalize_declared_scope_path,
    owner_task_retry_handoff_requests_from_scope_payload,
    ownership_handoff_requests_from_scope_payload,
    path_matches_any_declared_scope_candidate,
    unresolved_owner_handoff_requests_from_scope_payload,
)


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


def test_scope_authority_path_matching_is_case_insensitive_and_workspace_relative() -> None:
    assert normalize_declared_scope_path("L2-08/src/Index.ts", workspace_name="L2-08") == "src/Index.ts"
    assert path_matches_any_declared_scope_candidate("SRC/index.ts", ["src/index.ts"])
    assert path_matches_any_declared_scope_candidate("src/app/main.ts", ["src/**/main.ts"])
    assert path_matches_any_declared_scope_candidate("src/main.ts", ["src/**/main.ts"])
    assert not path_matches_any_declared_scope_candidate("../outside.ts", ["src/**/main.ts"])


def test_scope_authority_extracts_and_classifies_handoff_payloads() -> None:
    owned_request = {
        "schema_version": "file-ownership-handoff-request/1",
        "target_file": "src/index.js",
        "owner_found": True,
        "recommended_route": "owner_task_retry",
        "owner_step_id": "PM-0001-1-S4",
    }
    unknown_request = {
        "schema_version": "file-ownership-handoff-request/1",
        "target_file": "src/missing.js",
        "owner_found": False,
        "recommended_route": "scope_authority_resolution",
    }
    payload = {
        "task_boundary_scope_filter": {
            "ownership_handoff_requests": [],
            "scope_authority": {
                "ownership_handoff_requests": [
                    owned_request,
                    unknown_request,
                ],
            },
        }
    }

    assert ownership_handoff_requests_from_scope_payload(payload) == (
        owned_request,
        unknown_request,
    )
    assert owner_task_retry_handoff_requests_from_scope_payload(payload) == (owned_request,)
    assert unresolved_owner_handoff_requests_from_scope_payload(payload) == (unknown_request,)
