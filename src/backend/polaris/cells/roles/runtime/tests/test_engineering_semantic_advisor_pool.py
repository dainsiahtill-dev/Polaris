from __future__ import annotations

import asyncio
import json

from polaris.cells.roles.runtime.internal.engineering_semantic_advisor import (
    RemoteAdvisorConfig,
    RemoteSemanticAdvisor,
)
from polaris.cells.roles.runtime.internal.engineering_semantic_advisor_pool import RemoteAdvisorPool


def _request() -> dict[str, object]:
    return {
        "node_id": "obs_read_312",
        "snapshot_id": "snapshot_util_v17",
        "coverage": {"observed_line_ranges": [[1, 240]]},
        "allowed_evidence_refs": ["failure_unicode_alignment"],
        "max_summary_tokens": 192,
    }


class _FakeAdvisor(RemoteSemanticAdvisor):
    def __init__(self, config: RemoteAdvisorConfig, payload: dict[str, object]) -> None:
        super().__init__(config)
        self._payload = payload

    def _post(self, messages: list[dict[str, str]], schema: dict[str, object] | None = None) -> str:
        return json.dumps(self._payload, ensure_ascii=False)


def test_missing_summary_fields_use_conservative_host_defaults() -> None:
    payload = {
        "node_id": "obs_read_312",
        "snapshot_id": "snapshot_util_v17",
        "summary": "该文件负责显示宽度计算。",
        "summary_kind": "model_inference",
        "coverage": {"observed_line_ranges": [[1, 240]]},
    }
    advisor = _FakeAdvisor(RemoteAdvisorConfig(base_url="http://localhost", model="fake"), payload)
    result = asyncio.run(advisor.summarize_node(_request()))
    assert result["uncertain"] is True
    assert result["summary_kind"] == "model_inference"
    assert result["evidence_refs"] == ["snapshot_util_v17", "failure_unicode_alignment"]
    assert advisor.last_normalization


def test_pool_keeps_valid_result_when_other_endpoint_is_rejected() -> None:
    valid = {
        "node_id": "obs_read_312",
        "snapshot_id": "snapshot_util_v17",
        "summary": "该文件负责显示宽度计算。",
        "summary_kind": "model_inference",
        "coverage": {"observed_line_ranges": [[1, 240]]},
    }
    invalid = {"node_id": "obs_read_312", "snapshot_id": "snapshot_util_v17", "summary": 42}
    pool = RemoteAdvisorPool(
        [
            RemoteAdvisorConfig(base_url="http://a", model="a"),
            RemoteAdvisorConfig(base_url="http://b", model="b"),
        ]
    )
    pool._advisors[0] = _FakeAdvisor(pool._advisors[0].config, valid)
    pool._advisors[1] = _FakeAdvisor(pool._advisors[1].config, invalid)
    results = asyncio.run(pool.summarize_many([_request(), _request()]))
    assert results[0]["validation_result"] == "VALID"
    assert results[1]["validation_result"] == "REJECTED"
    assert "wall_seconds" in results[0] and "timings" in results[0]
