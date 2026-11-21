from __future__ import annotations

import pytest
from polaris.cells.roles.runtime.internal.engineering_semantic_advice import (
    CandidateRefs,
    parse_semantic_advice,
    validate_node_summary,
    validate_semantic_advice,
)


def candidates() -> CandidateRefs:
    return CandidateRefs(
        node_ids={"file_util_hpp"},
        slice_ids={"slice_display_width", "slice_render_table_call"},
        evidence_refs={"failure_unicode_alignment", "requirement_mixed_text_alignment"},
    )


def valid_advice() -> dict[str, object]:
    return {
        "assessments": [
            {
                "node_id": "file_util_hpp",
                "relation": "FAILURE_CANDIDATE",
                "suggested_view": "SLICE",
                "keep_slice_ids": ["slice_display_width"],
                "evidence_refs": ["failure_unicode_alignment"],
                "uncertain": True,
                "reason": "失败涉及显示宽度。",
            }
        ],
        "need_evidence": [],
    }


def test_semantic_advice_accepts_only_authorized_slice_evidence() -> None:
    validated = validate_semantic_advice(valid_advice(), candidates())
    assert validated["schema_version"] == "semantic-advice.v1"
    assert validated["assessments"][0]["suggested_view"] == "SLICE"


def test_semantic_advice_rejects_unknown_references_and_empty_slice() -> None:
    payload = valid_advice()
    payload["assessments"][0]["keep_slice_ids"] = ["slice_not_in_input"]
    with pytest.raises(ValueError, match="unauthorized reference"):
        validate_semantic_advice(payload, candidates())
    payload = valid_advice()
    payload["assessments"][0]["keep_slice_ids"] = []
    with pytest.raises(ValueError, match="SLICE requires"):
        validate_semantic_advice(payload, candidates())


def test_semantic_advice_rejects_non_slice_with_slices_and_bad_json() -> None:
    payload = valid_advice()
    payload["assessments"][0]["suggested_view"] = "CARD"
    with pytest.raises(ValueError, match="only valid for SLICE"):
        validate_semantic_advice(payload, candidates())
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_semantic_advice("{not json", candidates())


def summary_request() -> dict[str, object]:
    return {
        "node_id": "obs_read_312",
        "snapshot_id": "snapshot_util_v17",
        "coverage": {"observed_line_ranges": [[1, 240]]},
        "allowed_evidence_refs": ["failure_unicode_alignment"],
        "max_summary_tokens": 192,
    }


def valid_summary() -> dict[str, object]:
    return {
        "node_id": "obs_read_312",
        "snapshot_id": "snapshot_util_v17",
        "summary": "该文件负责显示宽度、Unicode 边界与表格换行计算。",
        "summary_kind": "model_inference",
        "coverage": {"observed_line_ranges": [[1, 240]], "summary_complete": False},
        "evidence_refs": ["snapshot_util_v17", "failure_unicode_alignment"],
        "uncertain": True,
        "reason": "摘要仅基于该快照和当前失败。",
    }


def test_node_summary_is_bound_to_snapshot_coverage_and_refs() -> None:
    validated = validate_node_summary(valid_summary(), summary_request())
    assert validated["schema_version"] == "node-summary.v1"
    assert validated["summary_kind"] == "model_inference"


def test_node_summary_rejects_stale_identity_fake_kind_and_bad_coverage() -> None:
    payload = valid_summary()
    payload["snapshot_id"] = "snapshot_newer"
    with pytest.raises(ValueError, match="does not match"):
        validate_node_summary(payload, summary_request())
    payload = valid_summary()
    payload["summary_kind"] = "verified_fact"
    with pytest.raises(ValueError, match="summary_kind"):
        validate_node_summary(payload, summary_request())
    payload = valid_summary()
    payload["coverage"]["observed_line_ranges"] = [[1, 100]]
    with pytest.raises(ValueError, match="coverage differs"):
        validate_node_summary(payload, summary_request())
