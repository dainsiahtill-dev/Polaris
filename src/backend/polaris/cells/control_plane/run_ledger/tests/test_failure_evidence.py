from __future__ import annotations

from polaris.cells.control_plane.run_ledger.public import (
    FailureClassV1,
    FailureEvidenceV1,
    append_failure_evidence_to_metadata,
    is_failure_class,
    merge_failure_evidence_payload,
    merge_failure_evidence_rows,
    normalize_failure_class,
    summarize_failed_gate_evidence_context_slot,
    summarize_failure_evidence_rows,
)


def test_normalize_failure_class_canonicalizes_known_values() -> None:
    assert normalize_failure_class("tool_dispatch_dropped") == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert normalize_failure_class(" TOOL-DISPATCH-DROPPED ") == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert normalize_failure_class(FailureClassV1.MISSING_EFFECT_RECEIPT) == FailureClassV1.MISSING_EFFECT_RECEIPT.value


def test_normalize_failure_class_preserves_unknown_values() -> None:
    assert normalize_failure_class("new_platform_failure") == "new_platform_failure"
    assert normalize_failure_class(None, default=FailureClassV1.TOOL_LIFECYCLE_UNKNOWN) == (
        FailureClassV1.TOOL_LIFECYCLE_UNKNOWN.value
    )


def test_is_failure_class_uses_canonical_comparison() -> None:
    assert is_failure_class("tool dispatch dropped", FailureClassV1.TOOL_DISPATCH_DROPPED)
    assert not is_failure_class("missing_tool_result", FailureClassV1.TOOL_DISPATCH_DROPPED)


def test_failure_evidence_to_dict_normalizes_failure_class_and_refs() -> None:
    evidence = FailureEvidenceV1(
        failure_class="tool_dispatch_dropped",
        responsible_layer="execution_control_plane",
        reason="native calls had no dispatch receipt",
        evidence_refs=("receipt-1", "", "receipt-2"),
        metadata={"turn_id": "turn-1"},
    ).to_dict()

    assert evidence == {
        "schema_version": "failure_evidence.v1",
        "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        "responsible_layer": "execution_control_plane",
        "reason": "native calls had no dispatch receipt",
        "evidence_refs": ["receipt-1", "receipt-2"],
        "metadata": {"turn_id": "turn-1"},
    }


def test_merge_failure_evidence_rows_keeps_structured_rows_and_dedupes() -> None:
    existing = {
        "schema_version": "failure_evidence.v1",
        "failure_class": "TOOL_RESULT_FAILED",
        "responsible_layer": "tool_executor",
    }
    lifecycle = {
        "schema_version": "failure_evidence.v1",
        "failure_class": "TOOL_DISPATCH_DROPPED",
        "responsible_layer": "execution_control_plane",
    }

    rows = merge_failure_evidence_rows([existing, "legacy text"], lifecycle, lifecycle)

    assert rows == [existing, lifecycle]


def test_merge_failure_evidence_payload_projects_structured_rows() -> None:
    existing = {
        "items": [{"failure_class": "TOOL_RESULT_FAILED"}],
        "failure_classes": ("TOOL_RESULT_FAILED",),
        "evidence_refs": ("receipt:1",),
    }
    raw_evidence = [
        {
            "failure_class": "TOOL_DISPATCH_DROPPED",
            "evidence_refs": ["provider_response:abc", "", "native_tool_call:def"],
        },
        "legacy text ignored",
    ]

    payload = merge_failure_evidence_payload(existing, raw_evidence)

    assert payload["items"] == [
        {"failure_class": "TOOL_RESULT_FAILED"},
        {
            "failure_class": "TOOL_DISPATCH_DROPPED",
            "evidence_refs": ["provider_response:abc", "", "native_tool_call:def"],
        },
    ]
    assert payload["failure_classes"] == ("TOOL_RESULT_FAILED", "TOOL_DISPATCH_DROPPED")
    assert payload["evidence_refs"] == ("receipt:1", "provider_response:abc", "native_tool_call:def")


def test_merge_failure_evidence_payload_overlays_mapping_projection() -> None:
    payload = merge_failure_evidence_payload(
        {"items": [{"failure_class": "TOOL_RESULT_FAILED"}]},
        {"failure_classes": ["CONTRACT_AMBIGUOUS"], "summary": "from upstream"},
    )

    assert payload == {
        "items": [{"failure_class": "TOOL_RESULT_FAILED"}],
        "failure_classes": ["CONTRACT_AMBIGUOUS"],
        "summary": "from upstream",
    }


def test_merge_failure_evidence_payload_projects_nested_mapping_rows() -> None:
    payload = merge_failure_evidence_payload(
        {"items": [{"failure_class": "TOOL_RESULT_FAILED"}]},
        {
            "summary": "from upstream",
            "failure_evidence": [
                {
                    "failure_class": "MISSING_EFFECT_RECEIPT",
                    "evidence_refs": ["effect:missing"],
                }
            ],
        },
    )

    assert payload["items"] == [
        {"failure_class": "TOOL_RESULT_FAILED"},
        {
            "failure_class": "MISSING_EFFECT_RECEIPT",
            "evidence_refs": ["effect:missing"],
        },
    ]
    assert payload["failure_classes"] == ("MISSING_EFFECT_RECEIPT",)
    assert payload["evidence_refs"] == ("effect:missing",)
    assert payload["failure_evidence"] == [
        {
            "failure_class": "MISSING_EFFECT_RECEIPT",
            "evidence_refs": ["effect:missing"],
        }
    ]
    assert payload["summary"] == "from upstream"


def test_summarize_failed_gate_evidence_context_slot_projects_structured_payload() -> None:
    summary = summarize_failed_gate_evidence_context_slot(
        {
            "schema_version": "failure_evidence_payload.v1",
            "source": "qa_verdict",
            "items": [
                {
                    "failure_class": "tool_dispatch_dropped",
                    "responsible_layer": "execution_control_plane",
                    "repairable_by_director": "false",
                    "requires_ce_replan": True,
                    "evidence_refs": ["provider_response:abc", "native_tool_call:def"],
                }
            ],
            "command": "npm test",
            "exit_code": "1",
            "diagnostics": [{"code": "tool_dispatch_dropped"}],
            "quality_errors": ["tool dispatch dropped"],
            "failed_required_modalities": ["command"],
            "failed_checks": ["tool_lifecycle"],
        }
    )

    assert summary == {
        "schema_version": "polaris.failed_gate_evidence.context_slot.v1",
        "source_schema_version": "failure_evidence_payload.v1",
        "source": "qa_verdict",
        "failure_class": "tool_dispatch_dropped",
        "failure_classes": ["tool_dispatch_dropped"],
        "failure_evidence_count": 1,
        "responsible_layer": "execution_control_plane",
        "repairable_by_director": False,
        "requires_ce_replan": True,
        "requires_pm_revision": False,
        "evidence_refs": ["provider_response:abc", "native_tool_call:def"],
        "command": "npm test",
        "exit_code": 1,
        "diagnostic_count": 1,
        "quality_error_count": 1,
        "failed_required_modalities": ["command"],
        "failed_checks": ["tool_lifecycle"],
    }


def test_summarize_failure_evidence_rows_uses_structured_rows_only() -> None:
    rows = [
        "legacy text",
        {
            "schema_version": "failure_evidence.v1",
            "failure_class": "TOOL_RESULT_FAILED",
            "responsible_layer": "tool_executor",
        },
        {
            "schema_version": "failure_evidence.v1",
            "failure_class": "TOOL_DISPATCH_DROPPED",
            "responsible_layer": "execution_control_plane",
        },
    ]

    summary = summarize_failure_evidence_rows(
        rows,
        existing_summary={"source": "previous_projection", "count": 99},
    )

    assert summary == {
        "source": "previous_projection",
        "count": 2,
        "latest_failure_class": "TOOL_DISPATCH_DROPPED",
    }


def test_append_failure_evidence_to_metadata_refreshes_rows_and_summary() -> None:
    existing = {
        "schema_version": "failure_evidence.v1",
        "failure_class": "TOOL_RESULT_FAILED",
        "responsible_layer": "tool_executor",
    }
    lifecycle = {
        "schema_version": "failure_evidence.v1",
        "failure_class": "TOOL_DISPATCH_DROPPED",
        "responsible_layer": "execution_control_plane",
    }
    metadata = {
        "failure_evidence": [existing, "legacy text"],
        "failure_evidence_summary": {"source": "previous_projection", "count": 99},
    }

    rows = append_failure_evidence_to_metadata(metadata, lifecycle, lifecycle)

    assert rows == [existing, lifecycle]
    assert metadata["failure_evidence"] == [existing, lifecycle]
    assert metadata["failure_evidence_summary"] == {
        "source": "previous_projection",
        "count": 2,
        "latest_failure_class": "TOOL_DISPATCH_DROPPED",
    }
