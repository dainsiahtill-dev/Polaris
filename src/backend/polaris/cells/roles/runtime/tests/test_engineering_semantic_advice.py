from __future__ import annotations

import pytest
from polaris.cells.roles.runtime.internal.engineering_semantic_advice import validate_node_summary


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


def test_node_summary_rejects_status_words_and_verbatim_excerpts() -> None:
    content = "74:  private accumulator = 0;\n75:  private error: string | null = null;\n"
    for bad in ("completed", "completed successfully", "ok", "failed"):
        payload = valid_summary()
        payload["summary"] = bad
        with pytest.raises(ValueError, match="describe the payload"):
            validate_node_summary(payload, summary_request())
    payload = valid_summary()
    payload["summary"] = "75:  private error: string | null = null;"
    with pytest.raises(ValueError, match="describe the payload"):
        validate_node_summary(payload, {**summary_request(), "content": content})


def test_node_summary_accepts_substantive_description_of_content() -> None:
    payload = valid_summary()
    payload["summary"] = "该文件负责显示宽度与 Unicode 边界计算。"
    request = {**summary_request(), "content": "75: private error: string | null = null;"}
    validated = validate_node_summary(payload, request)
    assert validated["summary"] == "该文件负责显示宽度与 Unicode 边界计算。"
