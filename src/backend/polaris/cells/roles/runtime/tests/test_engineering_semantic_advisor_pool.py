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


async def test_runtime_only_folds_the_advisor_result_grounded_in_the_current_source(monkeypatch) -> None:
    from polaris.cells.roles.runtime.internal.engineering_semantic_advice import CandidateRefs, summary_input_key
    from polaris.cells.roles.runtime.internal.engineering_semantic_runtime import SemanticRuntime

    source = "const LABEL_TABLE = { neutral: 'plain' };"
    nodes = ["file:one.js", "write:two.js"]
    pool = RemoteAdvisorPool([RemoteAdvisorConfig(base_url="http://unused", model="fixture")])
    sent = []

    def respond(messages, schema):
        request = json.loads(messages[1]["content"])
        sent.append((request["node_kind"], len(messages)))
        assert set(schema["required"]) == {"summary", "source_line", "uncertain"}
        assert "node_id" not in request and "coverage" not in request and "snapshot_id" not in request
        assert request["source_lines"] == [[1, source]]
        if request["node_kind"] == "write_call" and len(messages) > 2:
            return json.dumps(
                {"summary": "The neutral lookup maps to the plain label.", "source_line": 1, "uncertain": True}
            )
        return json.dumps({"summary": "The lookup is exported to every caller.", "source_line": 12, "uncertain": False})

    monkeypatch.setattr(pool._advisors[0], "_post", respond)
    runtime = SemanticRuntime(pool, "assist")
    candidates = [
        {
            "node_id": name,
            "snapshot_id": name + ":1",
            "content": source,
            "kind": kind,
            "coverage": {"observed_line_ranges": [[1, 1]]},
        }
        for name, kind in zip(nodes, ["file_card", "write_call"], strict=True)
    ]
    refs = CandidateRefs(node_ids=set(nodes), slice_ids=set(), evidence_refs=set())
    runtime.schedule({"candidates": candidates}, refs, fingerprint="exact")
    async with asyncio.timeout(5):
        while (result := runtime.poll()) is None:
            await asyncio.sleep(0.01)
    assert result["validation_result"] == "PARTIAL"
    key = summary_input_key(nodes[1], nodes[1] + ":1", source, [[1, 1]])
    assert list(runtime.fold_summaries()) == [key]
    assert runtime.fold_summaries()[key]["source_anchor"] == source
    assert runtime.fold_summaries()[key]["source_line"] == 1
    assert source in runtime.fold_summaries()[key]["summary"]
    assert sent == [("file_card", 2), ("file_card", 4), ("write_call", 2), ("write_call", 4)]


def test_compact_distiller_request_still_accepts_a_truthful_legacy_provider_result() -> None:
    request = {**_request(), "node_kind": "file_card", "content": "const LABEL_TABLE = { neutral: 'plain' };"}
    old_response = {
        "node_id": request["node_id"],
        "snapshot_id": request["snapshot_id"],
        "summary": "LABEL_TABLE associates the neutral state with the plain label.",
        "summary_kind": "model_inference",
        "coverage": {"observed_line_ranges": [[1, 240]], "summary_complete": False},
    }
    advisor = _FakeAdvisor(RemoteAdvisorConfig(base_url="http://unused", model="fixture"), old_response)
    admitted = asyncio.run(advisor.summarize_node(request))
    assert admitted["summary"] == old_response["summary"] and admitted["source_anchor"] == "LABEL_TABLE"
