from __future__ import annotations

import asyncio
import json

import pytest
from polaris.cells.roles.runtime.internal.engineering_file_cards import build_file_card
from polaris.cells.roles.runtime.internal.engineering_semantic_advice import CandidateRefs, summary_input_key
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
    assert (
        runtime.fold_summaries()[summary_input_key("a.py", "filecard:a", "input", [[1, 1]])]["summary"]
        == "旧文件主要负责输入处理。"
    )


class _SlowPool:
    """One in-flight job at a time; a re-schedule must not kill it."""

    def __init__(self) -> None:
        self.calls = 0

    async def summarize_many(self, requests):
        self.calls += 1
        await asyncio.sleep(0.05)
        return [
            {
                "endpoint_index": 0,
                "validation_result": "VALID",
                "result": {
                    "node_id": "file:a.py",
                    "snapshot_id": "filecard:a",
                    "summary": "慢速摘要内容",
                    "summary_kind": "model_inference",
                    "coverage": {"observed_line_ranges": [[1, 1]], "summary_complete": False},
                    "evidence_refs": ["filecard:a"],
                    "uncertain": False,
                    "reason": "慢速返回。",
                },
            }
        ]


async def test_semantic_runtime_keeps_an_in_flight_job_across_a_reschedule() -> None:
    runtime = SemanticRuntime(_SlowPool(), "assist")
    refs = CandidateRefs(node_ids={"file:a.py"}, slice_ids={"slice_file"}, evidence_refs={"filecard:a"})
    request = {
        "candidates": [
            {
                "node_id": "file:a.py",
                "snapshot_id": "filecard:a",
                "content": "x" * 600,
                "coverage": {"observed_line_ranges": [[1, 1]]},
            }
        ]
    }
    runtime.schedule(request, refs, fingerprint="one")
    runtime.schedule(request, refs, fingerprint="two")
    await asyncio.sleep(0.2)
    assert runtime.poll() is not None
    assert runtime.pool.calls == 1
    assert (
        runtime.fold_summaries()[summary_input_key("file:a.py", "filecard:a", "x" * 600, [[1, 1]])]["summary"]
        == "慢速摘要内容"
    )


def test_distiller_thresholds_derive_from_the_fold_knobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The distiller runs ahead of the fold, but the lead is derived, not restated."""

    from polaris.cells.roles.runtime.internal.engineering_tool_payload_projection import (
        distill_min_activities,
        distill_min_bytes,
    )

    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "30")
    monkeypatch.setenv("KERNELONE_CONTEXTOS_FOLD_MIN_BYTES", "2048")
    assert distill_min_activities() == 16
    assert distill_min_bytes() == 1024


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
    assert result is not None and result["validation_result"] == "REJECTED"
    assert runtime.fold_summaries() == {}
    runtime.schedule(request, refs, fingerprint="same-slice")
    await asyncio.sleep(0)
    assert runtime.poll() is None


def test_facts_for_advice_carries_only_selected_sources() -> None:
    card = build_file_card(
        "a.py",
        "x" * 4000,
        revision=1,
        preview="x" * 4000,
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
    assert refs.node_ids == {"file:a.py"}
    assert refs.evidence_refs == {"filecard:a"}
    assert len(fingerprint) == 64
    assert set(request) == {"candidates"}


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
    assert "write:src/garden.ts" in refs.node_ids
    written = [item for item in request["candidates"] if item["kind"] == "write_call"]
    assert len(written) == 1 and written[0]["node_id"] == "write:src/garden.ts"
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
    assert "write:src/hot.ts" not in refs.node_ids


def test_distiller_spends_its_slots_on_the_oldest_payload_first() -> None:
    """A newest-first selection summarises payloads the fold has not reached."""

    def write(call_id: str, path: str, body: str, seq: int) -> dict[str, object]:
        return {
            "kind": "tool_started",
            "seq": seq,
            "payload": {"tool": "write_file", "call_id": call_id, "arguments": {"file": path, "content": body}},
        }

    def done(call_id: str, seq: int) -> dict[str, object]:
        return {"kind": "tool_observed", "seq": seq, "payload": {"tool": "write_file", "call_id": call_id, "ok": True}}

    events = [write("c1", "src/old.ts", "a" * 900, 1), done("c1", 2)]
    events.extend({"kind": "turn_started", "seq": seq, "payload": {}} for seq in range(3, 17))
    events.extend([write("c2", "src/new.ts", "b" * 900, 17), done("c2", 18)])
    events.extend({"kind": "turn_started", "seq": seq, "payload": {}} for seq in range(19, 22))
    request, _refs, _fingerprint = facts_for_advice({"instruction": "fix"}, events)
    # c2 is only three activities old, so it is neither distilled nor folded yet.
    assert [item["node_id"] for item in request["candidates"]] == ["write:src/old.ts"]


def test_distiller_orders_two_eligible_payloads_by_age_then_size() -> None:
    events = [
        {
            "kind": "tool_started",
            "seq": 1,
            "payload": {
                "tool": "write_file",
                "call_id": "c1",
                "arguments": {"file": "src/big.ts", "content": "a" * 2000},
            },
        },
        {"kind": "tool_observed", "seq": 2, "payload": {"tool": "write_file", "call_id": "c1", "ok": True}},
        {
            "kind": "tool_started",
            "seq": 3,
            "payload": {
                "tool": "write_file",
                "call_id": "c2",
                "arguments": {"file": "src/small.ts", "content": "b" * 900},
            },
        },
        {"kind": "tool_observed", "seq": 4, "payload": {"tool": "write_file", "call_id": "c2", "ok": True}},
    ]
    events.extend({"kind": "turn_started", "seq": seq, "payload": {}} for seq in range(5, 20))
    request, _refs, _fingerprint = facts_for_advice({"instruction": "fix"}, events)
    assert [item["node_id"] for item in request["candidates"]] == ["write:src/big.ts", "write:src/small.ts"]


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


async def test_distill_one_snapshot_once_without_other_nodes_references() -> None:
    class Pool:
        def __init__(self) -> None:
            self.requests = []

        async def summarize_many(self, requests):
            self.requests.extend(requests)
            return [
                {
                    "validation_result": "VALID",
                    "result": {
                        "node_id": item["node_id"],
                        "snapshot_id": item["snapshot_id"],
                        "coverage": item["coverage"],
                        "summary": "这个模块独立负责输入的规范化和校验。",
                    },
                }
                for item in requests
            ]

    pool = Pool()
    runtime = SemanticRuntime(pool, "assist")
    refs = CandidateRefs(node_ids={"file:a.py"}, slice_ids=set(), evidence_refs={"command:unrelated"})
    candidate = {
        "node_id": "file:a.py",
        "snapshot_id": "filecard:a",
        "content": "source",
        "coverage": {"observed_line_ranges": [[1, 1]]},
    }
    for key in ("first", "other journal activity"):
        runtime.schedule({"candidates": [candidate]}, refs, fingerprint=key)
        await asyncio.sleep(0)
        runtime.poll()
    assert [r["snapshot_id"] for r in pool.requests] == ["filecard:a"]
    assert "command:unrelated" not in pool.requests[0]["allowed_evidence_refs"]
