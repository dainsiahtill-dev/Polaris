from __future__ import annotations

import asyncio
import json

from polaris.cells.roles.runtime.internal.engineering_file_card_projection import fold_file_card_previews
from polaris.cells.roles.runtime.internal.engineering_file_cards import build_file_card
from polaris.cells.roles.runtime.internal.engineering_semantic_advice import CandidateRefs
from polaris.cells.roles.runtime.internal.engineering_semantic_runtime import SemanticRuntime, facts_for_advice


class _FakePool:
    async def advise_many(
        self, requests: list[dict[str, object]], candidates: CandidateRefs
    ) -> list[dict[str, object]]:
        return [
            {
                "endpoint_index": 0,
                "validation_result": "VALID",
                "result": {
                    "schema_version": "semantic-advice.v1",
                    "assessments": [
                        {
                            "node_id": "a.py",
                            "relation": "FAILURE_CANDIDATE",
                            "suggested_view": "SLICE",
                            "keep_slice_ids": ["slice_alpha"],
                            "evidence_refs": ["failure_1"],
                            "uncertain": True,
                            "reason": "当前失败可能相关。",
                        }
                    ],
                    "need_evidence": [],
                },
            }
        ]


async def test_semantic_runtime_assist_pins_only_relevant_cards() -> None:
    runtime = SemanticRuntime(_FakePool(), "assist")
    runtime.schedule({}, CandidateRefs(node_ids=set(), slice_ids=set(), evidence_refs=set()), fingerprint="one")
    await asyncio.sleep(0)
    result = runtime.poll()
    assert result is not None and result["validation_result"] == "VALID"
    assert runtime.pin_reasons() == {"a.py": "semantic_relevance"}
    card = build_file_card(
        "a.py", "x", revision=1, preview="x", truncated=False, start_line=1, end_line=1, snapshot_ref="filecard:a"
    )
    older = {"role": "tool", "content": json.dumps({"file_card": card})}
    newer = build_file_card(
        "a.py", "y", revision=2, preview="y", truncated=False, start_line=1, end_line=1, snapshot_ref="filecard:b"
    )
    latest = {"role": "tool", "content": json.dumps({"file_card": newer})}
    folded = fold_file_card_previews([older, latest], semantic_pins=runtime.pin_reasons())
    assert json.loads(folded[0]["content"])["file_card"]["content_preview"] == "x"
    assert json.loads(folded[1]["content"])["file_card"]["content_preview"] == "y"


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
