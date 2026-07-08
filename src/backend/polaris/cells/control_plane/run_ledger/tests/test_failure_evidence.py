from __future__ import annotations

from polaris.cells.control_plane.run_ledger.public import (
    FailureClassV1,
    FailureEvidenceV1,
    append_failure_evidence_to_metadata,
    is_failure_class,
    looks_like_failure_evidence_payload,
    merge_failure_evidence_payload,
    merge_failure_evidence_rows,
    normalize_failure_class,
    summarize_failed_gate_evidence_context_slot,
    summarize_failure_evidence_rows,
    suspected_files_from_failure_evidence_payload,
    task_boundary_failure_evidence_from_verdict,
)


def test_normalize_failure_class_canonicalizes_known_values() -> None:
    assert normalize_failure_class("tool_dispatch_dropped") == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert normalize_failure_class(" TOOL-DISPATCH-DROPPED ") == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert normalize_failure_class(FailureClassV1.MISSING_EFFECT_RECEIPT) == FailureClassV1.MISSING_EFFECT_RECEIPT.value
    assert normalize_failure_class("patch_file_protocol_disabled") == (
        FailureClassV1.PATCH_FILE_PROTOCOL_DISABLED.value
    )
    assert normalize_failure_class("text-tool-protocol-disabled") == FailureClassV1.TEXT_TOOL_PROTOCOL_DISABLED.value


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


def test_failure_evidence_to_dict_returns_canonical_shape() -> None:
    evidence = FailureEvidenceV1(
        failure_class=FailureClassV1.MISSING_EFFECT_RECEIPT,
        responsible_layer="tool_executor",
        reason="effect receipt missing",
        evidence_refs=(" effect:1 ", ""),
        metadata={"tool": "write_file"},
    ).to_dict()

    assert set(evidence) == {
        "schema_version",
        "failure_class",
        "responsible_layer",
        "reason",
        "evidence_refs",
        "metadata",
    }
    assert evidence["failure_class"] == FailureClassV1.MISSING_EFFECT_RECEIPT.value
    assert evidence["evidence_refs"] == ["effect:1"]


def test_failure_evidence_to_dict_uses_first_known_separator_token() -> None:
    evidence = FailureEvidenceV1(
        failure_class="failure_class: TOOL_DISPATCH_DROPPED; provider_also_missed",
        responsible_layer="execution_control_plane",
        reason="legacy metadata included prose around the class",
    ).to_dict()

    assert evidence["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert is_failure_class(evidence["failure_class"], FailureClassV1.TOOL_DISPATCH_DROPPED)


def test_failure_evidence_to_dict_preserves_first_unknown_separator_token() -> None:
    evidence = FailureEvidenceV1(
        failure_class="new_platform_failure; extra context",
        responsible_layer="execution_control_plane",
        reason="unknown classes remain visible",
    ).to_dict()

    assert evidence["failure_class"] == "new_platform_failure"


def test_failure_evidence_to_dict_round_trips_through_payload_projection() -> None:
    evidence = FailureEvidenceV1(
        failure_class="TOOL_RESULT_FAILED; ignored secondary note",
        responsible_layer="tool_executor",
        reason="tool result failed",
        evidence_refs=("tool:1",),
    ).to_dict()

    payload = merge_failure_evidence_payload({}, [evidence])

    assert payload["items"] == [evidence]
    assert payload["failure_classes"] == (FailureClassV1.TOOL_RESULT_FAILED.value,)
    assert payload["evidence_refs"] == ("tool:1",)


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


def test_looks_like_failure_evidence_payload_uses_structure_not_prose() -> None:
    assert looks_like_failure_evidence_payload(
        {
            "schema_version": "polaris.failed_gate_evidence.context_slot.v1",
        }
    )
    assert looks_like_failure_evidence_payload(
        {
            "items": [
                {
                    "schema_version": "failure_evidence.v1",
                    "failure_class": "TOOL_DISPATCH_DROPPED",
                }
            ]
        }
    )
    assert looks_like_failure_evidence_payload({"failed_required_modalities": ["command"]})
    assert not looks_like_failure_evidence_payload("failure_class: TOOL_DISPATCH_DROPPED")
    assert not looks_like_failure_evidence_payload({"message": "failure_class: TOOL_DISPATCH_DROPPED"})


def test_merge_failure_evidence_payload_canonicalizes_known_separator_classes() -> None:
    payload = merge_failure_evidence_payload(
        {},
        [
            {
                "failure_class": "failure_class: tool_dispatch_dropped; extra",
            },
            {
                "failure_class": "tool-dispatch-dropped",
            },
            {
                "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED,
            },
        ],
    )

    assert payload["failure_classes"] == (FailureClassV1.TOOL_DISPATCH_DROPPED.value,)
    for item in payload["items"]:
        assert item["failure_class"] in (
            "failure_class: tool_dispatch_dropped; extra",
            "tool-dispatch-dropped",
            FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        )


def test_merge_failure_evidence_payload_preserves_unknown_classes() -> None:
    payload = merge_failure_evidence_payload(
        {},
        [
            {"failure_class": "new_platform_failure"},
            {"failure_class": "experiment_class; with extras"},
            {"failure_class": FailureClassV1.MISSING_EFFECT_RECEIPT.value},
        ],
    )

    assert payload["failure_classes"] == (
        "new_platform_failure",
        "experiment_class",
        FailureClassV1.MISSING_EFFECT_RECEIPT.value,
    )


def test_merge_failure_evidence_payload_dedupes_canonical_known_class_with_variant_spelling() -> None:
    payload = merge_failure_evidence_payload(
        {
            "items": [{"failure_class": "tool_dispatch_dropped"}],
            "failure_classes": ("tool_dispatch_dropped",),
        },
        [
            {"failure_class": "tool-dispatch-dropped"},
            {"failure_class": "TOOL_DISPATCH_DROPPED"},
            {"failure_class": "failure_class: tool-dispatch-dropped; legacy note"},
        ],
    )

    assert payload["failure_classes"] == (FailureClassV1.TOOL_DISPATCH_DROPPED.value,)
    assert payload["items"] == [
        {"failure_class": "tool_dispatch_dropped"},
        {"failure_class": "tool-dispatch-dropped"},
        {"failure_class": "TOOL_DISPATCH_DROPPED"},
        {"failure_class": "failure_class: tool-dispatch-dropped; legacy note"},
    ]


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
        "failure_classes": [FailureClassV1.TOOL_DISPATCH_DROPPED.value],
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


def test_summarize_failure_evidence_rows_projects_explicit_failure_classes() -> None:
    rows = [
        {
            "schema_version": "failure_evidence.v1",
            "failure_class": "TOOL_DISPATCH_DROPPED",
            "failure_classes": ["tool-dispatch-dropped", "TOOL_DISPATCH_DROPPED"],
        }
    ]

    summary = summarize_failure_evidence_rows(rows)

    assert summary == {
        "count": 1,
        "latest_failure_class": "TOOL_DISPATCH_DROPPED",
        "failure_classes": ["TOOL_DISPATCH_DROPPED"],
    }


def test_suspected_files_from_failure_evidence_payload_uses_structured_fields_only() -> None:
    payload = {
        "changed_files": ["src/main.py", "", "src/main.py"],
        "target_paths": "src/engine.py",
        "items": [
            {
                "candidate_files": ["tests/test_product.py"],
                "message": "do not parse prose mentioning ignored.py",
            },
            "legacy prose src/ignored.py",
        ],
        "failure_evidence": [
            {
                "suspected_files": ["README.md"],
            }
        ],
    }

    assert suspected_files_from_failure_evidence_payload(payload) == [
        "src/main.py",
        "src/engine.py",
        "tests/test_product.py",
        "README.md",
    ]


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

def test_task_boundary_failure_evidence_from_verdict_projects_public_row() -> None:
    task_boundary_failure = task_boundary_failure_evidence_from_verdict(
        {
            "ok": False,
            "task_id": "task-1",
            "run_id": "run-1",
            "status": "failed",
            "failure_class": "TASK_BOUNDARY_FAILED",
            "responsible_layer": "task_boundary",
            "reason": "missing required command evidence",
            "failure_stage": "verification",
            "root_cause_hint": "missing command receipt",
            "detail": "required verifier did not produce command evidence",
            "failed_required_evidence_modalities": ["command"],
            "diagnostic_context": {"paths": ("src/main.py",)},
            "evidence_refs": ["run_ledger:task-boundary-1"],
            "requires_ce_replan": "true",
        }
    )

    metadata = {
        "failure_evidence_summary": {
            "source": "previous_projection",
            "owner": "run_ledger_public_helper",
            "count": 99,
        },
    }

    rows = append_failure_evidence_to_metadata(metadata, task_boundary_failure)

    assert task_boundary_failure["failure_class"] == "TASK_BOUNDARY_FAILED"
    assert task_boundary_failure["responsible_layer"] == "task_boundary"
    assert task_boundary_failure["reason"] == "missing required command evidence"
    assert task_boundary_failure["evidence_refs"] == ["run_ledger:task-boundary-1"]
    assert task_boundary_failure["failure_stage"] == "verification"
    assert task_boundary_failure["failure_classes"] == ["TASK_BOUNDARY_FAILED"]
    assert task_boundary_failure["root_cause_hint"] == "missing command receipt"
    assert task_boundary_failure["detail"] == "required verifier did not produce command evidence"
    assert task_boundary_failure["requires_ce_replan"] is True
    assert task_boundary_failure["metadata"]["source"] == "polaris.task_boundary_verdict.v1"
    assert task_boundary_failure["metadata"]["task_boundary_status"] == "failed"
    assert task_boundary_failure["metadata"]["task_id"] == "task-1"
    assert task_boundary_failure["metadata"]["run_id"] == "run-1"
    assert task_boundary_failure["metadata"]["failed_required_evidence_modalities"] == ["command"]
    assert task_boundary_failure["metadata"]["diagnostic_context"] == {"paths": ["src/main.py"]}
    assert rows == [task_boundary_failure]
    assert metadata["failure_evidence"] == [task_boundary_failure]
    assert metadata["failure_evidence_summary"] == {
        "source": "previous_projection",
        "owner": "run_ledger_public_helper",
        "count": 1,
        "latest_failure_class": "TASK_BOUNDARY_FAILED",
        "failure_classes": ["TASK_BOUNDARY_FAILED"],
    }


def test_task_boundary_failure_evidence_from_verdict_ignores_ok_verdict() -> None:
    assert (
        task_boundary_failure_evidence_from_verdict(
            {
                "ok": True,
                "status": "completed_verified",
                "failure_class": "SHOULD_NOT_PROJECT",
            }
        )
        == {}
    )


def test_task_boundary_failure_evidence_from_verdict_accepts_role_result_metadata() -> None:
    row = task_boundary_failure_evidence_from_verdict(
        {
            "task_boundary_verdict": {
                "ok": False,
                "status": "failed",
                "failure_class": "INCOMPLETE_MATERIALIZATION",
                "reason": "target files were not written",
                "missing_target_files": ["src/index.ts"],
            }
        }
    )

    assert row["failure_class"] == "INCOMPLETE_MATERIALIZATION"
    assert row["reason"] == "target files were not written"
    assert row["metadata"]["missing_target_files"] == ["src/index.ts"]
