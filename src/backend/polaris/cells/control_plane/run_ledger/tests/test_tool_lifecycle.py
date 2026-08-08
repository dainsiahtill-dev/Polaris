from __future__ import annotations

from typing import Any, cast

import pytest
from polaris.cells.control_plane.run_ledger.public import tool_lifecycle
from polaris.cells.control_plane.run_ledger.public.directed_effect_receipt_validation import (
    directed_effect_receipt_payload_hash,
)
from polaris.cells.control_plane.run_ledger.public.failure_evidence import FailureClassV1
from polaris.cells.control_plane.run_ledger.public.projection import (
    _directed_effect_receipt_errors,
    _tool_receipt_modality,
    build_run_ledger_projection,
)
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle import (
    ToolLifecycleRequirementV1,
    batch_receipt_has_dispatch_evidence,
    build_claimed_materialization_without_tool_lifecycle_receipt,
    build_missing_dispatch_lifecycle_receipt,
    build_native_tool_call_envelope_payloads,
    build_native_tool_call_envelopes,
    build_tool_batch_lifecycle_receipt,
    build_tool_batch_lifecycle_receipt_from_sources,
    build_tool_call_lifecycle_receipt,
    build_tool_call_lifecycle_run_ledger_event,
    build_tool_dispatch_dropped_anomaly_from_lifecycle_receipt,
    build_tool_dispatch_dropped_anomaly_from_sources,
    build_tool_dispatch_dropped_anomaly_projection,
    build_tool_dispatch_dropped_lifecycle_from_anomaly_flags,
    build_tool_dispatch_dropped_lifecycle_from_observed_calls,
    build_tool_lifecycle_requirement_run_ledger_event,
    effect_receipts_from_batch_receipts,
    empty_tool_lifecycle_summary,
    failure_evidence_from_lifecycle_receipt,
    merge_tool_lifecycle_summaries,
    native_tool_call_count_from_facts,
    native_tool_call_count_from_metadata,
    native_tool_call_envelope_refs_from_metadata,
    native_tool_call_facts_from_lifecycle_receipt,
    native_tool_call_facts_from_metadata,
    native_tool_call_facts_from_raw_calls,
    native_tool_call_facts_from_sources,
    native_tool_call_names_from_facts,
    normalize_native_tool_call_envelope_refs,
    normalize_tool_call_lifecycle_receipt,
    observed_tool_call_names_from_sources,
    project_completion_audit_evidence_to_metadata,
    project_completion_dispatch_evidence_to_metadata,
    project_lifecycle_failure_evidence_to_metadata,
    project_native_tool_call_envelopes_to_metadata,
    project_native_tool_call_facts_from_evidence_to_metadata,
    project_native_tool_call_facts_to_metadata,
    project_tool_lifecycle_event,
    project_tool_lifecycle_failure_status,
    project_tool_lifecycle_metadata,
    project_tool_lifecycle_receipt_to_metadata,
    project_tool_lifecycle_summary,
    summarize_tool_lifecycle_events,
    task_boundary_tool_dispatch_from_lifecycle_metadata,
    task_boundary_tool_dispatch_from_lifecycle_receipt,
    tool_call_lifecycle_receipts_from_metadata,
    tool_dispatch_dropped_error_message,
    tool_dispatch_dropped_guard_applies,
)


def _authoritative_deo3_receipt(*, receipt_outcome: str = "succeeded") -> tuple[dict[str, Any], dict[str, Any]]:
    payload: dict[str, Any] = {
        "arguments_hash": "1" * 64,
        "authoritative": True,
        "batch_id": "batch-deo3",
        "claim_grant_hash": "2" * 64,
        "context_id": "context-deo3",
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
        "receipt_outcome": receipt_outcome,
        "schema_version": "roles.adapters.director_physical_effect_receipt.v2",
        "target_state_hash": "6" * 64,
        "tool_call_id": "call-deo3",
    }
    receipt_hash = directed_effect_receipt_payload_hash(payload)
    assert receipt_hash is not None
    effect = {
        **payload,
        "receipt_hash": receipt_hash,
        "receipt_id": f"director-physical-effect-{receipt_hash[:24]}",
    }
    commit = {
        "code": "receipt_committed",
        "event_id": "fact-deo3-receipt",
        "operation_id": effect["operation_id"],
        "receipt_ref": effect["receipt_id"],
        "receipt_hash": receipt_hash,
        "receipt_binding_hash": effect["receipt_binding_hash"],
        "receipt_outcome": receipt_outcome,
        "state": "RECEIPT_COMMITTED",
        "version": 3,
    }
    return effect, commit


def test_tool_lifecycle_all_exports_source_projection_helpers() -> None:
    required_exports = {
        "build_native_tool_call_envelope_payloads",
        "build_native_tool_call_envelopes",
        "build_tool_batch_lifecycle_receipt_from_sources",
        "build_tool_dispatch_dropped_anomaly_from_sources",
        "effect_receipts_from_batch_receipts",
        "native_tool_call_names_from_facts",
        "observed_tool_call_names_from_sources",
        "project_tool_lifecycle_failure_status",
        "project_tool_lifecycle_summary",
        "task_boundary_tool_dispatch_from_lifecycle_receipt",
        "tool_dispatch_dropped_guard_applies",
        "tool_dispatch_dropped_error_message",
    }

    assert required_exports <= set(tool_lifecycle.__all__)
    for name in required_exports:
        assert hasattr(tool_lifecycle, name)


def test_build_native_tool_call_envelopes_owns_public_envelope_shape() -> None:
    envelopes = build_native_tool_call_envelope_payloads(
        [
            {
                "id": "call-write",
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": {"path": "src/index.ts", "content": "secret payload"},
                },
            }
        ],
        provider="OpenAI",
    )
    typed_envelopes = build_native_tool_call_envelopes(
        [
            {
                "id": "call-write",
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": {"path": "src/index.ts", "content": "secret payload"},
                },
            }
        ],
        provider="OpenAI",
    )

    assert len(typed_envelopes) == 1
    assert envelopes == [typed_envelopes[0].to_dict()]
    assert envelopes[0]["schema_version"] == "native_tool_call_envelope.v1"
    assert envelopes[0]["provider"] == "openai"
    assert envelopes[0]["tool_name"] == "write_file"
    assert envelopes[0]["call_id"] == "call-write"
    assert envelopes[0]["metadata"] == {"has_tool_name": True}
    assert "content" not in envelopes[0]
    assert len(envelopes[0]["raw_call_hash"]) == 64
    assert len(envelopes[0]["arguments_hash"]) == 64


def test_tool_lifecycle_receipt_links_batch_and_effect_refs() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        provider_response_hash="provider-response-hash",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "call_id": "call-1",
                        "tool_name": "write_file",
                        "status": "success",
                        "effect_receipt": {
                            "operation": "write_file:create",
                            "file": "src/index.js",
                            "before_hash": "",
                            "after_hash": "after",
                        },
                    }
                ],
                "success_count": 1,
                "failure_count": 0,
            }
        ],
    ).to_dict()

    assert receipt["ok"] is True
    assert receipt["dispatch_status"] == "dispatched"
    assert receipt["provider_response_hash"] == "provider-response-hash"
    assert receipt["batch_receipt_hash"]
    assert receipt["batch_receipt_refs"][0]["batch_id"] == "batch-1"
    assert receipt["effect_receipt_count"] == 1
    assert receipt["effect_receipt_refs"][0]["file"] == "src/index.js"
    assert receipt["effect_receipt_refs"][0]["tool_name"] == "write_file"
    assert failure_evidence_from_lifecycle_receipt(receipt) == {}


@pytest.mark.parametrize(
    ("receipt_outcome", "expected_ok", "expected_failure"),
    (
        ("succeeded", True, ""),
        ("failed", False, FailureClassV1.TOOL_RESULT_FAILED.value),
    ),
)
def test_tool_lifecycle_projects_task_runtime_authoritative_effect_receipt(
    receipt_outcome: str,
    expected_ok: bool,
    expected_failure: str,
) -> None:
    effect, commit = _authoritative_deo3_receipt(receipt_outcome=receipt_outcome)
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-deo3",
        task_id="TASK-DEO3",
        turn_id="turn-deo3",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-deo3",
                "results": [
                    {
                        "call_id": "call-deo3",
                        "tool_name": "write_file",
                        "status": "success",
                        "effect_receipt": effect,
                        "effect_receipt_commit": commit,
                    }
                ],
                "success_count": 1,
                "failure_count": 0,
            }
        ],
    ).to_dict()

    assert receipt["effect_receipt_count"] == 1
    assert receipt["ok"] is expected_ok
    assert receipt["failure_class"] == expected_failure
    effect_ref = receipt["effect_receipt_refs"][0]
    assert effect_ref["receipt_hash"] == effect["receipt_hash"]
    assert effect_ref["receipt_outcome"] == receipt_outcome
    assert effect_ref["task_runtime_state"] == "RECEIPT_COMMITTED"
    assert effect_ref["task_runtime_event_id"] == "fact-deo3-receipt"


def test_tool_lifecycle_projects_matching_nested_commit_when_direct_receipt_copy_lacks_commit() -> None:
    """Regression: preserve the valid R5 batch shape without inventing receipt evidence."""

    effect, commit = _authoritative_deo3_receipt()
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-deo3-r5",
        task_id="TASK-DEO3-R5",
        turn_id="turn-deo3-r5",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-deo3-r5",
                "results": [
                    {
                        "call_id": "call-deo3",
                        "tool_name": "write_file",
                        "status": "success",
                        "effect_receipt": effect,
                        "result": {
                            "effect_receipt": dict(effect),
                            "effect_receipt_commit": commit,
                        },
                    }
                ],
                "success_count": 1,
                "failure_count": 0,
            }
        ],
    ).to_dict()

    assert receipt["ok"] is True
    assert receipt["failure_class"] == ""
    assert receipt["effect_receipt_count"] == 1
    assert receipt["effect_receipt_refs"][0]["task_runtime_event_id"] == "fact-deo3-receipt"


def test_r156_lifecycle_failure_reason_not_bare_dispatched_status() -> None:
    """R156: failure_count>0 must not leave failure_evidence.reason as \"dispatched\"."""

    receipt = build_tool_call_lifecycle_receipt(
        run_id="director-r156",
        task_id="TASK-3",
        turn_id="turn-r156",
        role="director",
        native_tool_calls_count=2,
        decoded_tool_calls_count=2,
        dispatched_tool_calls_count=2,
        dispatch_status="dispatched",
        receipts=[
            {
                "batch_id": "batch-fail",
                "failure_count": 1,
                "results": [
                    {
                        "call_id": "call-fail",
                        "tool_name": "write_file",
                        "status": "error",
                        "error": "serial_mutation_sibling_aborted_after_failure",
                    }
                ],
            },
            {
                "batch_id": "batch-aborted",
                "failure_count": 1,
                "results": [
                    {
                        "call_id": "call-aborted",
                        "tool_name": "write_file",
                        "status": "aborted",
                        "error": "serial_mutation_sibling_aborted_after_failure",
                    }
                ],
            },
        ],
    ).to_dict()

    assert receipt["ok"] is False
    assert receipt["failure_class"] == FailureClassV1.TOOL_RESULT_FAILED.value
    assert receipt["dispatch_status"] == "dispatched"
    assert receipt["reason"] == "serial_mutation_sibling_aborted_after_failure"
    assert receipt["reason"] != "dispatched"
    evidence = failure_evidence_from_lifecycle_receipt(receipt)
    assert evidence["failure_class"] == FailureClassV1.TOOL_RESULT_FAILED.value
    assert evidence["reason"] == "serial_mutation_sibling_aborted_after_failure"


def test_tool_lifecycle_rejects_nested_commit_for_different_direct_receipt() -> None:
    """A nested commit cannot authorize a different direct receipt projection."""

    direct_effect, _ = _authoritative_deo3_receipt()
    nested_effect, nested_commit = _authoritative_deo3_receipt(receipt_outcome="failed")
    item = {
        "effect_receipt": direct_effect,
        "result": {
            "effect_receipt": nested_effect,
            "effect_receipt_commit": nested_commit,
        },
    }

    assert tool_lifecycle._effect_receipt_from_result(item) == {}


def test_run_ledger_projects_v2_operation_from_hash_bound_tool_name() -> None:
    effect, commit = _authoritative_deo3_receipt()

    modality = _tool_receipt_modality(
        {"tool_receipts": [{"effect_receipt": effect, "effect_receipt_commit": commit}]},
        {},
    )

    assert modality is not None
    assert modality["ok"] is True
    assert modality["metadata"]["operations"] == ["write_file"]


def test_tool_lifecycle_rejects_task_runtime_commit_bound_to_another_receipt() -> None:
    effect, commit = _authoritative_deo3_receipt()
    commit["receipt_hash"] = "0" * 64
    item = {
        "effect_receipt": effect,
        "effect_receipt_commit": commit,
    }

    assert tool_lifecycle._effect_receipt_from_result(item) == {}


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    (
        ("physical_result_hash", "9" * 64),
        ("receipt_hash", "9" * 64),
        ("receipt_id", "director-physical-effect-" + "9" * 24),
    ),
)
def test_tool_lifecycle_rejects_tampered_authoritative_receipt(
    field: str,
    tampered_value: object,
) -> None:
    effect, commit = _authoritative_deo3_receipt()
    effect[field] = tampered_value
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-deo3-tampered",
        task_id="TASK-DEO3",
        turn_id="turn-deo3",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-deo3",
                "results": [
                    {
                        "call_id": "call-deo3",
                        "tool_name": "write_file",
                        "status": "success",
                        "effect_receipt": effect,
                        "effect_receipt_commit": commit,
                    }
                ],
                "success_count": 1,
                "failure_count": 0,
            }
        ],
    ).to_dict()

    assert receipt["ok"] is False
    assert receipt["dispatch_status"] == "blocked"
    assert receipt["effect_receipt_count"] == 0
    assert receipt["failure_class"] == FailureClassV1.MISSING_EFFECT_RECEIPT.value


@pytest.mark.parametrize("invalid_float", (float("nan"), float("inf"), float("-inf")))
def test_authoritative_receipt_non_finite_float_fails_closed_in_both_consumers(
    invalid_float: float,
) -> None:
    effect, commit = _authoritative_deo3_receipt()
    effect["physical_result_hash"] = invalid_float
    projection_receipt = {**effect, "_task_runtime_receipt_commit": commit}

    errors = _directed_effect_receipt_errors(projection_receipt, index=0)

    assert errors is not None
    assert "receipt[0]:invalid_receipt_payload" in errors
    assert tool_lifecycle._effect_receipt_from_result({"effect_receipt": effect, "effect_receipt_commit": commit}) == {}


@pytest.mark.parametrize(
    ("target", "field", "invalid_value"),
    (
        ("effect", "operation_id", 7),
        ("effect", "normalized_tool_name", 7),
        ("effect", "operation_id", " deo_v1_" + "a" * 48 + " "),
        ("effect", "normalized_tool_name", " write_file "),
        ("effect", "arguments_hash", "not-a-hash"),
        ("effect", "physical_result_hash", 42),
        ("effect", "plan_hash", ""),
        ("effect", "schema_version", " roles.adapters.director_physical_effect_receipt.v2 "),
        ("effect", "receipt_hash", "<pad-current>"),
        ("effect", "receipt_id", "<pad-current>"),
        ("commit", "event_id", 7),
        ("commit", "operation_id", 7),
        ("commit", "version", None),
        ("commit", "version", 0),
        ("commit", "version", True),
        ("commit", "version", "3"),
    ),
)
def test_authoritative_receipt_malformed_types_hashes_and_version_fail_closed_in_both_consumers(
    target: str,
    field: str,
    invalid_value: object,
) -> None:
    effect, commit = _authoritative_deo3_receipt()
    malformed = effect if target == "effect" else commit
    malformed[field] = f" {malformed[field]} " if invalid_value == "<pad-current>" else invalid_value
    projection_receipt = {**effect, "_task_runtime_receipt_commit": commit}

    errors = _directed_effect_receipt_errors(projection_receipt, index=0)

    assert errors is not None
    assert errors
    assert tool_lifecycle._effect_receipt_from_result({"effect_receipt": effect, "effect_receipt_commit": commit}) == {}


def test_authoritative_receipt_rejects_unhashed_audit_field_injection() -> None:
    effect, commit = _authoritative_deo3_receipt()
    effect.update(
        {
            "path": "/tampered/unbound",
            "before_hash": "a" * 64,
            "after_hash": "b" * 64,
        }
    )
    projection_receipt = {**effect, "_task_runtime_receipt_commit": commit}

    errors = _directed_effect_receipt_errors(projection_receipt, index=0)

    assert errors == ["receipt[0]:unexpected_receipt_fields:after_hash,before_hash,path"]
    assert tool_lifecycle._effect_receipt_from_result({"effect_receipt": effect, "effect_receipt_commit": commit}) == {}


def test_tool_lifecycle_treats_uncommitted_v2_effect_receipt_as_missing() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-deo3",
        task_id="TASK-DEO3",
        turn_id="turn-deo3",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-deo3",
                "results": [
                    {
                        "call_id": "call-deo3",
                        "tool_name": "write_file",
                        "status": "success",
                        "effect_receipt": {
                            "schema_version": "roles.adapters.director_physical_effect_receipt.v2",
                            "operation_id": "deo_v1_" + "d" * 48,
                            "receipt_hash": "e" * 64,
                            "receipt_binding_hash": "f" * 64,
                            "receipt_outcome": "succeeded",
                            "authoritative": True,
                            "durable": True,
                            "parent_close_eligible": True,
                        },
                    }
                ],
                "success_count": 1,
                "failure_count": 0,
            }
        ],
    ).to_dict()

    assert receipt["ok"] is False
    assert receipt["effect_receipt_count"] == 0
    assert receipt["failure_class"] == FailureClassV1.MISSING_EFFECT_RECEIPT.value


def test_project_native_tool_call_facts_to_metadata_can_emit_decision_caller_compat_count() -> None:
    metadata: dict[str, object] = {}

    project_native_tool_call_facts_to_metadata(
        metadata,
        {
            "native_tool_calls_count": 2,
            "native_tool_call_names": ["read_file", "write_file"],
        },
        project_decision_caller_count=True,
    )

    assert metadata["native_tool_calls_count"] == 2
    assert metadata["decision_caller_native_tool_calls_count"] == 2
    assert metadata["native_tool_call_names"] == ["read_file", "write_file"]


def test_project_tool_lifecycle_summary_owns_read_model_shape() -> None:
    summary = {
        "ok": False,
        "event_count": "2",
        "native_tool_calls_count": "3",
        "decoded_tool_calls_count": "2",
        "dispatched_tool_calls_count": "1",
        "tool_result_count": "1",
        "effect_receipt_count": "1",
        "native_tool_call_names": ["write_file", "write_file", "execute_command"],
        "dropped_count": "1",
        "failed_count": "0",
        "failure_evidence": [{"failure_class": "TOOL_DISPATCH_DROPPED"}],
        "events": [{"status": "dropped"}],
    }

    projection = project_tool_lifecycle_summary(summary)

    assert {key: projection[key] for key in summary} == {
        "ok": False,
        "event_count": 2,
        "native_tool_calls_count": 3,
        "decoded_tool_calls_count": 2,
        "dispatched_tool_calls_count": 1,
        "tool_result_count": 1,
        "effect_receipt_count": 1,
        "native_tool_call_names": ["write_file", "execute_command"],
        "dropped_count": 1,
        "failed_count": 0,
        "failure_evidence": [{"failure_class": "TOOL_DISPATCH_DROPPED"}],
        "events": [{"status": "dropped"}],
    }
    assert projection["unresolved_count"] == 1
    assert projection["unresolved_dropped_count"] == 1
    assert projection["unresolved_failed_count"] == 1
    assert set(projection["latest_by_task"]) == {"legacy:aggregate"}
    assert set(projection["unresolved_by_task"]) == {"legacy:aggregate"}
    assert projection["outcome_projection"]["degraded"] is True
    assert projection["outcome_projection"]["fallback"] == "historical_counts"


def test_normalize_native_tool_call_envelope_refs_filters_and_deduplicates() -> None:
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native_tool_call:openai:0:call-1:abcdef",
        "provider": "openai",
        "tool_name": "write_file",
        "call_id": "call-1",
    }
    without_id = {
        "schema_version": "native_tool_call_envelope.v1",
        "provider": "openai",
        "tool_name": "execute_command",
        "call_id": "call-2",
        "raw_call_hash": "a" * 64,
        "arguments_hash": "b" * 64,
    }

    refs = normalize_native_tool_call_envelope_refs(
        [
            envelope,
            dict(envelope),
            "not-an-envelope",
            without_id,
            dict(without_id),
        ]
    )

    assert refs == (envelope, without_id)


def test_tool_lifecycle_receipt_derives_dispatched_count_from_batch_receipts() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=0,
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "call_id": "call-1",
                        "tool_name": "read_file",
                        "status": "success",
                        "result": {"ok": True},
                    }
                ],
                "success_count": 1,
                "failure_count": 0,
            }
        ],
    ).to_dict()

    assert receipt["dispatch_status"] == "dispatched"
    assert receipt["dispatched_tool_calls_count"] == 1
    assert receipt["tool_result_count"] == 1
    assert receipt["failure_class"] == ""


def test_tool_lifecycle_receipt_detects_missing_batch_receipt() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[],
    ).to_dict()

    assert receipt["ok"] is False
    assert receipt["dispatch_status"] == "blocked"
    assert receipt["failure_class"] == "MISSING_BATCH_RECEIPT"


def test_tool_batch_lifecycle_receipt_classifies_decoded_batch_without_receipt() -> None:
    receipt = build_tool_batch_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        provider_response_hash="provider-response-hash",
        decoded_tool_calls_count=2,
        receipts=[],
    ).to_dict()

    assert receipt["ok"] is False
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert receipt["native_tool_calls_count"] == 2
    assert receipt["decoded_tool_calls_count"] == 2
    assert receipt["dropped_tool_calls"] == [
        {
            "count": 2,
            "reason": "decoded_tool_batch_without_authoritative_receipt",
        }
    ]
    assert receipt["reason"] == "decoded_tool_batch_produced_no_authoritative_batch_receipt"


def test_tool_batch_lifecycle_receipt_from_sources_owns_native_fact_projection() -> None:
    receipt = build_tool_batch_lifecycle_receipt_from_sources(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        provider_response_hash="provider-response-hash",
        metadata={
            "native_tool_call_envelopes": [
                {"envelope_id": "tool-envelope-1", "tool_name": "write_file"},
                {"envelope_id": "tool-envelope-2", "tool_name": "execute_command"},
            ],
        },
        native_tool_calls=[
            {"function": {"name": "write_file"}},
            {"function": {"name": "execute_command"}},
        ],
        decoded_tool_calls_count=2,
        receipts=[],
    ).to_dict()

    assert receipt["ok"] is False
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["native_tool_calls_count"] == 2
    assert receipt["decoded_tool_calls_count"] == 2
    assert receipt["native_tool_call_envelope_refs"] == [
        {"envelope_id": "tool-envelope-1", "tool_name": "write_file"},
        {"envelope_id": "tool-envelope-2", "tool_name": "execute_command"},
    ]


def test_text_fallback_lifecycle_without_dispatch_has_specific_failure_class() -> None:
    receipt = build_tool_batch_lifecycle_receipt_from_sources(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        metadata={
            "compatibility_mode": "required_tool_text_fallback",
            "text_fallback_requested": True,
            "native_tool_surface_absent_because_text_fallback": True,
            "text_tool_parser_attempted": True,
            "text_tool_decoded_calls_count": 1,
            "native_tool_call_envelopes": [
                {"envelope_id": "text-write-1", "tool_name": "write_file"},
            ],
        },
        decoded_tool_calls_count=1,
        receipts=[],
    ).to_dict()

    assert receipt["ok"] is False
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == "REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED"
    assert receipt["text_fallback_requested"] is True
    assert receipt["parser_attempted"] is True
    assert receipt["native_tool_surface_absent_because_text_fallback"] is True


def test_tool_batch_lifecycle_receipt_keeps_authoritative_receipt_dispatched() -> None:
    receipt = build_tool_batch_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        decoded_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "call_id": "call-1",
                        "tool_name": "read_file",
                        "status": "success",
                        "result": {"ok": True},
                    }
                ],
            }
        ],
    ).to_dict()

    assert receipt["ok"] is True
    assert receipt["dispatch_status"] == "dispatched"
    assert receipt["failure_class"] == ""
    assert receipt["reason"] == ""


def test_build_dropped_lifecycle_from_anomaly_flags_preserves_legacy_envelopes() -> None:
    envelopes = [
        {"envelope_id": "native-read", "tool_name": "read_file"},
        {"envelope_id": "native-write", "tool_name": "write_file"},
    ]

    lifecycle = build_tool_dispatch_dropped_lifecycle_from_anomaly_flags(
        anomaly_flags=[
            {
                "type": "TOOL_DISPATCH_DROPPED",
                "native_tool_calls_count": 99,
                "native_tool_call_envelopes": envelopes,
                "provider_response_hash": "hash-1",
            }
        ],
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        reason="tool dispatch dropped",
    )

    assert lifecycle["provider_response_hash"] == "hash-1"
    assert lifecycle["native_tool_calls_count"] == 2
    assert lifecycle["decoded_tool_calls_count"] == 2
    assert lifecycle["native_tool_call_envelope_refs"] == envelopes
    assert lifecycle["dropped_tool_calls"] == [
        {"tool_name": "read_file", "envelope_id": "native-read", "reason": "tool_dispatch_dropped"},
        {"tool_name": "write_file", "envelope_id": "native-write", "reason": "tool_dispatch_dropped"},
    ]


def test_build_dropped_lifecycle_from_anomaly_flags_prefers_lifecycle_receipt() -> None:
    envelope = {"envelope_id": "native-receipt-write", "tool_name": "write_file"}

    lifecycle = build_tool_dispatch_dropped_lifecycle_from_anomaly_flags(
        anomaly_flags=[
            {
                "type": "TOOL_DISPATCH_DROPPED",
                "native_tool_calls_count": 99,
                "provider_response_hash": "legacy-hash",
                "tool_call_lifecycle_receipt": {
                    "schema_version": "tool_call_lifecycle_receipt.v1",
                    "provider_response_hash": "receipt-hash",
                    "native_tool_calls_count": 1,
                    "decoded_tool_calls_count": 1,
                    "dispatched_tool_calls_count": 0,
                    "native_tool_call_envelope_refs": [envelope, "invalid-ref", dict(envelope)],
                    "dropped_tool_calls": [{"tool_name": "write_file", "reason": "tool_dispatch_dropped"}],
                    "dispatch_status": "dropped",
                    "failure_class": "TOOL_DISPATCH_DROPPED",
                },
            }
        ],
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        reason="tool dispatch dropped",
    )

    assert lifecycle["provider_response_hash"] == "receipt-hash"
    assert lifecycle["native_tool_calls_count"] == 1
    assert lifecycle["decoded_tool_calls_count"] == 1
    assert lifecycle["native_tool_call_envelope_refs"] == [envelope]
    assert lifecycle["dropped_tool_calls"] == [{"tool_name": "write_file", "reason": "tool_dispatch_dropped"}]


def test_build_dropped_lifecycle_from_observed_calls_owns_dropped_refs() -> None:
    lifecycle = build_tool_dispatch_dropped_lifecycle_from_observed_calls(
        tool_names=["write_file", "write_file", "execute_command", ""],
        reason="observed calls had no result receipt",
    )

    assert lifecycle["dispatch_status"] == "dropped"
    assert lifecycle["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert lifecycle["native_tool_calls_count"] == 2
    assert lifecycle["dropped_tool_calls"] == [
        {"tool_name": "write_file", "reason": "tool_dispatch_dropped"},
        {"tool_name": "execute_command", "reason": "tool_dispatch_dropped"},
    ]
    assert lifecycle["reason"] == "observed calls had no result receipt"


def test_build_dropped_lifecycle_from_observed_calls_prefers_native_envelopes() -> None:
    lifecycle = build_tool_dispatch_dropped_lifecycle_from_observed_calls(
        tool_names=["ignored"],
        native_tool_call_envelopes=[
            {"envelope_id": "native-1", "tool_name": "write_file"},
        ],
    )

    assert lifecycle["native_tool_calls_count"] == 1
    assert lifecycle["native_tool_call_envelope_refs"] == [{"envelope_id": "native-1", "tool_name": "write_file"}]
    assert lifecycle["dropped_tool_calls"] == [
        {"tool_name": "write_file", "envelope_id": "native-1", "reason": "tool_dispatch_dropped"}
    ]


def test_observed_tool_call_names_from_sources_owns_runtime_aliases() -> None:
    tool_calls = [
        {"name": "write_file"},
        {"tool": "read_file"},
        {"function": {"name": "execute_command"}},
        {"functionName": "repo_tree"},
        {"other": "ignored"},
        "not-a-mapping",
    ]

    assert observed_tool_call_names_from_sources(tool_calls) == (
        "write_file",
        "read_file",
        "execute_command",
        "repo_tree",
    )


def test_observed_tool_call_names_from_sources_falls_back_to_lifecycle_metadata() -> None:
    metadata = {
        "tool_call_lifecycle_receipt": {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_call_envelope_refs": [
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
            ],
        }
    }

    assert observed_tool_call_names_from_sources([], metadata) == ("write_file",)


def test_tool_lifecycle_receipt_preserves_dropped_tool_details() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=0,
        dropped_tool_calls=["write_file"],
        receipts=[],
    ).to_dict()

    assert receipt["ok"] is False
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert receipt["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "reason": "tool_dispatch_dropped",
        }
    ]
    failure_evidence = failure_evidence_from_lifecycle_receipt(receipt)
    assert failure_evidence["schema_version"] == "failure_evidence.v1"
    assert failure_evidence["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert failure_evidence["responsible_layer"] == "execution_control_plane"
    assert failure_evidence["reason"] == "dropped"
    assert failure_evidence["metadata"]["source"] == "tool_call_lifecycle_receipt.v1"
    assert failure_evidence["metadata"]["dropped_tool_calls"] == receipt["dropped_tool_calls"]
    assert failure_evidence["evidence_refs"][0].startswith("dropped_tool_call:")


def test_tool_dispatch_dropped_anomaly_projection_builds_lifecycle_and_failure_evidence() -> None:
    anomaly = build_tool_dispatch_dropped_anomaly_projection(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        provider_response_hash="provider-hash",
        native_tool_calls_count=2,
        native_tool_call_envelopes=[
            {"envelope_id": "tool-envelope-1", "tool_name": "write_file"},
            {"envelope_id": "tool-envelope-2", "tool_name": "execute_command"},
        ],
        streaming=True,
    )

    lifecycle = anomaly["tool_call_lifecycle_receipt"]
    assert anomaly["type"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert anomaly["streaming"] is True
    assert anomaly["native_tool_calls_count"] == 2
    assert anomaly["native_tool_call_envelopes"] == lifecycle["native_tool_call_envelope_refs"]
    assert anomaly["provider_response_hash"] == "provider-hash"
    assert lifecycle["dispatch_status"] == "dropped"
    assert lifecycle["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert lifecycle["decoded_tool_calls_count"] == 2
    assert lifecycle["dispatched_tool_calls_count"] == 0
    failure_evidence = anomaly["failure_evidence"][0]
    assert failure_evidence["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert "provider_response:provider-hash" in failure_evidence["evidence_refs"]
    assert "native_tool_call:tool-envelope-1" in failure_evidence["evidence_refs"]


def test_tool_dispatch_dropped_anomaly_from_sources_owns_native_fact_projection() -> None:
    anomaly = build_tool_dispatch_dropped_anomaly_from_sources(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        provider_response_hash="provider-hash",
        metadata={},
        native_tool_calls=[
            {"function": {"name": "write_file"}},
            {"function": {"name": "execute_command"}},
        ],
        native_tool_call_envelopes=[
            {"envelope_id": "tool-envelope-1", "tool_name": "write_file"},
            {"envelope_id": "tool-envelope-2", "tool_name": "execute_command"},
        ],
    )

    lifecycle = anomaly["tool_call_lifecycle_receipt"]
    assert anomaly["native_tool_calls_count"] == 2
    assert anomaly["native_tool_call_envelopes"] == lifecycle["native_tool_call_envelope_refs"]
    assert lifecycle["native_tool_calls_count"] == 2
    assert lifecycle["decoded_tool_calls_count"] == 2
    assert lifecycle["dispatched_tool_calls_count"] == 0
    assert (
        tool_dispatch_dropped_error_message(anomaly)
        == "tool_dispatch_dropped: provider emitted 2 tool call(s), but no executable tool batch was decoded"
    )


def test_tool_dispatch_dropped_anomaly_from_lifecycle_receipt_projects_counts() -> None:
    lifecycle = build_tool_batch_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        provider_response_hash="provider-response-hash",
        decoded_tool_calls_count=2,
        receipts=[],
        dropped_tool_calls=[
            {"tool_name": "write_file", "call_id": "call-1"},
            {"tool_name": "execute_command", "call_id": "call-2"},
        ],
        missing_receipt_reason="decoded_tool_batch_produced_no_authoritative_batch_receipt",
    ).to_dict()

    anomaly = build_tool_dispatch_dropped_anomaly_from_lifecycle_receipt(lifecycle)

    assert anomaly["type"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert anomaly["turn_id"] == "turn-1"
    assert anomaly["native_tool_calls_count"] == 2
    assert anomaly["decoded_tool_calls_count"] == 2
    assert anomaly["dispatched_tool_calls_count"] == 0
    assert anomaly["provider_response_hash"] == "provider-response-hash"
    assert anomaly["tool_call_lifecycle_receipt"] == lifecycle
    assert anomaly["failure_evidence"][0]["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value


def test_tool_lifecycle_receipt_derives_dropped_status_from_native_without_dispatch() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=0,
        dispatch_status="success",
        receipts=[],
    ).to_dict()

    assert receipt["ok"] is False
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value


def test_project_tool_lifecycle_event_centralizes_projection_shape() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        provider_response_hash="provider-hash",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=0,
        receipts=[],
        reason="native calls had no dispatch receipt",
    ).to_dict()

    event = project_tool_lifecycle_event(receipt, append_id="append-1", content_id="event-1")

    assert event["status"] == "dropped"
    assert event["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert event["failed"] is True
    assert event["dropped"] is True
    assert event["native_tool_calls_count"] == 1
    assert event["decoded_tool_calls_count"] == 1
    assert event["dispatched_tool_calls_count"] == 0
    assert event["provider_response_hash"] == "provider-hash"
    assert event["append_id"] == "append-1"
    assert event["content_id"] == "event-1"
    assert event["task_key"] == "TASK-1"
    assert event["task_identity_source"] == "task_id"
    assert event["failure_evidence"]["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert "provider_response:provider-hash" in event["failure_evidence"]["evidence_refs"]
    assert event["receipt"]["schema_version"] == "tool_call_lifecycle_receipt.v1"


def test_summarize_tool_lifecycle_events_centralizes_projection_totals() -> None:
    dropped_receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        provider_response_hash="provider-hash",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=0,
        native_tool_call_envelopes=[
            {
                "schema_version": "native_tool_call_envelope.v1",
                "envelope_id": "native-tool-1",
                "tool_name": "write_file",
            }
        ],
        receipts=[],
        reason="native calls had no dispatch receipt",
    ).to_dict()
    dispatched_receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-2",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "tool_name": "execute_command",
                        "status": "success",
                    }
                ],
            }
        ],
    ).to_dict()

    summary = summarize_tool_lifecycle_events(
        [
            project_tool_lifecycle_event(dropped_receipt, append_id="append-1", content_id="event-1"),
            project_tool_lifecycle_event(dispatched_receipt, append_id="append-2", content_id="event-2"),
        ]
    )

    assert summary["ok"] is True
    assert summary["event_count"] == 2
    assert summary["native_tool_calls_count"] == 2
    assert summary["decoded_tool_calls_count"] == 2
    assert summary["dispatched_tool_calls_count"] == 1
    assert summary["tool_result_count"] == 1
    assert summary["native_tool_call_names"] == ["write_file"]
    assert summary["dropped_count"] == 1
    assert summary["failed_count"] == 1
    assert summary["unresolved_count"] == 0
    assert summary["unresolved_dropped_count"] == 0
    assert summary["unresolved_failed_count"] == 0
    assert summary["failure_evidence"][0]["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert [event["content_id"] for event in summary["events"]] == ["event-1", "event-2"]


def test_project_tool_lifecycle_failure_status_centralizes_failure_precedence() -> None:
    dropped_receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=0,
        receipts=[],
        reason="native calls had no dispatch receipt",
    ).to_dict()
    missing_effect_receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-2",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "tool_name": "write_file",
                        "status": "success",
                    }
                ],
            }
        ],
        reason="write result had no effect receipt",
    ).to_dict()

    summary = summarize_tool_lifecycle_events(
        [
            project_tool_lifecycle_event(missing_effect_receipt, content_id="event-1"),
            project_tool_lifecycle_event(dropped_receipt, content_id="event-2"),
        ]
    )

    failure_status = project_tool_lifecycle_failure_status(summary)

    assert failure_status == {
        "failed": True,
        "status": "dropped",
        "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        "reason": "native calls had no dispatch receipt",
        "degraded": False,
        "fallback": "",
    }


def test_project_tool_lifecycle_failure_status_reports_non_dropped_failure() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "tool_name": "write_file",
                        "status": "success",
                    }
                ],
            }
        ],
        reason="write result had no effect receipt",
    ).to_dict()

    summary = summarize_tool_lifecycle_events([project_tool_lifecycle_event(receipt)])

    failure_status = project_tool_lifecycle_failure_status(summary)

    assert failure_status == {
        "failed": True,
        "status": "blocked",
        "failure_class": FailureClassV1.MISSING_EFFECT_RECEIPT.value,
        "reason": "write result had no effect receipt",
        "degraded": False,
        "fallback": "",
    }
    assert project_tool_lifecycle_failure_status(empty_tool_lifecycle_summary()) == {
        "failed": False,
        "status": "",
        "failure_class": "",
        "reason": "",
        "degraded": False,
        "fallback": "",
    }


def test_project_tool_lifecycle_failure_status_tool_result_failed_is_recoverable_not_integrity() -> None:
    """M08 fix: a tool that RAN and returned ok=False (TOOL_RESULT_FAILED) is a
    product-quality defect caught by real_run_gate/delivery_depth, NOT a control-
    plane integrity break.

    Per the M03 tool-denial blueprint ('a single per-tool failure should not
    equal ledger-integrity failure'), only MISSING/DROPPED/missing-effect
    LIFECYCLE evidence breaks canonical_execution. L1-01 r24 was
    DELIVERY_VERIFIED_CHAIN_CONTROL_PLANE_FAIL: product chain verified, but one
    tool's recoverable CAS race (deo_inventory_ready_failed:guarded_receipt_mismatch,
    classified TOOL_RESULT_FAILED) broke canonical. R195+Layer 2 made specific
    denial modes non-fatal at the tool surface; this M08 projection fix extends
    the separation to ALL per-tool execution failures (TOOL_RESULT_FAILED).
    """
    failed_receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-1",
                "failure_count": 1,
                "results": [
                    {
                        "tool_name": "write_file",
                        "status": "failed",
                        "reason": "deo_inventory_ready_failed:guarded_receipt_mismatch",
                    }
                ],
            }
        ],
        reason="deo_inventory_ready_failed:guarded_receipt_mismatch",
    ).to_dict()

    summary = summarize_tool_lifecycle_events([project_tool_lifecycle_event(failed_receipt)])
    failure_status = project_tool_lifecycle_failure_status(summary)

    # TOOL_RESULT_FAILED is a recoverable per-tool execution failure, not an
    # integrity break. canonical_execution must stay green; the product defect
    # is caught by real_run_gate / delivery_depth on a separate plane.
    assert failure_status["failure_class"] == FailureClassV1.TOOL_RESULT_FAILED.value
    assert failure_status["failed"] is False


def test_merge_tool_lifecycle_summaries_centralizes_multi_project_projection() -> None:
    failure_evidence = {"failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value}
    merged = merge_tool_lifecycle_summaries(
        [
            {
                "tool_lifecycle": {
                    "ok": True,
                    "event_count": 1,
                    "native_tool_calls_count": 1,
                    "decoded_tool_calls_count": 1,
                    "dispatched_tool_calls_count": 1,
                    "tool_result_count": 1,
                    "effect_receipt_count": 1,
                    "native_tool_call_names": ["read_file"],
                    "dropped_count": 0,
                    "failed_count": 0,
                    "failure_evidence": [],
                    "events": [{"content_id": "event-1"}],
                }
            },
            {
                "tool_lifecycle": {
                    "ok": False,
                    "event_count": 1,
                    "native_tool_calls_count": 2,
                    "decoded_tool_calls_count": 2,
                    "dispatched_tool_calls_count": 0,
                    "tool_result_count": 0,
                    "effect_receipt_count": 0,
                    "native_tool_call_names": ["write_file", "read_file"],
                    "dropped_count": 1,
                    "failed_count": 1,
                    "failure_evidence": [failure_evidence],
                    "events": [{"content_id": "event-2"}],
                }
            },
        ]
    )

    assert merged["ok"] is False
    assert merged["event_count"] == 2
    assert merged["native_tool_calls_count"] == 3
    assert merged["decoded_tool_calls_count"] == 3
    assert merged["dispatched_tool_calls_count"] == 1
    assert merged["tool_result_count"] == 1
    assert merged["effect_receipt_count"] == 1
    assert merged["native_tool_call_names"] == ["read_file", "write_file"]
    assert merged["dropped_count"] == 1
    assert merged["failed_count"] == 1
    assert merged["failure_evidence"] == [failure_evidence]
    assert [event["content_id"] for event in merged["events"]] == ["event-1", "event-2"]


def test_merge_tool_lifecycle_does_not_mask_project_missing_required_events() -> None:
    successful = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [{"tool_name": "read_file", "status": "success"}],
            }
        ],
    ).to_dict()
    successful_summary = summarize_tool_lifecycle_events([project_tool_lifecycle_event(successful)])

    merged = merge_tool_lifecycle_summaries(
        [
            {"tool_lifecycle": successful_summary},
            {"tool_lifecycle": empty_tool_lifecycle_summary(requirement=True)},
        ]
    )

    assert merged["ok"] is False
    assert merged["requirement_status"] == "missing_required"


def test_merge_tool_lifecycle_accepts_explicit_not_required_project() -> None:
    merged = merge_tool_lifecycle_summaries([{"tool_lifecycle": empty_tool_lifecycle_summary(requirement=False)}])

    assert merged["ok"] is True
    assert merged["requirement"] is False
    assert merged["requirement_status"] == "not_required"


def test_empty_tool_lifecycle_summary_matches_public_projection_shape() -> None:
    assert empty_tool_lifecycle_summary() == {
        "ok": True,
        "requirement": False,
        "requirement_status": "not_required",
        "event_count": 0,
        "native_tool_calls_count": 0,
        "decoded_tool_calls_count": 0,
        "dispatched_tool_calls_count": 0,
        "tool_result_count": 0,
        "effect_receipt_count": 0,
        "native_tool_call_names": [],
        "dropped_count": 0,
        "failed_count": 0,
        "failure_evidence": [],
        "events": [],
        "requirement_projection": {
            "schema_version": "polaris.tool_lifecycle_requirement.v1",
            "required": False,
            "state": "not_required",
            "required_task_keys": [],
            "missing_required_task_keys": [],
            "obligations": [],
        },
        "required_task_keys": [],
        "missing_required_task_keys": [],
        "latest_by_task": {},
        "unresolved_by_task": {},
        "unresolved_count": 0,
        "unresolved_dropped_count": 0,
        "unresolved_failed_count": 0,
        "outcome_projection": {
            "schema_version": "polaris.tool_lifecycle_outcome_projection.v1",
            "source": "event_rows",
            "degraded": False,
            "fallback": "",
            "requirement": False,
            "requirement_status": "not_required",
        },
    }


def test_empty_tool_lifecycle_requires_explicit_not_required_declaration() -> None:
    summary = summarize_tool_lifecycle_events([], requirement=False)

    assert summary["ok"] is True
    assert summary["requirement"] is False
    assert summary["requirement_status"] == "not_required"
    assert summary["event_count"] == 0
    assert project_tool_lifecycle_failure_status(summary)["failed"] is False


def test_empty_tool_lifecycle_fails_closed_when_requirement_is_explicit() -> None:
    summary = summarize_tool_lifecycle_events([], requirement=True)

    assert summary["ok"] is False
    assert summary["requirement"] is True
    assert summary["requirement_status"] == "missing_required"
    assert project_tool_lifecycle_failure_status(summary) == {
        "failed": True,
        "status": "missing_required",
        "failure_class": FailureClassV1.TOOL_LIFECYCLE_MISSING.value,
        "reason": "required tool lifecycle evidence is missing",
        "degraded": False,
        "fallback": "",
    }


def test_run_ledger_projection_does_not_require_lifecycle_without_execution_fact() -> None:
    projection = build_run_ledger_projection([])

    assert projection["tool_lifecycle"]["ok"] is True
    assert projection["tool_lifecycle"]["requirement"] is False
    assert projection["tool_lifecycle"]["requirement_status"] == "not_required"


def test_job_token_capability_does_not_activate_lifecycle_requirement() -> None:
    projection = build_run_ledger_projection(
        [
            {
                "event_type": "gate_evaluated",
                "gate": {"name": "chief_engineer_review", "ok": False},
                "job_token": {
                    "token_id": "token-1",
                    "target_files": ["src/main.py"],
                    "allowed_write_paths": ["src/main.py"],
                    "capability_audit": {"ok": True, "issues": []},
                },
                "physical_evidence": {},
            }
        ]
    )

    lifecycle = projection["tool_lifecycle"]
    assert lifecycle["requirement"] is False
    assert lifecycle["requirement_status"] == "not_required"


def test_run_ledger_projection_fails_closed_after_structured_requirement() -> None:
    requirement_event = build_tool_lifecycle_requirement_run_ledger_event(
        ToolLifecycleRequirementV1(task_id="TASK-1", run_id="run-1")
    )

    projection = build_run_ledger_projection([requirement_event])

    lifecycle = projection["tool_lifecycle"]
    assert lifecycle["ok"] is False
    assert lifecycle["requirement"] is True
    assert lifecycle["requirement_status"] == "missing_required"
    assert lifecycle["required_task_keys"] == ["TASK-1"]
    assert lifecycle["missing_required_task_keys"] == ["TASK-1"]


def test_run_ledger_projection_requires_receipt_for_each_required_task() -> None:
    requirement_events = [
        build_tool_lifecycle_requirement_run_ledger_event(ToolLifecycleRequirementV1(task_id=task_id, run_id="run-1"))
        for task_id in ("TASK-1", "TASK-2")
    ]
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[{"batch_id": "batch-1", "results": [{"status": "success"}]}],
    ).to_dict()
    lifecycle_event = build_tool_call_lifecycle_run_ledger_event(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        lifecycle_receipt=receipt,
    )

    projection = build_run_ledger_projection([*requirement_events, lifecycle_event])

    lifecycle = projection["tool_lifecycle"]
    assert lifecycle["ok"] is False
    assert lifecycle["required_task_keys"] == ["TASK-1", "TASK-2"]
    assert lifecycle["missing_required_task_keys"] == ["TASK-2"]


def test_tool_lifecycle_same_task_result_failures_are_resolved_by_later_success() -> None:
    def receipt(*, turn_id: str, failure_count: int = 0) -> dict[str, Any]:
        return build_tool_call_lifecycle_receipt(
            run_id="run-r26",
            task_id="TASK-1",
            turn_id=turn_id,
            role="director",
            native_tool_calls_count=1,
            decoded_tool_calls_count=1,
            dispatched_tool_calls_count=1,
            receipts=[
                {
                    "batch_id": f"batch-{turn_id}",
                    "failure_count": failure_count,
                    "results": [{"tool_name": "read_file", "status": "success"}],
                }
            ],
            reason="tool result failed" if failure_count else "",
        ).to_dict()

    summary = summarize_tool_lifecycle_events(
        [
            project_tool_lifecycle_event(receipt(turn_id="turn-1")),
            project_tool_lifecycle_event(receipt(turn_id="turn-2", failure_count=1)),
            project_tool_lifecycle_event(receipt(turn_id="turn-3", failure_count=1)),
            project_tool_lifecycle_event(receipt(turn_id="turn-4")),
        ]
    )

    assert summary["failed_count"] == 2
    assert [event["failure_class"] for event in summary["events"]] == [
        "",
        FailureClassV1.TOOL_RESULT_FAILED.value,
        FailureClassV1.TOOL_RESULT_FAILED.value,
        "",
    ]
    assert summary["unresolved_failed_count"] == 0
    assert summary["unresolved_dropped_count"] == 0
    assert summary["unresolved_by_task"] == {}
    assert summary["ok"] is True
    assert project_tool_lifecycle_failure_status(summary)["failed"] is False
    merged = merge_tool_lifecycle_summaries([{"tool_lifecycle": summary}])
    assert merged["ok"] is True
    assert merged["outcome_projection"]["degraded"] is False


def test_tool_lifecycle_success_for_another_task_does_not_resolve_failure() -> None:
    failed = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-FAILED",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[{"batch_id": "batch-1", "failure_count": 1, "results": [{"status": "failed"}]}],
        reason="tool result failed",
    ).to_dict()
    success = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-SUCCEEDED",
        turn_id="turn-2",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[{"batch_id": "batch-2", "results": [{"status": "success"}]}],
    ).to_dict()

    summary = summarize_tool_lifecycle_events(
        [project_tool_lifecycle_event(failed), project_tool_lifecycle_event(success)]
    )

    assert summary["ok"] is False
    assert set(summary["latest_by_task"]) == {"TASK-FAILED", "TASK-SUCCEEDED"}
    assert set(summary["unresolved_by_task"]) == {"TASK-FAILED"}
    assert summary["unresolved_failed_count"] == 1


def test_tool_lifecycle_p0_dropped_isolated_from_later_task_success() -> None:
    dropped = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-DROPPED",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=0,
        receipts=[],
        reason="dispatch dropped",
    ).to_dict()
    success = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-SUCCEEDED",
        turn_id="turn-2",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[{"batch_id": "batch-2", "results": [{"status": "success"}]}],
    ).to_dict()

    summary = summarize_tool_lifecycle_events(
        [project_tool_lifecycle_event(dropped), project_tool_lifecycle_event(success)]
    )
    status = project_tool_lifecycle_failure_status(summary)

    assert summary["unresolved_dropped_count"] == 1
    assert set(summary["unresolved_by_task"]) == {"TASK-DROPPED"}
    assert status["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value


def test_project_tool_lifecycle_failure_status_marks_legacy_fallback_degraded() -> None:
    status = project_tool_lifecycle_failure_status(
        {
            "ok": False,
            "dropped_count": 0,
            "failed_count": 1,
            "events": [
                {
                    "task_id": "TASK-1",
                    "failed": True,
                    "status": "failed",
                    "failure_class": FailureClassV1.TOOL_RESULT_FAILED.value,
                    "reason": "structured legacy receipt failed",
                }
            ],
        }
    )

    # M08 fix: TOOL_RESULT_FAILED (tool ran, returned ok=False) is a recoverable
    # per-tool execution failure / product-quality defect, NOT a control-plane
    # integrity break; canonical_execution stays green, degraded/fallback still
    # projected. (Previously failed:True.)
    assert status["failed"] is False
    assert status["degraded"] is True
    assert status["fallback"] == "legacy_event_rows"


def test_task_boundary_projection_retains_only_latest_failure_per_task() -> None:
    projection = build_run_ledger_projection(
        [
            {
                "event_type": "task_boundary_verdict",
                "task_boundary_verdict": {
                    "task_id": "TASK-1",
                    "status": "incomplete_materialization",
                    "ok": False,
                    "failure_class": "INCOMPLETE_MATERIALIZATION",
                },
            },
            {
                "event_type": "task_boundary_verdict",
                "task_boundary_verdict": {
                    "task_id": "TASK-1",
                    "status": "completed_verified",
                    "ok": True,
                    "failure_class": "PASSED",
                },
            },
            {
                "event_type": "task_boundary_verdict",
                "task_boundary_verdict": {
                    "task_id": "TASK-2",
                    "status": "incomplete_materialization",
                    "ok": False,
                    "failure_class": "INCOMPLETE_MATERIALIZATION",
                },
            },
            {
                "event_type": "task_boundary_verdict",
                "task_boundary_verdict": {
                    "task_id": "TASK-3",
                    "status": "completed_verified",
                    "ok": True,
                    "failure_class": "PASSED",
                },
            },
        ]
    )

    task_boundary = projection["task_boundary"]
    assert task_boundary["verdict_count"] == 4
    assert task_boundary["historical_failed_count"] == 2
    assert task_boundary["latest"]["task_id"] == "TASK-3"
    assert task_boundary["latest_by_task"]["TASK-1"]["ok"] is True
    assert [verdict["task_id"] for verdict in task_boundary["failed"]] == ["TASK-2"]
    assert task_boundary["ok"] is False


def test_tool_lifecycle_receipt_derives_counts_from_dropped_tool_details() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        dispatched_tool_calls_count=0,
        dropped_tool_calls=[{"tool_name": "write_file", "reason": "tool_dispatch_dropped"}],
        receipts=[],
    ).to_dict()

    assert receipt["native_tool_calls_count"] == 1
    assert receipt["decoded_tool_calls_count"] == 1
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert receipt["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "reason": "tool_dispatch_dropped",
        }
    ]


def test_tool_lifecycle_receipt_derives_counts_from_count_only_dropped_ref() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        dispatched_tool_calls_count=0,
        dropped_tool_calls=[{"count": 4, "reason": "native_tool_calls_without_dispatch"}],
        receipts=[],
    ).to_dict()

    assert receipt["native_tool_calls_count"] == 4
    assert receipt["decoded_tool_calls_count"] == 4
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert receipt["dropped_tool_calls"] == [
        {
            "count": 4,
            "reason": "native_tool_calls_without_dispatch",
        }
    ]


def test_build_missing_dispatch_lifecycle_receipt_projects_required_write_tool() -> None:
    receipt = build_missing_dispatch_lifecycle_receipt(
        required_write_tools=["read_file", "write_file", "write_file"],
        metadata_candidates=(),
        tool_results=[],
        batch_receipt=None,
    )

    assert receipt is not None
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert receipt["ok"] is False
    assert receipt["native_tool_calls_count"] == 1
    assert receipt["decoded_tool_calls_count"] == 1
    assert receipt["dispatched_tool_calls_count"] == 0
    assert receipt["dropped_tool_calls"] == [
        {"tool_name": "write_file", "reason": "tool_dispatch_dropped"},
    ]
    assert receipt["reason"] == "required_write_tool_without_dispatch_evidence"


def test_claimed_materialization_without_tools_seals_lifecycle_not_missing() -> None:
    """R137: claimed materialization with zero tools must not project TOOL_LIFECYCLE_MISSING."""

    sealed = build_claimed_materialization_without_tool_lifecycle_receipt(
        run_id="director-task3",
        task_id="TASK-3",
        turn_id="director-task3--TASK-3--attempt-txi_1-0",
        reason="director_no_materialized_changes",
        failure_class=FailureClassV1.INCOMPLETE_MATERIALIZATION.value,
    )
    assert sealed["dispatch_status"] == "blocked"
    assert sealed["ok"] is False
    assert sealed["failure_class"] == FailureClassV1.INCOMPLETE_MATERIALIZATION.value
    assert sealed["reason"] == "director_no_materialized_changes"
    assert sealed["native_tool_calls_count"] == 0
    assert sealed["dispatched_tool_calls_count"] == 0
    assert sealed["task_id"] == "TASK-3"

    success_task1 = build_tool_call_lifecycle_receipt(
        run_id="director-task1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "b1",
                "results": [
                    {
                        "tool_name": "read_file",
                        "status": "success",
                        "call_id": "c1",
                    }
                ],
            }
        ],
        dispatch_status="dispatched",
    )
    success_task2 = build_tool_call_lifecycle_receipt(
        run_id="director-task2",
        task_id="TASK-2",
        turn_id="turn-2",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "b2",
                "results": [
                    {
                        "tool_name": "read_file",
                        "status": "success",
                        "call_id": "c2",
                    }
                ],
            }
        ],
        dispatch_status="dispatched",
    )
    events = [
        project_tool_lifecycle_event(success_task1.to_dict()),
        project_tool_lifecycle_event(success_task2.to_dict()),
        project_tool_lifecycle_event(sealed),
    ]
    requirement = ToolLifecycleRequirementV1(
        task_id="TASK-3",
        run_id="director-task3",
        reason="director_materialization_claimed",
    )
    # Three claimed tasks; only TASK-3 previously missing.
    requirement_projection = {
        "schema_version": "polaris.tool_lifecycle_requirement.v1",
        "required": True,
        "required_task_keys": ["TASK-1", "TASK-2", "TASK-3"],
        "obligations": [
            requirement.to_dict(),
            {"task_key": "TASK-1", "task_id": "TASK-1", "run_id": "director-task1"},
            {"task_key": "TASK-2", "task_id": "TASK-2", "run_id": "director-task2"},
        ],
    }
    summary = summarize_tool_lifecycle_events(
        events,
        requirement=True,
        requirement_projection=requirement_projection,
    )
    assert summary["missing_required_task_keys"] == []
    assert summary["requirement_status"] != "missing_required"
    assert "TASK-3" in summary["latest_by_task"]
    # Terminal incomplete seal satisfies integrity (not missing / not open gap).
    assert summary["ok"] is True
    assert summary["unresolved_count"] == 0
    failure_status = project_tool_lifecycle_failure_status(summary)
    assert failure_status["failed"] is True
    assert failure_status["failure_class"] != FailureClassV1.TOOL_LIFECYCLE_MISSING.value
    assert failure_status["failure_class"] == FailureClassV1.INCOMPLETE_MATERIALIZATION.value


def test_build_missing_dispatch_lifecycle_receipt_prefers_native_envelope_metadata() -> None:
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native_tool_call:provider:0:call-1:hash",
        "tool_name": "write_file",
    }
    receipt = build_missing_dispatch_lifecycle_receipt(
        required_write_tools=["write_file"],
        metadata_candidates=({"native_tool_call_envelope_refs": [envelope]},),
        tool_results=[],
        batch_receipt=None,
    )

    assert receipt is not None
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert receipt["ok"] is False
    assert receipt["native_tool_calls_count"] == 1
    assert receipt["native_tool_call_envelope_refs"] == [envelope]
    assert receipt["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "reason": "tool_dispatch_dropped",
            "envelope_id": "native_tool_call:provider:0:call-1:hash",
        },
    ]


def test_build_missing_dispatch_lifecycle_receipt_seals_native_write_without_required_list() -> None:
    """R133: native write envelopes seal dropped lifecycle even if required_tools is empty."""
    envelopes = [
        {
            "schema_version": "native_tool_call_envelope.v1",
            "envelope_id": "native_tool_call:anthropic:0:call_a:hash_a",
            "tool_name": "write_file",
        },
        {
            "schema_version": "native_tool_call_envelope.v1",
            "envelope_id": "native_tool_call:anthropic:1:call_b:hash_b",
            "tool_name": "write_file",
        },
        {
            "schema_version": "native_tool_call_envelope.v1",
            "envelope_id": "native_tool_call:anthropic:2:call_c:hash_c",
            "tool_name": "read_file",
        },
    ]
    receipt = build_missing_dispatch_lifecycle_receipt(
        required_write_tools=[],
        metadata_candidates=(
            {
                "native_tool_calls_count": 3,
                "native_tool_call_envelopes": envelopes,
            },
        ),
        tool_results=[],
        batch_receipt=None,
    )

    assert receipt is not None
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert receipt["ok"] is False
    assert receipt["reason"] == "native_tool_calls_without_dispatch"
    assert receipt["native_tool_calls_count"] == 3
    assert receipt["dispatched_tool_calls_count"] == 0
    assert any(item.get("tool_name") == "write_file" for item in receipt.get("dropped_tool_calls") or [])


def test_build_missing_dispatch_lifecycle_receipt_ignores_read_only_native_without_required() -> None:
    receipt = build_missing_dispatch_lifecycle_receipt(
        required_write_tools=[],
        metadata_candidates=(
            {
                "native_tool_call_envelopes": [
                    {
                        "schema_version": "native_tool_call_envelope.v1",
                        "envelope_id": "native_tool_call:anthropic:0:call_r:hash_r",
                        "tool_name": "read_file",
                    }
                ],
            },
        ),
        tool_results=[],
        batch_receipt=None,
    )
    assert receipt is None


def test_build_missing_dispatch_lifecycle_receipt_skips_existing_dispatch_evidence() -> None:
    assert (
        build_missing_dispatch_lifecycle_receipt(
            required_write_tools=["write_file"],
            tool_results=[{"tool": "write_file", "ok": True}],
            batch_receipt=None,
        )
        is None
    )
    assert (
        build_missing_dispatch_lifecycle_receipt(
            required_write_tools=["write_file"],
            tool_results=[],
            batch_receipt={"results": [{"tool": "write_file", "ok": True}]},
        )
        is None
    )


def test_build_missing_dispatch_lifecycle_receipt_reuses_public_batch_evidence_keys() -> None:
    batch_receipts = [
        {"results": [{"tool_name": "write_file", "status": "success"}]},
        {"raw_results": [{"tool_name": "write_file", "status": "success"}]},
        {"effect_receipts": [{"operation": "write_file", "file": "src/main.py"}]},
    ]

    for batch_receipt in batch_receipts:
        assert batch_receipt_has_dispatch_evidence(batch_receipt) is True
        assert (
            build_missing_dispatch_lifecycle_receipt(
                required_write_tools=["write_file"],
                tool_results=[],
                batch_receipt=batch_receipt,
            )
            is None
        )


def test_batch_receipt_has_dispatch_evidence_owns_receipt_key_set() -> None:
    assert batch_receipt_has_dispatch_evidence({"results": [{"tool": "write_file"}]}) is True
    assert batch_receipt_has_dispatch_evidence({"raw_results": [{"tool": "write_file"}]}) is True
    assert batch_receipt_has_dispatch_evidence({"effect_receipts": [{"file": "src/main.py"}]}) is True
    assert batch_receipt_has_dispatch_evidence({"results": []}) is False
    assert batch_receipt_has_dispatch_evidence({"unrelated": [{"tool": "write_file"}]}) is False
    assert batch_receipt_has_dispatch_evidence(None) is False


def test_effect_receipts_from_batch_receipts_owns_receipt_key_set() -> None:
    top_level = {"operation": "top-level", "file": "src/top.py"}
    result_direct = {"operation": "result-direct", "file": "src/direct.py"}
    result_nested = {"operation": "result-nested", "file": "src/nested.py"}
    raw_direct = {"operation": "raw-direct", "file": "src/raw-direct.py"}
    raw_nested = {"operation": "raw-nested", "file": "src/raw-nested.py"}

    receipts = effect_receipts_from_batch_receipts(
        [
            None,
            "invalid",
            {
                "effect_receipts": [top_level, "invalid", None],
                "results": [
                    {"effect_receipt": result_direct},
                    {"result": {"effect_receipt": result_nested}},
                ],
                "raw_results": [
                    {"effect_receipt": raw_direct},
                    {"result": {"effect_receipt": raw_nested}},
                    {"result": {"effect_receipt": ["invalid"]}},
                ],
            },
        ]
    )

    assert receipts == [
        top_level,
        result_direct,
        result_nested,
        raw_direct,
        raw_nested,
    ]
    sources = [top_level, result_direct, result_nested, raw_direct, raw_nested]
    assert all(receipt is not source for receipt, source in zip(receipts, sources, strict=True))


def test_build_tool_call_lifecycle_run_ledger_event_normalizes_receipt_and_job_token() -> None:
    event = build_tool_call_lifecycle_run_ledger_event(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        lifecycle_receipt={
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 1,
            "dispatched_tool_calls_count": 0,
            "dispatch_status": "dropped",
            "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        },
        stage="director_tool_dispatch",
        ok=False,
    )

    assert event["event_type"] == "tool_call_lifecycle"
    assert event["stage"] == "director_tool_dispatch"
    assert event["run_id"] == "run-1"
    assert event["task_id"] == "TASK-1"
    assert event["job_token"] == {
        "run_id": "run-1",
        "task_id": "TASK-1",
        "project_id": "TASK-1",
        "capability_audit": {"ok": True, "issues": []},
        "gate_policy": {},
    }
    receipt = event["tool_call_lifecycle_receipt"]
    assert receipt["run_id"] == "run-1"
    assert receipt["task_id"] == "TASK-1"
    assert receipt["turn_id"] == "turn-1"
    assert receipt["role"] == "director"
    assert receipt["ok"] is False
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value


def test_build_tool_call_lifecycle_run_ledger_event_preserves_supplied_job_token() -> None:
    event = build_tool_call_lifecycle_run_ledger_event(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        lifecycle_receipt={"schema_version": "tool_call_lifecycle_receipt.v1", "ok": True},
        stage="tool_batch",
        job_token={
            "schema_version": 1,
            "source": "control_plane.job_token",
            "token_id": "token-1",
            "run_id": "",
            "task_id": "",
            "project_id": "",
            "stage": "",
            "contract_hash": "contract-hash",
            "blueprint_hash": "blueprint-hash",
            "execution_envelope_hash": "envelope-hash",
            "capability_audit": {"ok": True, "issues": []},
            "gate_policy": {"enabled_evidence_modalities": ["tool_receipt"]},
        },
    )

    assert event["stage"] == "tool_batch"
    assert event["job_token"]["token_id"] == "token-1"
    assert event["job_token"]["run_id"] == "run-1"
    assert event["job_token"]["task_id"] == "TASK-1"
    assert event["job_token"]["project_id"] == "TASK-1"
    assert event["job_token"]["stage"] == "tool_batch"
    assert event["job_token"]["contract_hash"] == "contract-hash"
    assert event["job_token"]["blueprint_hash"] == "blueprint-hash"
    assert event["job_token"]["execution_envelope_hash"] == "envelope-hash"


def test_native_tool_call_facts_from_lifecycle_receipt_prefers_envelope_names() -> None:
    facts = native_tool_call_facts_from_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 9,
            "native_tool_call_envelope_refs": [
                {
                    "schema_version": "native_tool_call_envelope.v1",
                    "envelope_id": "native_tool_call:openai:0:call-0:abcdef",
                    "tool_name": "write_file",
                },
                {
                    "schema_version": "native_tool_call_envelope.v1",
                    "envelope_id": "native_tool_call:openai:1:call-1:abcdef",
                    "tool_name": "execute_command",
                },
            ],
            "dispatched_tool_calls_count": 0,
        }
    )

    assert facts == {
        "native_tool_calls_count": 2,
        "native_tool_call_names": ["write_file", "execute_command"],
    }


def test_native_tool_call_facts_from_lifecycle_receipt_uses_dropped_tool_names() -> None:
    facts = native_tool_call_facts_from_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "dropped_tool_calls": ["write_file", {"tool_name": "write_file"}, {"tool_name": "edit_file"}],
        }
    )

    assert facts == {
        "native_tool_calls_count": 3,
        "native_tool_call_names": ["write_file", "edit_file"],
    }


def test_native_tool_call_facts_from_raw_calls_owns_provider_aliases() -> None:
    facts = native_tool_call_facts_from_raw_calls(
        [
            {"function": {"name": "write_file", "arguments": {"file": "src/index.js"}}},
            {"toolName": "execute_command"},
            {"function_name": "repo_tree"},
            {"tool_name": ""},
            "not-a-call",
        ]
    )

    assert facts == {
        "native_tool_calls_count": 4,
        "native_tool_call_names": ["write_file", "execute_command", "repo_tree"],
    }


def test_native_tool_call_facts_from_sources_prefers_metadata() -> None:
    facts = native_tool_call_facts_from_sources(
        {
            "native_tool_call_envelopes": [
                {"envelope_id": "native-1", "tool_name": "write_file"},
                {"envelope_id": "native-2", "tool_name": "execute_command"},
            ],
        },
        [{"function": {"name": "ignored_raw_tool"}}],
    )

    assert facts == {
        "native_tool_calls_count": 2,
        "native_tool_call_names": ["write_file", "execute_command"],
    }


def test_native_tool_call_facts_from_sources_falls_back_to_raw_calls() -> None:
    facts = native_tool_call_facts_from_sources(
        {},
        [
            {"function": {"name": "write_file"}},
            {"toolName": "repo_tree"},
            "not-a-call",
        ],
    )

    assert facts == {
        "native_tool_calls_count": 2,
        "native_tool_call_names": ["write_file", "repo_tree"],
    }


def test_native_tool_call_facts_from_sources_accepts_legacy_numeric_metadata() -> None:
    facts = native_tool_call_facts_from_sources(
        {
            "native_tool_calls_count": 2,
            "native_tool_call_names": ["read_file", "write_file"],
        },
        [],
    )

    assert facts == {
        "native_tool_calls_count": 2,
        "native_tool_call_names": ["read_file", "write_file"],
    }


def test_project_native_tool_call_envelopes_to_metadata_projects_count_and_names() -> None:
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native-write",
        "tool_name": "write_file",
    }
    command_envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native-run",
        "tool_name": "execute_command",
    }
    metadata: dict[str, object] = {"native_tool_calls_count": 99, "native_tool_call_names": ["stale"]}

    project_native_tool_call_envelopes_to_metadata(
        metadata,
        [envelope, dict(envelope), command_envelope, "not-an-envelope"],
    )

    assert metadata == {
        "native_tool_call_envelopes": [envelope, command_envelope],
        "native_tool_calls_count": 2,
        "native_tool_call_names": ["write_file", "execute_command"],
    }


def test_project_native_tool_call_facts_to_metadata_overwrites_stale_projection() -> None:
    metadata = {"native_tool_calls_count": 9, "native_tool_call_names": ["stale_tool"]}

    project_native_tool_call_facts_to_metadata(
        metadata,
        {
            "native_tool_calls_count": 2,
            "native_tool_call_names": ["write_file", "", "execute_command"],
        },
    )

    assert metadata == {
        "native_tool_calls_count": 2,
        "native_tool_call_names": ["write_file", "execute_command"],
    }


def test_native_tool_call_names_from_facts_owns_name_coercion() -> None:
    assert native_tool_call_names_from_facts(
        {
            "native_tool_calls_count": 3,
            "native_tool_call_names": ["", " write_file ", None, "execute_command"],
        }
    ) == ["write_file", "execute_command"]
    assert native_tool_call_names_from_facts({}, fallback=(" repo_tree ", "", None)) == ["repo_tree"]


def test_project_native_tool_call_facts_to_metadata_can_preserve_names() -> None:
    metadata = {"native_tool_calls_count": 9, "native_tool_call_names": ["stale_tool"]}

    project_native_tool_call_facts_to_metadata(
        metadata,
        {"native_tool_calls_count": 0, "native_tool_call_names": []},
        project_names=False,
    )

    assert metadata == {
        "native_tool_calls_count": 0,
        "native_tool_call_names": ["stale_tool"],
    }


def test_project_native_tool_call_facts_from_evidence_to_metadata_uses_lifecycle_evidence() -> None:
    metadata = {"native_tool_calls_count": 9, "native_tool_call_names": ["stale_tool"]}
    evidence = {
        "tool_call_lifecycle_receipt": {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_call_envelope_refs": [
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "read_file"},
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
            ],
        }
    }

    project_native_tool_call_facts_from_evidence_to_metadata(metadata, evidence)

    assert metadata == {
        "native_tool_calls_count": 2,
        "native_tool_call_names": ["read_file", "write_file"],
    }


def test_project_native_tool_call_facts_from_evidence_to_metadata_ignores_missing_evidence() -> None:
    metadata = {"native_tool_calls_count": 9, "native_tool_call_names": ["stale_tool"]}

    project_native_tool_call_facts_from_evidence_to_metadata(metadata, {})

    assert metadata == {"native_tool_calls_count": 9, "native_tool_call_names": ["stale_tool"]}


def test_project_completion_audit_evidence_to_metadata_projects_lifecycle_facts() -> None:
    metadata: dict[str, object] = {}
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native-completion-write",
        "tool_name": "write_file",
    }

    project_completion_audit_evidence_to_metadata(
        metadata,
        {
            "required_tools": ["write_file"],
            "native_tool_calls_count": 9,
            "native_tool_call_names": ["stale_tool"],
            "tool_call_lifecycle_receipt": {
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "native_tool_call_envelope_refs": [envelope],
            },
        },
    )

    assert metadata["required_tools"] == ["write_file"]
    assert metadata["native_tool_calls_count"] == 1
    assert metadata["native_tool_call_names"] == ["write_file"]
    lifecycle = cast(dict[str, Any], metadata["tool_call_lifecycle_receipt"])
    failure_evidence_rows = cast(list[dict[str, Any]], metadata["failure_evidence"])
    assert lifecycle["native_tool_call_envelope_refs"] == [envelope]
    assert failure_evidence_rows[0]["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value


def test_project_completion_audit_evidence_to_metadata_preserves_direct_failure_evidence() -> None:
    metadata: dict[str, object] = {}
    failure_evidence = [
        {
            "schema_version": "failure_evidence.v1",
            "failure_class": "TOOL_RESULT_FAILED",
            "responsible_layer": "tool_executor",
        }
    ]

    project_completion_audit_evidence_to_metadata(
        metadata,
        {
            "native_tool_calls_count": 2,
            "native_tool_call_names": ["read_file"],
            "failure_evidence": failure_evidence,
            "failure_evidence_summary": {"count": 1, "latest_failure_class": "TOOL_RESULT_FAILED"},
        },
    )

    assert metadata["native_tool_calls_count"] == 2
    assert metadata["native_tool_call_names"] == ["read_file"]
    assert metadata["failure_evidence"] == failure_evidence
    assert metadata["failure_evidence_summary"] == {"count": 1, "latest_failure_class": "TOOL_RESULT_FAILED"}


def test_project_completion_audit_evidence_to_metadata_can_overwrite_stale_native_facts() -> None:
    metadata: dict[str, object] = {
        "native_tool_calls_count": 9,
        "native_tool_call_names": ["stale_tool"],
    }

    project_completion_audit_evidence_to_metadata(
        metadata,
        {
            "native_tool_calls_count": 1,
            "native_tool_call_names": ["write_file"],
        },
        overwrite_native_facts=True,
    )

    assert metadata["native_tool_calls_count"] == 1
    assert metadata["native_tool_call_names"] == ["write_file"]


def test_project_completion_dispatch_evidence_keeps_native_envelope_refs() -> None:
    metadata: dict[str, object] = {
        "native_tool_call_envelopes": ["bad legacy projection"],
    }
    decision_metadata = {
        "native_tool_call_envelope_refs": [
            {
                "schema_version": "native_tool_call_envelope.v1",
                "envelope_id": "native_tool_call:openai:0:call-1:abcdef",
                "tool_name": "write_file",
            },
            {
                "schema_version": "native_tool_call_envelope.v1",
                "envelope_id": "native_tool_call:openai:1:call-2:abcdef",
                "tool_name": "execute_command",
            },
        ],
        "tool_call_lifecycle_receipt": {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "dispatch_status": "dropped",
            "failure_class": "tool_dispatch_dropped",
            "native_tool_calls_count": 2,
        },
    }
    usage_metadata = {
        "final_request_context_audit": {"schema_version": "llm.final_request_context_audit.v1"},
        "required_tools": ["write_file"],
    }

    project_completion_dispatch_evidence_to_metadata(metadata, decision_metadata, usage_metadata)

    assert metadata["native_tool_call_envelope_refs"] == decision_metadata["native_tool_call_envelope_refs"]
    assert metadata["tool_call_lifecycle_receipt"] == decision_metadata["tool_call_lifecycle_receipt"]
    assert metadata["final_request_context_audit"] == usage_metadata["final_request_context_audit"]
    assert metadata["required_tools"] == ["write_file"]


def test_project_completion_dispatch_evidence_derives_refs_from_lifecycle_receipt() -> None:
    metadata: dict[str, object] = {}
    usage_metadata = {
        "tool_call_lifecycle_receipt": {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_call_envelope_refs": [
                {
                    "schema_version": "native_tool_call_envelope.v1",
                    "envelope_id": "native_tool_call:openai:0:call-1:abcdef",
                    "tool_name": "write_file",
                }
            ],
        }
    }

    project_completion_dispatch_evidence_to_metadata(metadata, usage_metadata)

    assert metadata["native_tool_call_envelope_refs"] == [
        {
            "schema_version": "native_tool_call_envelope.v1",
            "envelope_id": "native_tool_call:openai:0:call-1:abcdef",
            "tool_name": "write_file",
        }
    ]


def test_project_lifecycle_failure_evidence_to_metadata_appends_failed_lifecycle() -> None:
    metadata = {
        "failure_evidence": [
            {
                "schema_version": "failure_evidence.v1",
                "failure_class": "TOOL_RESULT_FAILED",
                "responsible_layer": "tool_executor",
            }
        ],
        "failure_evidence_summary": {"source": "previous_projection", "count": 1},
    }

    rows = project_lifecycle_failure_evidence_to_metadata(
        metadata,
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 1,
            "decoded_tool_calls_count": 1,
            "dispatched_tool_calls_count": 0,
            "dispatch_status": "dropped",
            "failure_class": "TOOL_DISPATCH_DROPPED",
            "reason": "provider emitted tool calls but none were dispatched",
        },
    )

    assert rows[-1]["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert metadata["failure_evidence_summary"] == {
        "source": "previous_projection",
        "count": 2,
        "latest_failure_class": "TOOL_DISPATCH_DROPPED",
    }


def test_project_lifecycle_failure_evidence_to_metadata_skips_success_lifecycle() -> None:
    metadata: dict[str, object] = {}

    rows = project_lifecycle_failure_evidence_to_metadata(
        metadata,
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "ok": True,
            "dispatch_status": "dispatched",
        },
    )

    assert rows == []
    assert metadata == {}


def test_project_tool_lifecycle_metadata_projects_canonical_receipt_failure_and_native_facts() -> None:
    metadata = {
        "tool_call_lifecycle_receipts": [
            {
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "native_tool_calls_count": 1,
                "decoded_tool_calls_count": 1,
                "dispatched_tool_calls_count": 0,
                "dispatch_status": "dropped",
                "failure_class": "TOOL_DISPATCH_DROPPED",
                "reason": "provider emitted tool calls but none were dispatched",
                "native_tool_call_envelope_refs": [
                    {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"}
                ],
            }
        ],
    }

    project_tool_lifecycle_metadata(metadata)

    lifecycle = cast(dict[str, Any], metadata["tool_call_lifecycle_receipt"])
    failure_evidence_rows = cast(list[dict[str, Any]], metadata["failure_evidence"])
    failure_summary = cast(dict[str, Any], metadata["failure_evidence_summary"])
    assert lifecycle["dispatch_status"] == "dropped"
    assert metadata["native_tool_calls_count"] == 1
    assert metadata["native_tool_call_names"] == ["write_file"]
    assert failure_evidence_rows[-1]["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert failure_summary["latest_failure_class"] == "TOOL_DISPATCH_DROPPED"


def test_project_tool_lifecycle_receipt_to_metadata_owns_canonical_and_compat_keys() -> None:
    metadata: dict[str, object] = {}
    receipt = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "native_tool_call_envelope_refs": [
            {
                "schema_version": "native_tool_call_envelope.v1",
                "envelope_id": "native-write",
                "tool_name": "write_file",
            },
        ],
        "dispatch_status": "dropped",
        "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
    }

    project_tool_lifecycle_receipt_to_metadata(metadata, receipt)

    assert metadata["tool_call_lifecycle_receipt"] == metadata["tool_call_lifecycle"]
    lifecycle = cast(dict[str, Any], metadata["tool_call_lifecycle_receipt"])
    failure_evidence_rows = cast(list[dict[str, Any]], metadata["failure_evidence"])
    assert lifecycle["native_tool_calls_count"] == 1
    assert metadata["native_tool_calls_count"] == 1
    assert metadata["native_tool_call_names"] == ["write_file"]
    assert failure_evidence_rows[0]["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value


def test_tool_lifecycle_normalizer_canonicalizes_legacy_dropped_tool_names() -> None:
    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "dispatch_status": "dropped",
            "failure_class": "TOOL_DISPATCH_DROPPED",
            "dropped_tool_calls": ["write_file"],
        }
    )

    assert receipt["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "reason": "tool_dispatch_dropped",
        }
    ]


def test_tool_lifecycle_normalizer_canonicalizes_failure_class_alias() -> None:
    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "dispatch_status": "blocked",
            "failure_class": "missing-effect-receipt",
        }
    )

    assert receipt["failure_class"] == FailureClassV1.MISSING_EFFECT_RECEIPT.value


def test_tool_lifecycle_normalizer_canonicalizes_dispatch_status_alias() -> None:
    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "dispatch_status": "tool-dispatch-dropped",
            "failure_class": "tool_dispatch_dropped",
            "dropped_tool_calls": ["write_file"],
        }
    )

    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value


def test_tool_lifecycle_normalizer_derives_counts_from_native_envelopes() -> None:
    envelopes = [
        {
            "schema_version": "native_tool_call_envelope.v1",
            "envelope_id": f"native_tool_call:openai:{index}:call-{index}:abcdef",
            "provider": "openai",
            "tool_name": "write_file",
            "call_id": f"call-{index}",
        }
        for index in range(2)
    ]

    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 9,
            "decoded_tool_calls_count": 2,
            "dispatched_tool_calls_count": 0,
            "native_tool_call_envelope_refs": envelopes,
            "dispatch_status": "",
            "failure_class": "",
        }
    )

    assert receipt["native_tool_calls_count"] == 2
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert receipt["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "envelope_id": "native_tool_call:openai:0:call-0:abcdef",
            "reason": "tool_dispatch_dropped",
        },
        {
            "tool_name": "write_file",
            "envelope_id": "native_tool_call:openai:1:call-1:abcdef",
            "reason": "tool_dispatch_dropped",
        },
    ]
    assert receipt["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "envelope_id": "native_tool_call:openai:0:call-0:abcdef",
            "reason": "tool_dispatch_dropped",
        },
        {
            "tool_name": "write_file",
            "envelope_id": "native_tool_call:openai:1:call-1:abcdef",
            "reason": "tool_dispatch_dropped",
        },
    ]


def test_tool_lifecycle_normalizer_preserves_count_only_dropped_evidence() -> None:
    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 6,
            "decoded_tool_calls_count": 0,
            "dispatched_tool_calls_count": 0,
            "dispatch_status": "",
            "failure_class": "",
        }
    )

    assert receipt["native_tool_calls_count"] == 6
    assert receipt["decoded_tool_calls_count"] == 6
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert receipt["dropped_tool_calls"] == [
        {
            "count": 6,
            "reason": "native_tool_calls_without_dispatch",
        }
    ]


def test_tool_lifecycle_normalizer_falls_back_to_valid_legacy_envelopes() -> None:
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native_tool_call:openai:0:call-0:abcdef",
        "provider": "openai",
        "tool_name": "write_file",
        "call_id": "call-0",
    }

    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 9,
            "dispatched_tool_calls_count": 0,
            "native_tool_call_envelope_refs": ["bad lifecycle projection"],
            "native_tool_call_envelopes": [envelope],
            "dispatch_status": "",
            "failure_class": "",
        }
    )

    assert receipt["native_tool_calls_count"] == 1
    assert receipt["native_tool_call_envelope_refs"] == [envelope]
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert receipt["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "envelope_id": "native_tool_call:openai:0:call-0:abcdef",
            "reason": "tool_dispatch_dropped",
        }
    ]


def test_tool_lifecycle_normalizer_deduplicates_native_envelopes() -> None:
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native_tool_call:openai:0:call-0:abcdef",
        "provider": "openai",
        "tool_name": "write_file",
        "call_id": "call-0",
    }

    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 9,
            "dispatched_tool_calls_count": 0,
            "native_tool_call_envelope_refs": [envelope, dict(envelope)],
            "dispatch_status": "",
            "failure_class": "",
        }
    )

    assert receipt["native_tool_calls_count"] == 1
    assert receipt["native_tool_call_envelope_refs"] == [envelope]


def test_tool_lifecycle_normalizer_derives_counts_from_lifecycle_refs() -> None:
    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 7,
            "dispatched_tool_calls_count": 0,
            "tool_result_count": 1,
            "effect_receipt_count": 0,
            "native_tool_call_envelope_refs": [
                {
                    "schema_version": "native_tool_call_envelope.v1",
                    "envelope_id": "native_tool_call:openai:0:call-0:abcdef",
                    "provider": "openai",
                    "tool_name": "write_file",
                    "call_id": "call-0",
                }
            ],
            "batch_receipt_refs": [{"batch_id": "batch-1", "receipt_hash": "batch-hash"}],
            "effect_receipt_refs": [
                {"receipt_hash": "effect-1", "operation": "write_file:create", "file": "src/index.js"},
                {"receipt_hash": "effect-2", "operation": "edit_file", "file": "src/index.js"},
            ],
            "dispatch_status": "",
            "failure_class": "",
        }
    )

    assert receipt["native_tool_calls_count"] == 1
    assert receipt["dispatched_tool_calls_count"] == 1
    assert receipt["tool_result_count"] == 1
    assert receipt["effect_receipt_count"] == 2
    assert receipt["batch_receipt_refs"] == [{"batch_id": "batch-1", "receipt_hash": "batch-hash"}]
    assert [item["receipt_hash"] for item in receipt["effect_receipt_refs"]] == ["effect-1", "effect-2"]
    assert receipt["dispatch_status"] == "dispatched"
    assert receipt["ok"] is True


def test_tool_lifecycle_normalizer_deduplicates_batch_and_effect_refs() -> None:
    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 1,
            "dispatched_tool_calls_count": 1,
            "tool_result_count": 1,
            "batch_receipt_refs": [
                {"batch_id": "batch-1", "receipt_hash": "batch-hash"},
                {"batch_id": "batch-1", "receipt_hash": "batch-hash"},
            ],
            "effect_receipt_refs": [
                {"receipt_hash": "effect-1", "operation": "write_file:create", "file": "src/index.js"},
                {"receipt_hash": "effect-1", "operation": "write_file:create", "file": "src/index.js"},
            ],
            "dispatch_status": "dispatched",
            "failure_class": "",
        }
    )

    assert receipt["batch_receipt_refs"] == [{"batch_id": "batch-1", "receipt_hash": "batch-hash"}]
    assert receipt["effect_receipt_refs"] == [
        {"receipt_hash": "effect-1", "operation": "write_file:create", "file": "src/index.js"}
    ]
    assert receipt["effect_receipt_count"] == 1


def test_tool_lifecycle_normalizer_projects_raw_dispatched_payload_as_ok() -> None:
    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 1,
            "decoded_tool_calls_count": 1,
            "dispatched_tool_calls_count": 1,
            "tool_result_count": 1,
            "effect_receipt_count": 1,
            "dispatch_status": "dispatched",
            "failure_class": "",
        }
    )

    assert receipt["ok"] is True
    assert receipt["dispatch_status"] == "dispatched"
    assert receipt["failure_class"] == ""


def test_tool_lifecycle_normalizer_does_not_mark_dispatched_without_failure_as_unknown() -> None:
    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 1,
            "decoded_tool_calls_count": 1,
            "dispatched_tool_calls_count": 1,
            "tool_result_count": 1,
            "dispatch_status": "dispatched",
        }
    )

    assert receipt["ok"] is True
    assert receipt["dispatch_status"] == "dispatched"
    assert receipt["failure_class"] == ""


def test_tool_lifecycle_receipt_preserves_native_tool_call_envelopes() -> None:
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native_tool_call:openai:0:call-1:abcdef",
        "provider": "openai",
        "tool_name": "write_file",
        "call_id": "call-1",
        "raw_call_hash": "a" * 64,
        "arguments_hash": "b" * 64,
    }
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        native_tool_call_envelopes=[envelope],
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "call_id": "call-1",
                        "tool_name": "write_file",
                        "status": "success",
                        "effect_receipt": {
                            "operation": "write_file:create",
                            "file": "src/index.js",
                        },
                    }
                ],
                "success_count": 1,
                "failure_count": 0,
            }
        ],
    ).to_dict()

    assert receipt["ok"] is True
    assert receipt["native_tool_call_envelope_refs"] == [envelope]


def test_tool_lifecycle_receipt_derives_native_count_from_envelopes() -> None:
    envelopes = [
        {
            "schema_version": "native_tool_call_envelope.v1",
            "envelope_id": f"native_tool_call:openai:{index}:call-{index}:abcdef",
            "provider": "openai",
            "tool_name": "write_file",
            "call_id": f"call-{index}",
            "raw_call_hash": "a" * 64,
            "arguments_hash": "b" * 64,
        }
        for index in range(2)
    ]

    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=7,
        decoded_tool_calls_count=2,
        dispatched_tool_calls_count=0,
        native_tool_call_envelopes=envelopes,
    ).to_dict()

    assert receipt["native_tool_calls_count"] == 2
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value


def test_tool_lifecycle_projects_task_boundary_dispatch_from_metadata() -> None:
    lifecycle = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "native_tool_call_envelope_refs": [
            {"envelope_id": "native-1", "tool_name": "write_file"},
        ],
        "decoded_tool_calls_count": 1,
        "dispatched_tool_calls_count": 0,
        "provider_response_hash": "provider/hash",
        "dispatch_status": "dropped",
        "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        "reason": "native_tool_calls_without_dispatch",
    }

    dispatch = task_boundary_tool_dispatch_from_lifecycle_metadata(
        {"tool_call_lifecycle_receipt": lifecycle},
    )

    assert dispatch == {
        "status": "dropped",
        "dropped": True,
        "native_tool_calls_count": 1,
        "native_tool_call_names": ["write_file"],
        "decoded_tool_calls_count": 1,
        "dispatched_tool_calls_count": 0,
        "provider_response_hash": "provider/hash",
        "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        "reason": "native_tool_calls_without_dispatch",
        "compatibility_mode": "native_tools",
        "text_fallback_requested": False,
        "parser_attempted": False,
    }


def test_tool_lifecycle_projects_task_boundary_dispatch_from_receipt() -> None:
    lifecycle = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "native_tool_call_envelope_refs": [
            {"envelope_id": "native-1", "tool_name": "write_file"},
        ],
        "decoded_tool_calls_count": 1,
        "dispatched_tool_calls_count": 0,
        "provider_response_hash": "provider/hash",
        "dispatch_status": "dropped",
        "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        "reason": "native_tool_calls_without_dispatch",
    }

    assert task_boundary_tool_dispatch_from_lifecycle_receipt(lifecycle) == {
        "status": "dropped",
        "dropped": True,
        "native_tool_calls_count": 1,
        "native_tool_call_names": ["write_file"],
        "decoded_tool_calls_count": 1,
        "dispatched_tool_calls_count": 0,
        "provider_response_hash": "provider/hash",
        "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        "reason": "native_tool_calls_without_dispatch",
        "compatibility_mode": "native_tools",
        "text_fallback_requested": False,
        "parser_attempted": False,
    }


def test_tool_lifecycle_projects_task_boundary_dispatch_from_plural_receipts() -> None:
    dispatched = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "native_tool_calls_count": 1,
        "dispatched_tool_calls_count": 1,
        "tool_result_count": 1,
        "dispatch_status": "dispatched",
        "failure_class": "",
    }
    dropped = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "native_tool_call_envelope_refs": [
            {"envelope_id": "native-write", "tool_name": "write_file"},
        ],
        "decoded_tool_calls_count": 1,
        "dispatched_tool_calls_count": 0,
        "provider_response_hash": "provider/plural",
        "dispatch_status": "dropped",
        "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        "reason": "native_tool_calls_without_dispatch",
    }

    dispatch = task_boundary_tool_dispatch_from_lifecycle_metadata(
        {"tool_call_lifecycle_receipts": [dispatched, dropped]},
    )

    assert dispatch == {
        "status": "dropped",
        "dropped": True,
        "native_tool_calls_count": 1,
        "native_tool_call_names": ["write_file"],
        "decoded_tool_calls_count": 1,
        "dispatched_tool_calls_count": 0,
        "provider_response_hash": "provider/plural",
        "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        "reason": "native_tool_calls_without_dispatch",
        "compatibility_mode": "native_tools",
        "text_fallback_requested": False,
        "parser_attempted": False,
    }


def test_tool_lifecycle_projects_failed_text_fallback_to_task_boundary() -> None:
    lifecycle = build_tool_batch_lifecycle_receipt(
        run_id="run-text-fallback",
        task_id="TASK-2",
        turn_id="turn-2",
        role="director",
        decoded_tool_calls_count=0,
        receipts=[],
        compatibility_mode="required_tool_text_fallback",
        text_fallback_requested=True,
        parser_attempted=True,
        native_tool_surface_absent_because_text_fallback=True,
    ).to_dict()

    dispatch = task_boundary_tool_dispatch_from_lifecycle_receipt(lifecycle)

    assert dispatch is not None
    assert dispatch["status"] == "blocked"
    assert dispatch["dropped"] is False
    assert dispatch["failure_class"] == "REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED"
    assert dispatch["compatibility_mode"] == "required_tool_text_fallback"
    assert dispatch["text_fallback_requested"] is True
    assert dispatch["parser_attempted"] is True


def test_tool_lifecycle_receipts_from_metadata_deduplicates_aliases() -> None:
    receipt = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "native_tool_call_envelope_refs": [
            {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
        ],
    }
    metadata = {
        "tool_call_lifecycle_receipt": receipt,
        "tool_call_lifecycle": dict(receipt),
        "tool_call_lifecycle_receipts": [dict(receipt)],
    }

    receipts = tool_call_lifecycle_receipts_from_metadata(metadata)

    assert len(receipts) == 1
    assert receipts[0]["native_tool_calls_count"] == 1
    assert receipts[0]["native_tool_call_envelope_refs"] == [
        {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
    ]


def test_native_tool_call_facts_from_metadata_prefers_top_level_envelopes() -> None:
    top_level = [
        {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
    ]
    metadata = {
        "native_tool_call_envelopes": top_level,
        "tool_call_lifecycle_receipt": {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_call_envelope_refs": [
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "execute_command"},
            ],
        },
    }

    assert native_tool_call_envelope_refs_from_metadata(metadata) == tuple(top_level)
    assert native_tool_call_facts_from_metadata(metadata) == {
        "native_tool_calls_count": 1,
        "native_tool_call_names": ["write_file"],
    }


def test_native_tool_call_facts_from_metadata_treats_lifecycle_zero_as_authoritative() -> None:
    metadata = {
        "native_tool_calls_count": 99,
        "tool_call_lifecycle_receipt": {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 0,
            "decoded_tool_calls_count": 0,
            "dispatched_tool_calls_count": 0,
            "dispatch_status": "dispatched",
        },
    }

    assert native_tool_call_envelope_refs_from_metadata(metadata) == ()
    assert native_tool_call_facts_from_metadata(metadata) == {
        "native_tool_calls_count": 0,
        "native_tool_call_names": [],
    }


def test_native_tool_call_count_from_metadata_uses_envelopes_before_numeric_fallback() -> None:
    metadata = {
        "native_tool_call_envelope_refs": [
            {"schema_version": "native_tool_call_envelope.v1", "tool_name": "read_file"},
            {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
        ],
        "native_tool_calls_count": 99,
    }

    assert native_tool_call_count_from_metadata(metadata, fallback=1) == 2
    assert native_tool_call_count_from_metadata({"native_tool_calls_count": 3}, fallback=1) == 3
    assert native_tool_call_count_from_metadata({}, fallback=2) == 2


def test_native_tool_call_count_from_facts_owns_fact_count_coercion() -> None:
    assert native_tool_call_count_from_facts({"native_tool_calls_count": 2}, fallback=1) == 2
    assert native_tool_call_count_from_facts({"native_tool_calls_count": 0}, fallback=3) == 3
    assert native_tool_call_count_from_facts({"native_tool_calls_count": "bad"}, fallback=4) == 4
    assert native_tool_call_count_from_facts({}, fallback=5) == 5


def test_tool_dispatch_dropped_guard_applies_owns_native_count_gate() -> None:
    facts = {"native_tool_calls_count": "2"}

    assert tool_dispatch_dropped_guard_applies(
        native_tool_call_facts=facts,
        tool_definitions_present=True,
        decoded_tool_batch_present=False,
    )
    assert not tool_dispatch_dropped_guard_applies(
        native_tool_call_facts=facts,
        tool_definitions_present=False,
        decoded_tool_batch_present=False,
    )
    assert not tool_dispatch_dropped_guard_applies(
        native_tool_call_facts=facts,
        tool_definitions_present=True,
        decoded_tool_batch_present=True,
    )
    assert not tool_dispatch_dropped_guard_applies(
        native_tool_call_facts={"native_tool_calls_count": 0},
        tool_definitions_present=True,
        decoded_tool_batch_present=False,
    )


def test_tool_lifecycle_receipt_deduplicates_native_envelopes_by_envelope_id() -> None:
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native_tool_call:openai:0:call-0:abcdef",
        "provider": "openai",
        "tool_name": "write_file",
        "call_id": "call-0",
    }

    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=9,
        decoded_tool_calls_count=9,
        dispatched_tool_calls_count=0,
        native_tool_call_envelopes=[envelope, dict(envelope)],
    ).to_dict()

    assert receipt["native_tool_calls_count"] == 1
    assert receipt["native_tool_call_envelope_refs"] == [envelope]
    assert receipt["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "envelope_id": "native_tool_call:openai:0:call-0:abcdef",
            "reason": "tool_dispatch_dropped",
        }
    ]


def test_tool_lifecycle_receipt_blocks_successful_write_without_effect_receipt() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "call_id": "call-1",
                        "tool_name": "write_file",
                        "status": "success",
                    }
                ],
                "success_count": 1,
                "failure_count": 0,
            }
        ],
    ).to_dict()

    assert receipt["ok"] is False
    assert receipt["dispatch_status"] == "blocked"
    assert receipt["failure_class"] == "MISSING_EFFECT_RECEIPT"
    assert receipt["effect_receipt_count"] == 0
    assert receipt["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "call_id": "call-1",
            "reason": "successful_write_tool_without_effect_receipt",
        }
    ]
