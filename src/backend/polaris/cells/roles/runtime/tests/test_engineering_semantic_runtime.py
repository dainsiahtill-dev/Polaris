from __future__ import annotations

import asyncio
import json

from polaris.cells.roles.runtime.internal.engineering_file_cards import build_file_card
from polaris.cells.roles.runtime.internal.engineering_semantic_advice import CandidateRefs
from polaris.cells.roles.runtime.internal.engineering_semantic_runtime import SemanticRuntime, facts_for_advice


class _SummaryPool:
    async def summarize_many(self, requests):
        return [
            {
                "endpoint_index": 0,
                "validation_result": "VALID",
                "result": {
                    "node_id": "a.py",
                    "snapshot_id": "filecard:a",
                    "summary": "旧文件主要负责输入处理。",
                    "summary_kind": "model_inference",
                    "coverage": {"observed_line_ranges": [[1, 1]], "summary_complete": False},
                    "evidence_refs": ["filecard:a"],
                    "uncertain": False,
                    "reason": "基于旧快照摘要。",
                },
            }
        ]


async def test_semantic_runtime_stores_remote_fold_summary() -> None:
    runtime = SemanticRuntime(_SummaryPool(), "assist")
    refs = CandidateRefs(node_ids={"a.py"}, slice_ids={"slice_file"}, evidence_refs={"filecard:a"})
    request = {
        "candidates": [
            {
                "node_id": "a.py",
                "snapshot_id": "filecard:a",
                "content": "input",
                "coverage": {"observed_line_ranges": [[1, 1]]},
            }
        ]
    }
    runtime.schedule(request, refs, fingerprint="fold")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    result = runtime.poll()
    assert result is not None and result["validation_result"] == "VALID"
    assert runtime.fold_summaries()["a.py"]["summary"] == "旧文件主要负责输入处理。"


class _RejectingPool:
    async def summarize_many(self, requests: list[dict[str, object]]) -> list[dict[str, object]]:
        return [
            {
                "endpoint_index": 0,
                "validation_result": "REJECTED",
                "error": "ValueError: summary must describe the payload, not a status word or a verbatim excerpt",
            }
        ]


async def test_semantic_runtime_drops_rejected_summary_so_host_falls_back() -> None:
    runtime = SemanticRuntime(_RejectingPool(), "assist")
    refs = CandidateRefs(node_ids={"a.py"}, slice_ids={"slice_file"}, evidence_refs={"filecard:a"})
    request = {
        "candidates": [
            {
                "node_id": "a.py",
                "snapshot_id": "filecard:a",
                "content": "input",
                "coverage": {"observed_line_ranges": [[1, 1]]},
            }
        ]
    }
    runtime.schedule(request, refs, fingerprint="fold")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    result = runtime.poll()
    assert result is not None and result["validation_result"] == "VALID"
    assert runtime.fold_summaries() == {}


def test_facts_for_advice_bounds_candidates_and_failure() -> None:
    card = build_file_card(
        "a.py",
        "x" * 600,
        revision=1,
        preview="x" * 600,
        truncated=False,
        start_line=1,
        end_line=1,
        snapshot_ref="filecard:a",
    )
    events = [
        {
            "kind": "tool_observed",
            "seq": 1,
            "payload": {"tool": "read_file", "ok": True, "content": json.dumps({"file_card": card})},
        },
        {
            "kind": "tool_observed",
            "seq": 2,
            "payload": {
                "tool": "execute_command",
                "ok": True,
                "content": json.dumps({"command_card": {"content_ref": "command:abc"}}),
            },
        },
        {
            "kind": "tool_observed",
            "seq": 3,
            "payload": {"tool": "execute_command", "ok": False, "call_id": "failure_1", "content": "assertion failed"},
        },
    ]
    events.extend({"kind": "turn_started", "seq": index + 4, "payload": {}} for index in range(11))
    events.extend({"kind": "message", "seq": index + 15, "payload": {}} for index in range(100))
    request, refs, fingerprint = facts_for_advice({"instruction": "fix", "workspace_revision": 2}, events)
    assert refs.node_ids == {"a.py"}
    assert "failure_1" in refs.evidence_refs
    assert "command:abc" in refs.evidence_refs
    assert len(fingerprint) == 64
    assert request["activity"] == "assertion failed"


def test_facts_for_advice_summarizes_written_bodies_for_folding() -> None:
    """A folded write call should be able to carry a real summary, not only a
    deterministic structural note."""

    events = [
        {
            "kind": "tool_started",
            "seq": 1,
            "payload": {
                "tool": "write_file",
                "call_id": "call_w1",
                "arguments": {"file": "src/garden.ts", "content": "export const step = 1;\n" * 60},
            },
        },
        {
            "kind": "tool_observed",
            "seq": 2,
            "payload": {"tool": "write_file", "call_id": "call_w1", "ok": True, "content": "Wrote 1020 bytes."},
        },
    ]
    events.extend({"kind": "turn_started", "seq": index + 3, "payload": {}} for index in range(14))
    request, refs, _fingerprint = facts_for_advice({"instruction": "fix the garden"}, events)
    assert "src/garden.ts" in refs.node_ids
    written = [item for item in request["candidates"] if item["kind"] == "write_call"]
    assert len(written) == 1 and written[0]["node_id"] == "src/garden.ts"
    assert written[0]["content"].startswith("export const step = 1;")


def test_facts_for_advice_skips_a_write_that_is_still_the_current_target() -> None:
    events = [
        {
            "kind": "tool_started",
            "seq": 1,
            "payload": {
                "tool": "write_file",
                "call_id": "call_w1",
                "arguments": {"file": "src/hot.ts", "content": "x" * 900},
            },
        },
        {"kind": "tool_observed", "seq": 2, "payload": {"tool": "write_file", "call_id": "call_w1", "ok": True}},
    ]
    events.extend({"kind": "turn_started", "seq": index + 3, "payload": {}} for index in range(9))
    # The write is inside the last twelve events, so it is still the edit target.
    _request, refs, _fingerprint = facts_for_advice({"instruction": "fix"}, events)
    assert "src/hot.ts" not in refs.node_ids


def test_facts_for_advice_does_not_trigger_before_ten_activities() -> None:
    card = build_file_card(
        "a.py",
        "x" * 600,
        revision=1,
        preview="x" * 600,
        truncated=False,
        start_line=1,
        end_line=1,
        snapshot_ref="filecard:a",
    )
    events = [
        {
            "kind": "tool_observed",
            "seq": 1,
            "payload": {"tool": "read_file", "ok": True, "content": json.dumps({"file_card": card})},
        }
    ]
    events.extend({"kind": "turn_started", "seq": index + 2, "payload": {}} for index in range(9))
    _request, refs, _fingerprint = facts_for_advice({"instruction": "fix"}, events)
    assert refs.node_ids == set()
