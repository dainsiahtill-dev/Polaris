from __future__ import annotations

import json
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.engineering_command_cards import build_command_card
from polaris.cells.roles.runtime.internal.engineering_epoch_kimi import KimiEpochPool
from polaris.cells.roles.runtime.internal.engineering_epoch_runtime import EpochRuntime, facts_for_epoch
from polaris.cells.roles.runtime.internal.engineering_file_cards import build_file_card
from polaris.cells.roles.runtime.internal.engineering_memory_cards import (
    attach_run_card_section,
    build_context_memory_messages,
    build_known_facts_cards,
    build_memory_cards,
    run_card_section,
)


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
    def __init__(self, *, cite_command: bool = False, reject: bool = False, call_refs: tuple[str, ...] = ()) -> None:
        self.cite_command = cite_command
        self.reject = reject
        self.call_refs = call_refs

    async def summarize_many(self, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.reject:
            return [{"validation_result": "REJECTED", "error": "invalid response"}]
        refs = list(self.call_refs)
        if self.cite_command:
            refs.append(json.loads(requests[0]["content"])["commands"][0]["evidence_ref"])
        return [
            {
                "endpoint_index": 0,
                "validation_result": "VALID",
                "result": {
                    "node_id": "epoch-0001-0010",
                    "snapshot_id": "epoch-0001-0010",
                    "summary": "No source files were changed.",
                    "summary_kind": "model_inference",
                    "coverage": {"observed_line_ranges": [[1, 1]], "summary_complete": False},
                    "evidence_refs": refs,
                    "uncertain": True,
                    "reason": "基于关闭 epoch。",
                },
            }
        ]


def _closed_epoch() -> list[dict[str, object]]:
    # turn_seq is the journal seq of turn_started, not the activity ordinal.
    events: list[dict[str, object]] = []

    def append(kind: str, **payload: object) -> None:
        events.append({"seq": len(events) + 1, "kind": kind, "payload": payload})

    for activity in range(1, 31):
        turn = len(events) + 1
        append("turn_started")
        if activity in (1, 2, 11):
            path = {1: "early.ts", 2: "failed.ts", 11: "next.ts"}[activity]
            # Provider call IDs may repeat across turns.
            append("tool_started", call_id="write", turn_seq=turn, tool="write_file", arguments={"file": path})
            append("tool_observed", call_id="write", turn_seq=turn, tool="write_file", ok=activity != 2)
        if activity == 8:
            append("tool_started", call_id="edit", turn_seq=turn, tool="edit_file", arguments={"file": "pending.ts"})
        if activity == 9:
            append("verification_completed", verification="failed")
        if activity == 10:
            append(
                "tool_started", call_id="cmd", turn_seq=turn, tool="execute_command", arguments={"command": "npm test"}
            )
            append("tool_observed", call_id="cmd", turn_seq=turn, tool="execute_command", ok=False, exit_code=2)
    return events


def test_epoch_uses_observed_outcomes_across_entire_closed_range() -> None:
    request, _, fingerprint = facts_for_epoch({}, _closed_epoch())
    candidate = request["candidates"][0]
    facts = json.loads(candidate["content"])
    assert facts["activity_range"] == [1, 10]
    assert facts["file_changes"] == ["early.ts"]
    assert facts["verification_history"] == ["failed"]
    assert [(item["file"], item["outcome"]) for item in facts["file_operations"]] == [
        ("early.ts", "succeeded"),
        ("failed.ts", "failed"),
        ("pending.ts", "unknown"),
    ]
    assert [(item["command"], item["exit_code"]) for item in facts["commands"]] == [("npm test", 2)]

    changed = _closed_epoch()
    changed[1] = {
        **changed[1],
        "payload": {"tool": "write_file", "turn_seq": 1, "call_id": "write", "arguments": {"file": "other.ts"}},
    }
    assert facts_for_epoch({}, changed)[2] != fingerprint


async def test_epoch_runtime_keeps_model_inference_out_of_journal_facts_injected_as_memory() -> None:
    runtime = EpochRuntime(_Pool())
    events = _closed_epoch()
    request, refs, fingerprint = facts_for_epoch({}, events)
    runtime.schedule(request, refs, fingerprint=fingerprint)
    awaitable = runtime._job
    assert awaitable is not None
    await awaitable
    card = runtime.poll()
    assert card is not None and card["kind"] == "epoch_card"
    assert card["model_advice"]["summary"] == "No source files were changed."
    assert card["summary_kind"] == "journal_facts"
    memory = build_context_memory_messages({}, [{"kind": "epoch_card_completed", "payload": card}], limit_chars=20_000)
    rendered = str(memory)
    assert "early.ts" in rendered and "npm test" in rendered
    assert "No source files were changed." not in rendered


async def test_epoch_devourer_can_highlight_an_observed_command_for_the_next_request() -> None:
    runtime = EpochRuntime(_Pool(cite_command=True))
    request, refs, fingerprint = facts_for_epoch({}, _closed_epoch())
    runtime.schedule(request, refs, fingerprint=fingerprint)
    assert runtime._job is not None
    await runtime._job
    card = runtime.poll()
    assert card is not None
    assert card["advice_status"] == "evidence_selected"
    memory = build_context_memory_messages({}, [{"kind": "epoch_card_completed", "payload": card}], limit_chars=20_000)
    assert "Devourer recall" in str(memory)
    assert "No source files were changed." not in str(memory)


async def test_rejected_epoch_advice_preserves_host_observations() -> None:
    runtime = EpochRuntime(_Pool(reject=True))
    request, refs, fingerprint = facts_for_epoch({}, _closed_epoch())
    runtime.schedule(request, refs, fingerprint=fingerprint)
    assert runtime._job is not None
    await runtime._job
    card = runtime.poll()
    assert card is not None
    assert card["advice_status"] == "rejected" and card["advice_error"] == "invalid response"
    memory = build_context_memory_messages({}, [{"kind": "epoch_card_completed", "payload": card}], limit_chars=20_000)
    assert "early.ts" in str(memory) and "Devourer recall" not in str(memory)


async def test_epoch_resolves_unique_legacy_call_refs_and_ignores_reused_ids() -> None:
    events = _closed_epoch()
    request, refs, fingerprint = facts_for_epoch({}, events)
    assert "cmd" in refs.evidence_refs and "write" not in refs.evidence_refs
    runtime = EpochRuntime(_Pool(call_refs=("cmd", "write")))
    runtime.schedule(request, refs, fingerprint=fingerprint)
    assert runtime._job is not None
    await runtime._job
    card = runtime.poll()
    assert card is not None
    assert card["recall_refs"] == [request["candidates"][0]["host_facts"]["commands"][0]["evidence_ref"]]


def test_epoch_unknown_effect_keeps_canonical_path_and_ignores_cross_turn_observation() -> None:
    events = _activity_events(30)
    events[1:1] = [
        {
            "seq": 100,
            "kind": "tool_started",
            "payload": {
                "turn_seq": 1,
                "call_id": "same",
                "tool": "edit_file",
                "arguments": {"file": "canonical.ts", "path": "alias.ts"},
            },
        },
        {
            "seq": 101,
            "kind": "tool_observed",
            "payload": {"turn_seq": 999, "call_id": "same", "tool": "edit_file", "ok": True},
        },
        {
            "seq": 102,
            "kind": "tool_observed",
            "payload": {"turn_seq": 1, "call_id": "same", "tool": "edit_file", "ok": False, "interrupted": True},
        },
    ]
    candidate = facts_for_epoch({}, events)[0]["candidates"][0]
    facts = json.loads(candidate["content"])
    assert facts["file_changes"] == []
    assert facts["file_operations"][0]["file"] == "canonical.ts"
    assert facts["file_operations"][0]["outcome"] == "unknown"


def test_legacy_epoch_memory_is_rederived_only_from_a_complete_journal() -> None:
    legacy: dict[str, Any] = {"id": "epoch-0001-0010", "summary": "No source files were changed."}
    events: list[dict[str, Any]] = [{"kind": "run_started", "seq": 1, "payload": {}}]
    for event in _closed_epoch():
        events.append({**event, "seq": len(events) + 1})
    events.append({"kind": "epoch_card_completed", "seq": len(events) + 1, "payload": legacy})
    cards = [card for card in build_memory_cards({}, events) if card["kind"] == "epoch_card"]
    assert len(cards) == 1 and "early.ts" in cards[0]["summary"]
    assert "No source files were changed." not in cards[0]["summary"]
    assert legacy["summary"] == "No source files were changed."
    for partial in (events[2:], events[:2] + events[3:], [events[-1]]):
        assert all(card["kind"] != "epoch_card" for card in build_memory_cards({}, partial))


@pytest.mark.parametrize("probe_ref", ["event:18", "event:999", "event:13", "epoch-0001-0010"])
async def test_kimi_optional_probe_is_admitted_only_with_exact_observed_refs(
    probe_ref: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = KimiEpochPool(base_url="https://kimi.example", api_key="secret", model="kimi")
    probe = {
        "open_question": "Did the failure occur before the test body?",
        "discriminating_check": "Compare the recorded setup and test output.",
        "evidence_refs": [probe_ref],
    }
    response = {"summary": "Historical notes infer all tests passed.", "exploration": probe}
    monkeypatch.setattr(pool, "_post", lambda _: json.dumps(response))
    events = [{"seq": 0, "kind": "run_updated", "payload": {"workspace_revision": 7}}, *_closed_epoch()]
    run = {"role": "director", "workspace_revision": 7, "instruction": "Investigate npm test"}
    request, refs, fingerprint = facts_for_epoch(run, events)
    runtime = EpochRuntime(pool)
    runtime.schedule(request, refs, fingerprint=fingerprint)
    assert runtime._job is not None
    await runtime._job
    card = runtime.poll()
    assert card is not None and card["validation_result"] == "VALID"
    assert "Did the failure" not in card["summary"]
    memory = str(
        build_context_memory_messages(
            run, [*events, {"kind": "epoch_card_completed", "payload": card}], limit_chars=4000
        )
    )
    assert "Historical notes infer all tests passed." not in memory and "early.ts" in memory
    if probe_ref == "event:18":
        assert card["advice_status"] == "probe_proposed"
        assert "Did the failure" in memory and "Compare the recorded setup" in memory and "event:18" in memory
        assert "untrusted" in memory.lower()
    else:
        assert card["exploration_status"] == "rejected" and "Did the failure" not in memory


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


def test_memory_distinguishes_a_proposed_choice_from_quoted_requirements() -> None:
    text = "需求写着“选择距离近的对象”。我决定用索引保持相同距离时的稳定顺序。"
    events = [
        _command_event("node check.js", "TypeError: missing table", 1, 1),
        {"seq": 2, "kind": "message", "payload": {"message": {"role": "assistant", "content": text}}},
    ]
    rendered = build_context_memory_messages({"instruction": "实现查询"}, events)[0]["content"]
    assert "选择距离近的对象" not in rendered
    assert "我决定用索引" in rendered and "模型自述" in rendered
    assert "旧文案" not in rendered and "read_command_result" not in rendered
    assert "TypeError" in rendered


def test_known_facts_entry_has_a_recallable_line() -> None:
    """索引给了地址却没给可回忆的内容，模型只能重读整文件（实测 rereads 高）。"""

    import json
    import sqlite3
    from pathlib import Path

    workspace = Path("/home/dains/.polaris/leak-lab/L1-06")
    db = workspace / ".polaris/runtime/engineering/sessions.sqlite3"
    if not db.is_file():
        import pytest

        pytest.skip("leak-lab 工作区不存在")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    events = [
        {"seq": seq, "kind": kind, "role": role, "payload": json.loads(payload) if payload else {}}
        for seq, kind, role, payload in conn.execute(
            "select seq,kind,role,payload from events order by seq"
        )
    ]
    revision = conn.execute("select workspace_revision from runs order by rowid desc limit 1").fetchone()[0]
    conn.close()
    cards = build_known_facts_cards(
        {"workspace": str(workspace), "workspace_revision": revision, "instruction": "x"},
        events,
        can_read=lambda p: True,
    )
    assert cards and cards[0]["entries"], "该真实 run 应产出索引"
    entry = cards[0]["entries"][0]
    assert entry["summary"].strip(), "条目必须带一行可回忆的摘要"
    # 结构摘要里不该再出现类型关键字。
    assert "unsigned" not in entry["summary"].split("：")[-1]
