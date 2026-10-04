"""Canonical/raw result mirrors must not create fictitious tool dispatches."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public.directed_effect_receipt_validation import (
    directed_effect_receipt_payload_hash,
    directed_effect_receipt_v2_errors,
)
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle import build_tool_call_lifecycle_receipt
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle._helpers import _result_items


def _row(call_id: str, *, status: str = "success") -> dict[str, Any]:
    return {
        "call_id": call_id,
        "tool_name": "write_file",
        "status": status,
        "error": "syntax rejected before commit" if status == "error" else None,
        "result": {"written": status == "success"},
        "effect_receipt": {"operation": "write_file", "file": f"{call_id}.ts", "after_hash": call_id}
        if status == "success"
        else None,
        "effect_receipt_commit": None,
    }


def _lifecycle(batches: list[dict[str, Any]]) -> dict[str, Any]:
    return dict(
        build_tool_call_lifecycle_receipt(
            run_id="run", task_id="task", turn_id="turn", role="director", receipts=batches
        ).to_dict()
    )


@pytest.mark.parametrize("failed", [False, True])
def test_ten_mirrored_results_count_once_and_preserve_failed_effect(failed: bool) -> None:
    rows = [_row(f"call-{index}", status="error" if failed and index == 9 else "success") for index in range(10)]
    raw = deepcopy(rows)
    raw[-1].update(
        directed_effect_preserved_call_ids=[f"call-{index}" for index in range(9)],
        directed_effect_aborted_call_ids=[],
        directed_effect_activated_rollback_call_ids=[],
        directed_effect_executed_rollback_call_ids=[],
    )
    batch = {"results": rows, "raw_results": raw, "failure_count": int(failed)}
    before = deepcopy(batch)

    receipt = _lifecycle([batch])

    assert receipt["dispatched_tool_calls_count"] == 10
    assert receipt["tool_result_count"] == 10
    assert receipt["effect_receipt_count"] == (9 if failed else 10)
    assert receipt["ok"] is not failed
    assert receipt["failure_class"] == ("TOOL_RESULT_FAILED" if failed else "")
    assert receipt["reason"] == ("syntax rejected before commit" if failed else "")
    reconciled = _result_items([batch])
    assert reconciled[-1]["directed_effect_preserved_call_ids"] == [f"call-{index}" for index in range(9)]
    for key in (
        "directed_effect_aborted_call_ids",
        "directed_effect_activated_rollback_call_ids",
        "directed_effect_executed_rollback_call_ids",
    ):
        assert reconciled[-1][key] == []
    assert reconciled[-1]["status"] == ("error" if failed else "success")
    assert batch == before


@pytest.mark.parametrize("canonical_count,raw_count,want", [(2, 2, 2), (1, 2, 2), (2, 1, 2)])
def test_repeated_same_id_occurrences_are_paired_not_globally_deduplicated(
    canonical_count: int, raw_count: int, want: int
) -> None:
    row = _row("reused-id")
    receipt = _lifecycle([{"results": [deepcopy(row)] * canonical_count, "raw_results": [deepcopy(row)] * raw_count}])
    assert receipt["tool_result_count"] == want
    assert receipt["dispatched_tool_calls_count"] == want


@pytest.mark.parametrize("field", ["status", "result", "effect_receipt", "effect_receipt_commit", "tool_name"])
def test_conflicting_same_id_evidence_is_not_discarded(field: str) -> None:
    canonical = _row("call-1")
    raw = deepcopy(canonical)
    raw[field] = "error" if field == "status" else {"conflicting": True}
    batch = {"results": [canonical], "raw_results": [raw], "failure_count": 1}
    assert _result_items([batch]) == [canonical, raw]
    receipt = _lifecycle([batch])
    assert receipt["tool_result_count"] == 2
    assert receipt["ok"] is False


def test_distinct_call_ids_with_same_payload_remain_distinct() -> None:
    canonical = _row("call-1")
    raw = {**canonical, "call_id": "call-2"}
    receipt = _lifecycle([{"results": [canonical], "raw_results": [raw]}])
    assert receipt["tool_result_count"] == 2


@pytest.mark.parametrize("canonical", [None, [], "invalid", [None, "invalid"]])
def test_raw_only_legacy_results_remain_readable(canonical: Any) -> None:
    rows = [_row("call-1"), _row("call-2", status="error")]
    receipt = _lifecycle([{"results": canonical, "raw_results": rows, "failure_count": 1}])
    assert receipt["tool_result_count"] == 2
    assert receipt["effect_receipt_count"] == 1
    assert receipt["reason"] == "syntax rejected before commit"


def test_independent_batches_and_attempts_with_same_call_id_are_not_deduplicated() -> None:
    row = _row("reused-id")
    batches = [
        {
            "batch_id": batch_id,
            "turn_id": f"turn-{batch_id}",
            "results": [deepcopy(row)],
            "raw_results": [deepcopy(row)],
        }
        for batch_id in ("first", "retry")
    ]
    receipt = _lifecycle(batches)
    assert receipt["tool_result_count"] == 2
    assert receipt["dispatched_tool_calls_count"] == 2
    assert len(receipt["batch_receipt_refs"]) == 2


def test_raw_effect_and_commit_supplements_survive_reconciliation() -> None:
    canonical = {"call_id": "call-1", "tool_name": "write_file", "status": "success", "result": {"written": True}}
    raw = {
        **canonical,
        "effect_receipt": {"operation": "write_file", "file": "a.ts"},
        "effect_receipt_commit": {"event_id": "fact-1"},
    }
    batch = {"results": [canonical], "raw_results": [raw]}
    assert _result_items([batch]) == [raw]
    receipt = _lifecycle([batch])
    assert receipt["tool_result_count"] == 1
    assert receipt["effect_receipt_count"] == 1
    assert receipt["ok"] is True


def test_missing_effect_mirror_is_reported_once_without_inventing_success() -> None:
    row = {"call_id": "call-1", "tool_name": "write_file", "status": "success"}
    receipt = _lifecycle([{"results": [row], "raw_results": [deepcopy(row)]}])
    assert receipt["tool_result_count"] == 1
    assert receipt["effect_receipt_count"] == 0
    assert receipt["failure_class"] == "MISSING_EFFECT_RECEIPT"
    assert len(receipt["dropped_tool_calls"]) == 1
    assert receipt["ok"] is False


def test_rows_without_call_identity_are_not_assumed_to_be_mirrors() -> None:
    row = {"tool_name": "read_file", "status": "success", "result": "same"}
    assert len(_result_items([{"results": [row], "raw_results": [deepcopy(row)]}])) == 2


def test_merged_raw_claim_does_not_bypass_physical_effect_commit_validation() -> None:
    canonical = {"call_id": "call-1", "tool_name": "write_file", "status": "success"}
    raw = {
        **canonical,
        "effect_receipt": {
            "schema_version": "roles.adapters.director_physical_effect_receipt.v2",
            "authoritative": True,
            "durable": True,
            "receipt_outcome": "succeeded",
        },
        "effect_receipt_commit": {"code": "receipt_committed", "state": "RECEIPT_COMMITTED"},
    }
    receipt = _lifecycle([{"results": [canonical], "raw_results": [raw]}])
    assert receipt["tool_result_count"] == 1
    assert receipt["effect_receipt_count"] == 0
    assert receipt["failure_class"] == "MISSING_EFFECT_RECEIPT"
    assert receipt["ok"] is False


def test_reordered_raw_mirrors_preserve_canonical_order_and_raw_diagnostics() -> None:
    first, second = _row("call-1"), _row("call-2")
    raw_second = {**second, "diagnostic": "retained"}
    result = _result_items([{"results": [first, second], "raw_results": [raw_second, deepcopy(first)]}])
    assert result == [first, raw_second]


def _committed_row() -> dict[str, Any]:
    effect: dict[str, Any] = {
        "arguments_hash": "1" * 64,
        "authoritative": True,
        "batch_id": "batch-typed-conflict",
        "claim_grant_hash": "2" * 64,
        "context_id": "context-typed-conflict",
        "durable": True,
        "effect_call_id": None,
        "effect_operation_id": None,
        "normalized_tool_name": "write_file",
        "operation_id": "deo_v1_" + "a" * 48,
        "parent_close_eligible": True,
        "physical_result_hash": "3" * 64,
        "plan_hash": None,
        "policy_evidence_hash": "4" * 64,
        "repair_binding_hash": None,
        "repair_contingency_kind": None,
        "repair_request_hash": None,
        "receipt_binding_hash": "5" * 64,
        "receipt_outcome": "succeeded",
        "schema_version": "roles.adapters.director_physical_effect_receipt.v2",
        "target_state_hash": "6" * 64,
        "tool_call_id": "call-typed-conflict",
    }
    receipt_hash = directed_effect_receipt_payload_hash(effect)
    assert receipt_hash is not None
    effect.update(receipt_hash=receipt_hash, receipt_id=f"director-physical-effect-{receipt_hash[:24]}")
    commit = {
        "code": "receipt_committed",
        "event_id": "fact-typed-conflict",
        "operation_id": effect["operation_id"],
        "receipt_ref": effect["receipt_id"],
        "receipt_hash": receipt_hash,
        "receipt_binding_hash": effect["receipt_binding_hash"],
        "receipt_outcome": "succeeded",
        "state": "RECEIPT_COMMITTED",
        "version": 1,
    }
    assert directed_effect_receipt_v2_errors(effect, commit, prefix="receipt") == ()
    return {
        "call_id": "call-typed-conflict",
        "tool_name": "write_file",
        "status": "success",
        "effect_receipt": effect,
        "effect_receipt_commit": commit,
    }


@pytest.mark.parametrize("flag", ["authoritative", "durable", "parent_close_eligible"])
@pytest.mark.parametrize("invalid", [1, 1.0])
def test_invalid_raw_effect_type_conflict_remains_failed_with_valid_canonical(flag: str, invalid: Any) -> None:
    canonical = _committed_row()
    raw = deepcopy(canonical)
    raw["effect_receipt"][flag] = invalid
    errors = directed_effect_receipt_v2_errors(raw["effect_receipt"], raw["effect_receipt_commit"], prefix="receipt")
    assert errors and f"receipt:{flag}_not_true" in errors
    assert _lifecycle([{"raw_results": [raw]}])["ok"] is False

    receipt = _lifecycle([{"results": [canonical], "raw_results": [raw]}])

    assert receipt["ok"] is False
    assert receipt["failure_class"] == "MISSING_EFFECT_RECEIPT"
    assert receipt["tool_result_count"] == 2
    assert receipt["effect_receipt_count"] == 1
    assert len(receipt["dropped_tool_calls"]) == 1


@pytest.mark.parametrize("invalid", [True, 1.0])
def test_invalid_raw_commit_type_conflict_remains_failed(invalid: Any) -> None:
    canonical = _committed_row()
    raw = deepcopy(canonical)
    raw["effect_receipt_commit"]["version"] = invalid
    errors = directed_effect_receipt_v2_errors(raw["effect_receipt"], raw["effect_receipt_commit"], prefix="receipt")
    assert errors and "receipt:invalid_task_runtime_version" in errors
    assert _lifecycle([{"raw_results": [raw]}])["ok"] is False
    receipt = _lifecycle([{"results": [canonical], "raw_results": [raw]}])
    assert receipt["ok"] is False
    assert receipt["tool_result_count"] == 2
    assert receipt["effect_receipt_count"] == 1


@pytest.mark.parametrize(
    "canonical_value,raw_value",
    [(True, 1), (False, 0), (1, 1.0), (0.0, -0.0), ({"nested": [True]}, {"nested": [1]})],
)
def test_shared_result_values_preserve_json_type_and_numeric_wire_conflicts(
    canonical_value: Any, raw_value: Any
) -> None:
    canonical = {**_row("typed-result"), "result": canonical_value}
    raw = {**canonical, "result": raw_value}
    assert len(_result_items([{"results": [canonical], "raw_results": [raw]}])) == 2


class _UnsupportedEquality:
    def __eq__(self, other: object) -> bool:
        raise AssertionError("unsupported value equality must not run")


@pytest.mark.parametrize(
    "value", [(1,), {1}, Decimal("1"), float("inf"), float("nan"), {1: "non-string key"}, _UnsupportedEquality()]
)
def test_unsupported_shared_values_never_qualify_as_mirror_evidence(value: Any) -> None:
    canonical = {**_row("unsupported"), "result": value}
    raw = {**canonical}
    assert len(_result_items([{"results": [canonical], "raw_results": [raw]}])) == 2


def test_cyclic_shared_values_are_retained_without_comparison_recursion() -> None:
    value: list[Any] = []
    value.append(value)
    canonical = {**_row("cyclic"), "result": value}
    raw = {**canonical}
    assert len(_result_items([{"results": [canonical], "raw_results": [raw]}])) == 2


def test_finite_nested_json_mirrors_with_shared_container_aliases_still_match() -> None:
    shared = {"text": "中文", "value": 2.5, "nothing": None}
    canonical = {**_row("nested"), "result": [shared, shared]}
    raw = deepcopy(canonical)
    assert len(_result_items([{"results": [canonical], "raw_results": [raw]}])) == 1
