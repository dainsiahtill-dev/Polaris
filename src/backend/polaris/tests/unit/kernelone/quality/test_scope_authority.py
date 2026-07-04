"""Tests for the read-only ScopeAuthority projection."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.quality.file_ownership_ledger import record_file_owners
from polaris.kernelone.quality.scope_authority import (
    build_scope_authority_decision,
    matching_owner_handoff_request,
    normalize_declared_scope_path,
    owner_handoff_identifier_tokens,
    owner_task_retry_handoff_requests_from_scope_payload,
    ownership_handoff_requests_from_scope_payload,
    partition_paths_by_declared_scope,
    path_matches_any_declared_scope_candidate,
    scope_authority_decision_summary,
    task_record_identifier_tokens,
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
    assert decision["owner_task_retry_handoff_requests"] == [decision["ownership_handoff_requests"][0]]
    assert decision["unresolved_owner_handoff_requests"] == [decision["ownership_handoff_requests"][1]]


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
    assert decision["owner_task_retry_handoff_requests"] == []
    assert decision["unresolved_owner_handoff_requests"] == []


def test_scope_authority_summary_projects_bounded_decision_fields(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    record_file_owners(
        workspace,
        workspace,
        [{"step_id": "S1", "target_file": f"src/file_{index}.py"} for index in range(4)],
        "TASK-1",
    )

    decision = build_scope_authority_decision(
        workspace=workspace,
        cache_root=workspace,
        task_declared_write_targets=[f"tests/test_{index}.py" for index in range(4)],
        out_of_scope_repair_target_files=[f"src/file_{index}.py" for index in range(4)],
        requesting_task_id="TASK-2",
        reason="scope_filter",
    )

    summary = scope_authority_decision_summary(decision, limit=2)

    assert summary["task_declared_write_targets"] == ["tests/test_0.py", "tests/test_1.py"]
    assert summary["out_of_scope_repair_target_files"] == ["src/file_0.py", "src/file_1.py"]
    assert len(summary["ownership_handoff_requests"]) == 2
    assert len(summary["owner_task_retry_handoff_requests"]) == 2
    assert summary["unresolved_owner_handoff_requests"] == []


def test_scope_authority_summary_accepts_nested_scope_authority_payload() -> None:
    request = {
        "target_file": "src/index.js",
        "owner_found": True,
        "recommended_route": "owner_task_retry",
    }
    payload = {
        "scope_authority": {
            "task_declared_write_targets": ["tests/behavior.test.js"],
            "out_of_scope_repair_target_files": ["src/index.js"],
            "ownership_handoff_requests": [request],
            "owner_task_retry_handoff_requests": [request],
            "unresolved_owner_handoff_requests": [],
        }
    }

    summary = scope_authority_decision_summary(payload, limit=12)

    assert summary["task_declared_write_targets"] == ["tests/behavior.test.js"]
    assert summary["out_of_scope_repair_target_files"] == ["src/index.js"]
    assert summary["ownership_handoff_requests"] == [request]
    assert summary["ownership_handoff_requests"][0] is not request
    assert summary["owner_task_retry_handoff_requests"] == [request]
    assert summary["unresolved_owner_handoff_requests"] == []


def test_scope_authority_path_matching_is_case_insensitive_and_workspace_relative() -> None:
    assert normalize_declared_scope_path("L2-08/src/Index.ts", workspace_name="L2-08") == "src/Index.ts"
    assert path_matches_any_declared_scope_candidate("SRC/index.ts", ["src/index.ts"])
    assert path_matches_any_declared_scope_candidate("src/app/main.ts", ["src/**/main.ts"])
    assert path_matches_any_declared_scope_candidate("src/main.ts", ["src/**/main.ts"])
    assert not path_matches_any_declared_scope_candidate("../outside.ts", ["src/**/main.ts"])


def test_scope_authority_partitions_paths_by_declared_scope() -> None:
    in_scope, out_of_scope = partition_paths_by_declared_scope(
        ["SRC/index.ts", "src/app/main.ts", "tests/app.test.ts", "src/app/main.ts"],
        ["src/index.ts", "src/**/main.ts"],
    )

    assert in_scope == ("SRC/index.ts", "src/app/main.ts")
    assert out_of_scope == ("tests/app.test.ts",)


def test_scope_authority_partition_allows_all_when_scope_is_undeclared() -> None:
    in_scope, out_of_scope = partition_paths_by_declared_scope(
        ["src/index.ts", "", "tests/app.test.ts", "src/index.ts"],
        [],
    )

    assert in_scope == ("src/index.ts", "tests/app.test.ts")
    assert out_of_scope == ()


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

    classified_payload = {
        "task_boundary_scope_filter": {
            "scope_authority": {
                "ownership_handoff_requests": [
                    owned_request,
                    unknown_request,
                ],
                "owner_task_retry_handoff_requests": [owned_request],
                "unresolved_owner_handoff_requests": [unknown_request],
            }
        }
    }
    assert ownership_handoff_requests_from_scope_payload(classified_payload) == (
        owned_request,
        unknown_request,
    )
    assert owner_task_retry_handoff_requests_from_scope_payload(classified_payload) == (owned_request,)
    assert unresolved_owner_handoff_requests_from_scope_payload(classified_payload) == (unknown_request,)

    classified_only_payload = {
        "task_boundary_scope_filter": {
            "scope_authority": {
                "owner_task_retry_handoff_requests": [owned_request],
                "unresolved_owner_handoff_requests": [unknown_request],
            }
        }
    }
    assert ownership_handoff_requests_from_scope_payload(classified_only_payload) == (
        owned_request,
        unknown_request,
    )
    assert owner_task_retry_handoff_requests_from_scope_payload(classified_only_payload) == (owned_request,)
    assert unresolved_owner_handoff_requests_from_scope_payload(classified_only_payload) == (unknown_request,)


def test_scope_authority_matches_owner_handoff_using_projected_identifier_tokens() -> None:
    owner_row = {
        "id": "row-1",
        "external_task_id": "TASK-4",
        "metadata": {"external_task_id": "TASK-4"},
    }
    request = {
        "schema_version": "file-ownership-handoff-request/1",
        "target_file": "src/index.js",
        "owner_step_id": "unmatched-owner-step",
        "owner_parent": "unmatched-parent",
        "owner_task_identifier_tokens": ["4", "TASK-04", "TASK-4"],
        "owner_found": True,
        "recommended_route": "owner_task_retry",
    }

    assert task_record_identifier_tokens(owner_row) >= {"TASK-4", "4"}
    assert owner_handoff_identifier_tokens(request) == frozenset({"4", "TASK-04", "TASK-4"})
    assert matching_owner_handoff_request(owner_row, [request]) == request


def test_scope_authority_matches_owner_handoff_using_legacy_owner_fields() -> None:
    owner_row = {
        "external_task_id": "PM-0001-1-S4",
        "metadata": {"pm_task_id": "PM-0001-1-S4"},
    }
    request = {
        "schema_version": "file-ownership-handoff-request/1",
        "target_file": "src/index.js",
        "owner_step_id": "S4",
        "owner_parent": "PM-0001-1",
        "owner_found": True,
        "recommended_route": "owner_task_retry",
    }

    assert matching_owner_handoff_request(owner_row, [request]) == request
