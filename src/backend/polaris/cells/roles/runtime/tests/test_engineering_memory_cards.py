from __future__ import annotations

import json

from polaris.cells.roles.runtime.internal.engineering_command_cards import build_command_card
from polaris.cells.roles.runtime.internal.engineering_epoch_runtime import EpochRuntime, facts_for_epoch
from polaris.cells.roles.runtime.internal.engineering_file_cards import build_file_card
from polaris.cells.roles.runtime.internal.engineering_memory_cards import (
    attach_run_card_section,
    build_context_memory_messages,
    build_memory_cards,
    run_card_section,
)
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
        "verification_contract",
        "evidence_lease",
    }
    failure = next(card for card in cards if card["kind"] == "failure_cluster" and "刹车" in card["title"])
    assert failure["unresolved"] is True
    requirement = next(card for card in cards if card["kind"] == "requirement_trace")
    assert requirement["status"] == "fail"
    contract = next(card for card in cards if card["kind"] == "verification_contract")
    assert contract["status"] == "failed"
    assert contract["evidence_refs"] == ["verify-1"]


def test_context_memory_injection_surfaces_unresolved_failures_and_contracts() -> None:
    events = [
        _command_event("node test.js", "ReferenceError: root is not defined", 1, 1),
        {
            "seq": 2,
            "kind": "verification_completed",
            "payload": {
                "verification": "failed",
                "workspace_revision": 4,
                "checks": [{"call_id": "verify-1", "content": "[FAIL] 交互：刹车无效"}],
            },
        },
    ]
    messages = build_context_memory_messages({"instruction": "支持刹车"}, events)
    assert len(messages) == 1 and messages[0]["role"] == "system"
    assert "未解决失败聚类" in messages[0]["content"]
    assert "ReferenceError" in messages[0]["content"]
    assert "最近验证契约" in messages[0]["content"]


def test_epoch_selection_waits_until_twenty_activities_behind() -> None:
    """A 50-activity lag meant the first card could not appear before turn 60.

    Every live run confirmed it: all 17 runs with a card produced their first one
    at turn 61. At 20 the first epoch is usable around turn 30, with run left.
    """

    stale = facts_for_epoch({"instruction": "build"}, _activity_events(29))
    assert stale[0] == {"candidates": []}
    assert stale[1].node_ids == set()

    fresh = facts_for_epoch({"instruction": "build"}, _activity_events(30))
    assert fresh[1].node_ids == {"epoch-0001-0010"}
    assert len(fresh[2]) == 64
    assert fresh[0]["candidates"][0]["node_id"] == "epoch-0001-0010"


def _activity_events(count: int) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    seq = 0
    for activity in range(1, count + 1):
        seq += 1
        events.append({"seq": seq, "kind": "turn_started", "payload": {}})
        seq += 1
        events.append({"seq": seq, "kind": "message", "payload": {"content": f"step-{activity}"}})
    return events


def _epoch_completed(epoch_id: str) -> dict[str, object]:
    return {
        "seq": 0,
        "kind": "epoch_card_completed",
        "payload": {"id": epoch_id, "kind": "epoch_card", "fingerprint": epoch_id},
    }


def test_epoch_selection_advances_past_reported_epochs() -> None:
    # 40 activities leave exactly two eligible epochs: 10 and 20.
    events = _activity_events(40)
    _request, refs, _fingerprint = facts_for_epoch({"instruction": "build"}, events)
    assert refs.node_ids == {"epoch-0001-0010"}

    events.append(_epoch_completed("epoch-0001-0010"))
    _request, refs, _fingerprint = facts_for_epoch({"instruction": "build"}, events)
    assert refs.node_ids == {"epoch-0011-0020"}

    # Every eligible epoch is reported: selection is empty, not stuck on epoch 10.
    events.append(_epoch_completed("epoch-0011-0020"))
    request, refs, fingerprint = facts_for_epoch({"instruction": "build"}, events)
    assert request == {"candidates": []}
    assert refs.node_ids == set()
    assert fingerprint == ""


def test_epoch_selection_ignores_reported_epoch_by_node_id() -> None:
    events = _activity_events(40)
    events.append(
        {
            "seq": 0,
            "kind": "epoch_card_completed",
            "payload": {"node_id": "epoch-0001-0010", "fingerprint": "epoch-0001-0010"},
        }
    )
    _request, refs, _fingerprint = facts_for_epoch({"instruction": "build"}, events)
    assert refs.node_ids == {"epoch-0011-0020"}


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


def test_epoch_cards_reach_the_injected_memory() -> None:
    """实测 2026-11-25：吞噬兽出了 11 张卡却从未进入请求 ——
    build_memory_cards 里没有 epoch 构建器，过滤 epoch_card 那段是死代码。"""

    events = [
        {
            "seq": 1,
            "kind": "epoch_card_completed",
            "payload": {
                "id": "epoch-0001-0010",
                "summary": "已改 src/garden.ts 与 src/render.ts；npm test 31 项通过；重置复现未验证",
                "evidence_refs": ["call_abc"],
                "error": None,
            },
        },
        {
            "seq": 2,
            "kind": "epoch_card_completed",
            "payload": {"id": "epoch-0002-0020", "summary": "", "error": "empty"},
        },
    ]
    cards = [card for card in build_memory_cards({"instruction": "做花园"}, events) if card.get("kind") == "epoch_card"]
    assert len(cards) == 1 and cards[0]["id"] == "epoch-0001-0010"
    messages = build_context_memory_messages({"instruction": "做花园"}, events, limit_chars=20_000)
    assert any("已改 src/garden.ts" in str(message.get("content")) for message in messages)


def test_run_card_reaches_the_provider_memory_message() -> None:
    """制图师叙述原本 0 到达：第二条 system 消息会被 native 编码器并入角色身份。
    并入这条每轮都在场、不参与角色身份的记忆卡 —— 零新增消息，100% 到达。
    """

    card = {
        "phase": "EDIT",
        "intent": "修复月相亮度映射",
        "narrative": {"intent": "修复月相亮度映射", "reason": "rev12 的 tsc 失败指向 brightness.ts"},
        "next_actions": [{"action": "先修最近一次失败：TS6234", "why": "宿主推导", "expected": "签名消失"}],
    }
    messages = list(build_context_memory_messages({"instruction": "做花园"}, [], run_card=card))
    assert len(messages) == 1 and messages[0]["role"] == "system"
    assert "ContextOS RunCard" in messages[0]["content"]
    assert "当前阶段：EDIT" in messages[0]["content"]
    assert "TS6234" in messages[0]["content"]

    # 已注入的记忆卡消息也能在 run card 算出来之后补上（同一批消息对象）。
    existing = [{"role": "system", "content": "ContextOS 长期记忆（摘要，非验收证据）：\n- 失败聚类 X"}]
    attach_run_card_section(existing, card)
    assert "ContextOS RunCard" in existing[0]["content"]
    attach_run_card_section(existing, card)  # 幂等
    assert existing[0]["content"].count("ContextOS RunCard") == 1
    assert run_card_section({}) == ""
