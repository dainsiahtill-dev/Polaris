from __future__ import annotations

import json

from polaris.cells.roles.runtime.internal.engineering_command_cards import build_command_card
from polaris.cells.roles.runtime.internal.engineering_epoch_runtime import EpochRuntime, facts_for_epoch
from polaris.cells.roles.runtime.internal.engineering_file_cards import build_file_card
from polaris.cells.roles.runtime.internal.engineering_memory_cards import build_memory_cards
from polaris.cells.roles.runtime.internal.engineering_semantic_advice import CandidateRefs


def _file_event(path: str, preview: str, revision: int, seq: int, **extra: object) -> dict[str, object]:
    card = build_file_card(
        path,
        preview,
        revision=revision,
        preview=preview,
        truncated=False,
        start_line=1,
        end_line=1,
        snapshot_ref=f"filecard:{path}:{revision}",
    )
    card.update(extra)
    return {
        "seq": seq,
        "kind": "tool_observed",
        "payload": {"tool": "read_file", "ok": True, "content": json.dumps({"file_card": card})},
    }


def _command_event(command: str, output: str, exit_code: int, seq: int) -> dict[str, object]:
    card = build_command_card(command, output, "", exit_code, f"command:{seq}")
    return {
        "seq": seq,
        "kind": "tool_observed",
        "payload": {"tool": "execute_command", "ok": exit_code == 0, "content": json.dumps({"command_card": card})},
    }


def test_memory_cards_cover_long_term_engineering_facts() -> None:
    events = [
        _file_event("a.py", "def alpha():\n    pass\n", 1, 1),
        _file_event(
            "a.py",
            "def alpha():\n    return 1\n",
            2,
            2,
            recovered_from="filecard:a.py:1",
            pin_reason="current_reasoning",
        ),
        _command_event("node test.js", "ReferenceError: root is not defined", 1, 3),
        {
            "seq": 4,
            "kind": "verification_completed",
            "payload": {
                "verification": "failed",
                "checks": [{"call_id": "verify-1", "content": "[FAIL] 交互：刹车无效 —— 速度未下降"}],
            },
        },
        {
            "seq": 5,
            "kind": "tool_observed",
            "payload": {
                "tool": "report_review",
                "ok": True,
                "content": json.dumps(
                    {"clause_findings": [{"clause": "支持刹车", "status": "fail", "evidence": "verify-1"}]}
                ),
            },
        },
    ]
    cards = build_memory_cards({"instruction": "支持刹车"}, events)
    kinds = {card["kind"] for card in cards}
    assert kinds == {
        "failure_cluster",
        "interface_contract",
        "requirement_trace",
        "tool_pattern",
        "file_diff",
        "evidence_lease",
    }
    failure = next(card for card in cards if card["kind"] == "failure_cluster" and "刹车" in card["title"])
    assert failure["unresolved"] is True
    requirement = next(card for card in cards if card["kind"] == "requirement_trace")
    assert requirement["status"] == "fail"


def test_epoch_selection_waits_until_fifty_activities_behind() -> None:
    events: list[dict[str, object]] = []
    seq = 0
    for activity in range(1, 62):
        seq += 1
        events.append({"seq": seq, "kind": "turn_started", "payload": {}})
        seq += 1
        events.append({"seq": seq, "kind": "message", "payload": {"content": f"step-{activity}"}})
    request, refs, fingerprint = facts_for_epoch({"instruction": "build"}, events)
    assert refs.node_ids == {"epoch-0001-0010"}
    assert len(fingerprint) == 64
    assert request["candidates"][0]["node_id"] == "epoch-0001-0010"


class _Pool:
    async def summarize_many(self, requests):
        return [
            {
                "endpoint_index": 0,
                "validation_result": "VALID",
                "result": {
                    "node_id": "epoch-0001-0010",
                    "snapshot_id": "epoch-0001-0010",
                    "summary": "完成基础模块，验证仍有失败。",
                    "summary_kind": "model_inference",
                    "coverage": {"observed_line_ranges": [[1, 1]], "summary_complete": False},
                    "evidence_refs": [],
                    "uncertain": True,
                    "reason": "基于关闭 epoch。",
                },
            }
        ]


async def test_epoch_runtime_returns_validated_epoch_card() -> None:
    runtime = EpochRuntime(_Pool())
    refs = CandidateRefs(node_ids={"epoch-0001-0010"}, slice_ids={"slice_epoch"}, evidence_refs=set())
    request = {
        "candidates": [
            {
                "node_id": "epoch-0001-0010",
                "snapshot_id": "epoch-0001-0010",
                "content": "step",
                "coverage": {"observed_line_ranges": [[1, 1]]},
            }
        ]
    }
    runtime.schedule(request, refs, fingerprint="epoch-fingerprint")
    awaitable = runtime._job
    assert awaitable is not None
    await awaitable
    card = runtime.poll()
    assert card is not None and card["kind"] == "epoch_card"
    assert card["summary"] == "完成基础模块，验证仍有失败。"
