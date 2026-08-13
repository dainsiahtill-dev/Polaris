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
    build_verified_existing_artifact_lifecycle_receipt,
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
