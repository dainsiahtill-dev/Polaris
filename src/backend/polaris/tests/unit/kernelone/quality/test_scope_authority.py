"""Tests for the read-only ScopeAuthority projection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from polaris.delivery.http.routers.factory import _quality_gate_owner_handoff_index
from polaris.kernelone.quality.file_ownership_ledger import record_file_owners
from polaris.kernelone.quality.scope_authority import (
    build_owner_handoff_index,
    build_scope_authority_decision,
    matching_owner_handoff_request,
    normalize_declared_scope_path,
    owner_handoff_identifier_tokens,
    owner_handoff_index_summary,
    owner_task_retry_handoff_requests_from_scope_payload,
    ownership_handoff_requests_from_scope_payload,
    partition_paths_by_declared_scope,
    path_matches_any_declared_scope_candidate,
    scope_authority_decision_summary,
    task_record_identifier_tokens,
    task_record_routing_key,
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


def test_scope_authority_partition_normalizes_workspace_prefixed_paths() -> None:
    in_scope, out_of_scope = partition_paths_by_declared_scope(
        ["L2-08/src/Index.ts", "./L2-08/tests/app.test.ts", "../outside.ts"],
        ["src/index.ts"],
        workspace_name="L2-08",
    )

    assert in_scope == ("src/Index.ts",)
    assert out_of_scope == ("tests/app.test.ts",)


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


def test_scope_authority_accepts_tuple_handoff_payload_with_priority() -> None:
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

    top_level_tuple: dict[str, Any] = {
        "ownership_handoff_requests": (owned_request, unknown_request),
    }
    assert ownership_handoff_requests_from_scope_payload(top_level_tuple) == (
        owned_request,
        unknown_request,
    )
    assert owner_task_retry_handoff_requests_from_scope_payload(top_level_tuple) == (owned_request,)
    assert unresolved_owner_handoff_requests_from_scope_payload(top_level_tuple) == (unknown_request,)

    nested_tuple: dict[str, Any] = {
        "scope_authority": {
            "ownership_handoff_requests": (owned_request, unknown_request),
        }
    }
    assert ownership_handoff_requests_from_scope_payload(nested_tuple) == (
        owned_request,
        unknown_request,
    )

    scope_filter_priority_tuple: dict[str, Any] = {
        "task_boundary_scope_filter": {
            "ownership_handoff_requests": (owned_request,),
        },
        "ownership_handoff_requests": (owned_request, unknown_request),
    }
    assert ownership_handoff_requests_from_scope_payload(scope_filter_priority_tuple) == (owned_request,)


def test_scope_authority_dedupes_repeated_handoff_rows_within_candidate() -> None:
    owned_request = {
        "schema_version": "file-ownership-handoff-request/1",
        "target_file": "src/index.js",
        "owner_found": True,
        "recommended_route": "owner_task_retry",
        "owner_step_id": "PM-0001-1-S4",
        "owner_task_identifier_tokens": ["4", "TASK-04", "TASK-4"],
    }
    duplicate_owned = dict(owned_request)
    unknown_request = {
        "schema_version": "file-ownership-handoff-request/1",
        "target_file": "src/missing.js",
        "owner_found": False,
        "recommended_route": "scope_authority_resolution",
    }

    payload: dict[str, Any] = {
        "ownership_handoff_requests": (
            owned_request,
            duplicate_owned,
            owned_request,
            unknown_request,
            unknown_request,
        )
    }

    deduped = ownership_handoff_requests_from_scope_payload(payload)
    assert deduped == (owned_request, unknown_request)
    assert owner_task_retry_handoff_requests_from_scope_payload(payload) == (owned_request,)
    assert unresolved_owner_handoff_requests_from_scope_payload(payload) == (unknown_request,)

    list_payload: dict[str, Any] = {
        "ownership_handoff_requests": [owned_request, duplicate_owned, unknown_request],
    }
    deduped_list = ownership_handoff_requests_from_scope_payload(list_payload)
    assert deduped_list == (owned_request, unknown_request)


def test_scope_authority_explicit_empty_tuple_or_list_blocks_fallthrough() -> None:
    owned_request = {
        "schema_version": "file-ownership-handoff-request/1",
        "target_file": "src/index.js",
        "owner_found": True,
        "recommended_route": "owner_task_retry",
        "owner_step_id": "PM-0001-1-S4",
    }

    only_empty_list: dict[str, Any] = {
        "ownership_handoff_requests": [],
    }
    assert ownership_handoff_requests_from_scope_payload(only_empty_list) == ()
    assert owner_task_retry_handoff_requests_from_scope_payload(only_empty_list) == ()
    assert unresolved_owner_handoff_requests_from_scope_payload(only_empty_list) == ()

    only_empty_tuple: dict[str, Any] = {
        "ownership_handoff_requests": (),
    }
    assert ownership_handoff_requests_from_scope_payload(only_empty_tuple) == ()
    assert owner_task_retry_handoff_requests_from_scope_payload(only_empty_tuple) == ()
    assert unresolved_owner_handoff_requests_from_scope_payload(only_empty_tuple) == ()

    nested_only_empty_tuple: dict[str, Any] = {
        "scope_authority": {
            "ownership_handoff_requests": (),
            "owner_task_retry_handoff_requests": (),
            "unresolved_owner_handoff_requests": (),
        }
    }
    assert ownership_handoff_requests_from_scope_payload(nested_only_empty_tuple) == ()
    assert owner_task_retry_handoff_requests_from_scope_payload(nested_only_empty_tuple) == ()
    assert unresolved_owner_handoff_requests_from_scope_payload(nested_only_empty_tuple) == ()

    scope_filter_only_empty_list_falls_through_to_top_tuple: dict[str, Any] = {
        "task_boundary_scope_filter": {
            "ownership_handoff_requests": [],
            "scope_authority": {"ownership_handoff_requests": (owned_request,)},
        },
        "ownership_handoff_requests": [owned_request],
    }
    assert ownership_handoff_requests_from_scope_payload(scope_filter_only_empty_list_falls_through_to_top_tuple) == (
        owned_request,
    )


def test_scope_authority_ignores_non_mapping_rows_in_handoff_candidates() -> None:
    owned_request = {
        "schema_version": "file-ownership-handoff-request/1",
        "target_file": "src/index.js",
        "owner_found": True,
        "recommended_route": "owner_task_retry",
        "owner_step_id": "PM-0001-1-S4",
    }

    payload: dict[str, Any] = {
        "ownership_handoff_requests": (
            "src/string-row-must-not-be-parsed",
            42,
            None,
            owned_request,
        )
    }

    assert ownership_handoff_requests_from_scope_payload(payload) == (owned_request,)

    non_string_payload: dict[str, Any] = {
        "ownership_handoff_requests": (
            "src/looks-like-target-but-is-not-evidence",
            ["src/list-row-is-not-evidence"],
            {"target_file": "src/looks-like-target-but-is-evidence-from-mapping"},
        )
    }
    assert ownership_handoff_requests_from_scope_payload(non_string_payload) == (
        {"target_file": "src/looks-like-target-but-is-evidence-from-mapping"},
    )


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


def test_scope_authority_owner_handoff_index_uses_public_task_record_routing_key() -> None:
    owner_row = {
        "id": "7",
        "external_task_id": "TASK-7",
        "metadata": {"external_task_id": "TASK-7"},
    }
    request = {
        "schema_version": "file-ownership-handoff-request/1",
        "target_file": "src/index.js",
        "owner_task_identifier_tokens": ["TASK-7"],
        "owner_found": True,
        "recommended_route": "owner_task_retry",
    }
    payload = {
        "task_boundary_scope_filter": {
            "scope_authority": {
                "owner_task_retry_handoff_requests": [request],
                "unresolved_owner_handoff_requests": [],
            }
        }
    }

    index = build_owner_handoff_index(payload, [owner_row])

    routing_key = task_record_routing_key(owner_row)
    assert routing_key == "7"
    assert index.matched_owner_handoff_by_task_key[routing_key] == request


def test_factory_owner_handoff_index_reader_uses_public_task_record_routing_key() -> None:
    owner_row = {
        "id": "8",
        "external_task_id": "TASK-8",
        "metadata": {"source_task_id": "TASK-8"},
    }
    request = {
        "schema_version": "file-ownership-handoff-request/1",
        "target_file": "src/index.js",
        "owner_task_identifier_tokens": ["TASK-8"],
        "owner_found": True,
        "recommended_route": "owner_task_retry",
    }
    payload = {
        "task_boundary_scope_filter": {
            "scope_authority": {
                "owner_task_retry_handoff_requests": [request],
                "unresolved_owner_handoff_requests": [],
            }
        }
    }

    index = _quality_gate_owner_handoff_index(payload, [owner_row])
    routing_key = task_record_routing_key(owner_row)

    assert routing_key == "8"
    assert index.matched_owner_handoff_by_task_key[routing_key] == request


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


def test_scope_authority_builds_owner_handoff_index() -> None:
    matched_request = {
        "target_file": "src/index.js",
        "owner_found": True,
        "recommended_route": "owner_task_retry",
        "owner_step_id": "TASK-12",
    }
    unmatched_request = {
        "target_file": "src/missing.js",
        "owner_found": True,
        "recommended_route": "owner_task_retry",
        "owner_step_id": "TASK-99",
    }
    unknown_request = {
        "target_file": "src/unknown.js",
        "owner_found": False,
        "recommended_route": "scope_authority_resolution",
    }

    index = build_owner_handoff_index(
        {
            "task_boundary_scope_filter": {
                "ownership_handoff_requests": [
                    matched_request,
                    unmatched_request,
                    unknown_request,
                ]
            }
        },
        [{"id": 12, "metadata": {}}],
    )

    assert index.all_handoff_requests == (matched_request, unmatched_request, unknown_request)
    assert index.owner_handoff_requests == (matched_request, unmatched_request)
    assert index.unknown_owner_handoff_requests == (unknown_request,)
    assert index.matched_owner_handoff_by_task_key["12"] == matched_request
    assert index.unmatched_owner_handoff_requests == (unmatched_request,)


def test_scope_authority_summarizes_owner_handoff_index() -> None:
    unmatched_request = {
        "target_file": "src/missing.js",
        "owner_found": True,
        "recommended_route": "owner_task_retry",
        "owner_step_id": "TASK-99",
    }
    unknown_request = {
        "target_file": "src/unknown.js",
        "owner_found": False,
        "recommended_route": "scope_authority_resolution",
    }

    empty_summary = owner_handoff_index_summary()
    assert empty_summary == {
        "ownership_handoff_count": 0,
        "matched_owner_handoff_count": 0,
        "matched_owner_handoff_routes": [],
        "unmatched_owner_handoff_count": 0,
        "unmatched_owner_handoff_requests": [],
        "unknown_owner_handoff_count": 0,
        "unknown_owner_handoff_requests": [],
    }

    index = build_owner_handoff_index(
        {
            "task_boundary_scope_filter": {
                "ownership_handoff_requests": [
                    unmatched_request,
                    unknown_request,
                ]
            }
        },
        [],
    )

    summary = owner_handoff_index_summary(index, limit=1)

    assert summary["ownership_handoff_count"] == 2
    assert summary["matched_owner_handoff_count"] == 0
    assert summary["matched_owner_handoff_routes"] == []
    assert summary["unmatched_owner_handoff_count"] == 1
    assert summary["unmatched_owner_handoff_requests"] == [unmatched_request]
    assert summary["unmatched_owner_handoff_requests"][0] is not unmatched_request
    assert summary["unknown_owner_handoff_count"] == 1
    assert summary["unknown_owner_handoff_requests"] == [unknown_request]
    assert summary["unknown_owner_handoff_requests"][0] is not unknown_request


def test_scope_authority_index_summary_none_projects_empty_matched_route() -> None:
    """``owner_handoff_index_summary`` must project an empty matched route even when no index exists."""

    summary = owner_handoff_index_summary()

    assert summary["matched_owner_handoff_count"] == 0
    assert summary["matched_owner_handoff_routes"] == []


def test_scope_authority_index_summary_projects_matched_routes_with_copy() -> None:
    """Matched owner handoffs are projected as a bounded list of copies, not the original request objects."""

    matched_request = {
        "schema_version": "file-ownership-handoff-request/1",
        "target_file": "src/index.js",
        "owner_task_identifier_tokens": ["TASK-7"],
        "owner_found": True,
        "recommended_route": "owner_task_retry",
    }
    unmatched_request = {
        "schema_version": "file-ownership-handoff-request/1",
        "target_file": "src/missing.js",
        "owner_task_identifier_tokens": ["TASK-99"],
        "owner_found": True,
        "recommended_route": "owner_task_retry",
    }
    unknown_request = {
        "schema_version": "file-ownership-handoff-request/1",
        "target_file": "src/unknown.js",
        "owner_found": False,
        "recommended_route": "scope_authority_resolution",
    }

    index = build_owner_handoff_index(
        {
            "task_boundary_scope_filter": {
                "ownership_handoff_requests": [
                    matched_request,
                    unmatched_request,
                    unknown_request,
                ]
            }
        },
        [{"id": "TASK-7", "metadata": {}}],
    )

    summary = owner_handoff_index_summary(index, limit=12)

    assert summary["matched_owner_handoff_count"] == 1
    assert summary["unmatched_owner_handoff_count"] == 1
    assert summary["unknown_owner_handoff_count"] == 1
    assert summary["ownership_handoff_count"] == 3
    assert summary["matched_owner_handoff_routes"] == [{"task_key": "TASK-7", "request": matched_request}]
    assert summary["matched_owner_handoff_routes"][0]["request"] is not matched_request


def test_scope_authority_index_summary_bounds_matched_route_list() -> None:
    """The matched route projection must respect the ``limit`` bound like other list projections."""

    requests = [
        {
            "schema_version": "file-ownership-handoff-request/1",
            "target_file": f"src/file_{index}.js",
            "owner_task_identifier_tokens": [f"TASK-{index}"],
            "owner_found": True,
            "recommended_route": "owner_task_retry",
        }
        for index in range(3)
    ]
    records = [{"id": f"TASK-{index}", "metadata": {}} for index in range(3)]

    index = build_owner_handoff_index(
        {"ownership_handoff_requests": requests},
        records,
    )

    summary = owner_handoff_index_summary(index, limit=2)

    assert summary["matched_owner_handoff_count"] == 3
    assert len(summary["matched_owner_handoff_routes"]) == 2
    assert all(
        isinstance(item, dict) and "task_key" in item and "request" in item
        for item in summary["matched_owner_handoff_routes"]
    )
