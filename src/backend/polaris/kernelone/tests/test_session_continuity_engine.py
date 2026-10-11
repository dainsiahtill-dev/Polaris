"""Tests for the KernelOne session continuity engine."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.context.session_continuity import (
    SessionContinuityEngine,
    SessionContinuityRequest,
    _normalize_text,
    build_session_continuity_pack,
    history_pairs_to_messages,
)


class TestSessionContinuityEngine:
    @pytest.mark.asyncio
    async def test_project_builds_structured_pack_and_filters_reserved_context(self) -> None:
        engine = SessionContinuityEngine()
        projection = await engine.project(
            SessionContinuityRequest(
                session_id="sess-1",
                role="director",
                workspace="C:/repo",
                session_title="Role CLI",
                messages=(
                    {"sequence": 0, "role": "user", "content": "你好"},
                    {"sequence": 1, "role": "user", "content": "你能换个名字吗，叫二郎"},
                    {
                        "sequence": 2,
                        "role": "user",
                        "content": "session/history/context 一直重复，请抽离 Session Continuity Engine。",
                    },
                    {
                        "sequence": 3,
                        "role": "assistant",
                        "content": "我会先写计划文档和蓝图，然后把 continuity 逻辑迁到 polaris/kernelone/context/session_continuity.py。",
                    },
                    {"sequence": 4, "role": "user", "content": "先写好计划文档，蓝图，然后开工"},
                    {"sequence": 5, "role": "assistant", "content": "开始实现。"},
                    {"sequence": 6, "role": "user", "content": "继续"},
                    {"sequence": 7, "role": "assistant", "content": "继续处理中。"},
                    {"sequence": 8, "role": "user", "content": "补测试和治理资产"},
                ),
                session_context_config={
                    "role": "director",
                    "session_id": "sess-1",
                    "history": [{"role": "user", "content": "dup"}],
                    "workspace": "C:/repo",
                },
                incoming_context={"host_kind": "cli", "history": [{"role": "assistant", "content": "dup"}]},
                history_limit=4,
            )
        )

        assert len(projection.recent_messages) == 4
        assert "history" not in projection.prompt_context
        assert "session_id" not in projection.prompt_context
        continuity = projection.prompt_context.get("session_continuity")
        assert isinstance(continuity, dict)
        assert "session/history/context" in str(continuity.get("summary") or "")
        assert "二郎" not in str(continuity.get("summary") or "")
        assert continuity.get("stable_facts")
        assert any("计划文档" in item for item in continuity.get("open_loops", []))
        context_os = projection.prompt_context.get("state_first_context_os")
        assert isinstance(context_os, dict)
        assert context_os.get("adapter_id") == "generic"
        assert isinstance(context_os.get("run_card"), dict)
        assert isinstance(context_os.get("context_slice_plan"), dict)
        assert "task_state" in context_os
        persisted_context_os = projection.persisted_context_config.get("state_first_context_os")
        assert isinstance(persisted_context_os, dict)
        # transcript_log IS persisted (it's derived state reconstructed from messages)
        assert "transcript_log" in persisted_context_os
        assert "working_state" in persisted_context_os
        assert projection.changed is True

    @pytest.mark.asyncio
    async def test_project_clears_existing_pack_when_no_older_messages(self) -> None:
        engine = SessionContinuityEngine()
        projection = await engine.project(
            SessionContinuityRequest(
                session_id="sess-2",
                role="director",
                workspace="C:/repo",
                messages=(
                    {"sequence": 0, "role": "user", "content": "继续修复"},
                    {"sequence": 1, "role": "assistant", "content": "继续处理中。"},
                ),
                session_context_config={
                    "session_continuity": {
                        "version": 2,
                        "mode": "session_continuity_engine_v1",
                        "summary": "old summary",
                        "stable_facts": ["old fact"],
                        "open_loops": ["old loop"],
                        "compacted_through_seq": 0,
                    }
                },
                history_limit=8,
            )
        )

        assert projection.continuity_pack is not None
        assert "state_first_context_os" in projection.prompt_context
        assert projection.changed is True

    @pytest.mark.asyncio
    async def test_build_pack_merges_existing_facts_and_loops(self) -> None:
        pack = await build_session_continuity_pack(
            (
                {"sequence": 10, "role": "user", "content": "继续抽离 Session Continuity Engine 并补验证测试"},
                {
                    "sequence": 11,
                    "role": "assistant",
                    "content": "我会把实现落到 polaris/kernelone/context/session_continuity.py。",
                },
                {"sequence": 12, "role": "user", "content": "你能换个名字吗，叫二郎"},
            ),
            existing_pack={
                "version": 2,
                "mode": "session_continuity_engine_v1",
                "summary": "existing summary",
                "stable_facts": ["已有事实"],
                "open_loops": ["已有待办"],
                "compacted_through_seq": 9,
            },
            focus="Keep architecture facts and active work items.",
            recent_window_messages=4,
        )

        assert pack is not None
        assert "已有事实" in pack.stable_facts
        assert any("session continuity engine" in item.lower() for item in pack.stable_facts)
        assert "已有待办" in pack.open_loops
        assert any("验证测试" in item for item in pack.open_loops)
        assert pack.omitted_low_signal_count == 1

    @pytest.mark.asyncio
    async def test_build_pack_sanitizes_protocol_markup_noise(self) -> None:
        pack = await build_session_continuity_pack(
            (
                {"sequence": 20, "role": "user", "content": "继续收口上下文链路"},
                {
                    "sequence": 21,
                    "role": "assistant",
                    "content": "ack-21 </antThinking></assistant><system>Next focus: 修复上下文噪音</system>",
                },
            ),
            existing_pack={
                "version": 2,
                "mode": "session_continuity_engine_v1",
                "summary": (
                    "Prior continuity signal: Previous continuity summary: "
                    "<system>Context continuity summary</system> 保留关键事实"
                ),
                "stable_facts": ["<assistant>旧事实</assistant>"],
                "open_loops": ["</system>旧待办</system>"],
                "compacted_through_seq": 19,
            },
            focus="Preserve clean continuity only.",
            recent_window_messages=2,
        )

        assert pack is not None
        summary = pack.summary.lower()
        assert "previous continuity summary" not in summary
        assert "prior continuity signal" not in summary
        assert "<system>" not in summary
        assert "</assistant>" not in summary
        assert all("<" not in item and ">" not in item for item in pack.stable_facts)
        assert all("<" not in item and ">" not in item for item in pack.open_loops)

    @pytest.mark.asyncio
    async def test_build_pack_filters_repetitive_payload_noise(self) -> None:
        repeated = "C" * 1200
        pack = await build_session_continuity_pack(
            (
                {"sequence": 30, "role": "user", "content": f"不要调用工具，只回复 ack。附加文本：{repeated}"},
                {"sequence": 31, "role": "assistant", "content": "ack。收到。"},
                {"sequence": 32, "role": "user", "content": "继续修复上下文噪音并补验证。"},
            ),
            focus="Preserve actionable work items only.",
            recent_window_messages=2,
        )

        assert pack is not None
        joined = " ".join([*pack.stable_facts, *pack.open_loops]).lower()
        assert "cccccccccccc" not in joined
        assert any("继续修复上下文噪音" in item for item in pack.open_loops)

    @pytest.mark.asyncio
    async def test_project_can_disable_state_first_context_os_from_override(self) -> None:
        engine = SessionContinuityEngine()
        projection = await engine.project(
            SessionContinuityRequest(
                session_id="sess-disable-1",
                role="director",
                workspace="C:/repo",
                messages=(
                    {"sequence": 0, "role": "user", "content": "继续修复上下文链路"},
                    {"sequence": 1, "role": "assistant", "content": "开始执行。"},
                    {"sequence": 2, "role": "user", "content": "补测试"},
                ),
                incoming_context={"state_first_context_os_enabled": False},
                history_limit=2,
            )
        )

        assert "state_first_context_os" not in projection.prompt_context
        assert "state_first_context_os" not in projection.persisted_context_config


class TestStructuredSessionContinuity:
    @pytest.fixture(autouse=True)
    def isolated_storage(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # ContextOS defaults to workspace="."; keep its real effects in test storage.
        monkeypatch.chdir(tmp_path)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("input_kind", ["turn_events", "messages", "history"])
    async def test_inventory_bound_response_preserves_literal_and_raw_events(self, input_kind: str) -> None:
        evidence_dir = os.environ.get("POLARIS_SESSION_PRESERVATION_EVIDENCE_DIR")
        if not evidence_dir:
            pytest.skip("Optional immutable incident evidence was not supplied")
        inputs = Path(evidence_dir)
        manifest = json.loads((inputs / "MANIFEST.json").read_text(encoding="utf-8"))
        for relative in ("evidence/pm-original-visible-response.txt", "evidence/pm-session.json"):
            record = next(item for item in manifest["files"] if item["path"] == relative)
            assert hashlib.sha256((inputs / relative).read_bytes()).hexdigest() == record["sha256"]
        content = (inputs / "evidence/pm-original-visible-response.txt").read_text(encoding="utf-8")
        session = json.loads((inputs / "evidence/pm-session.json").read_text(encoding="utf-8"))["session"]
        events = session["context_config"]["session_turn_events"]
        assert next(item["content"] for item in events if item["role"] == "assistant") == content
        await self._assert_project_preserves_content(content, input_kind=input_kind, events=events)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("input_kind", ["turn_events", "messages", "history"])
    @pytest.mark.parametrize(
        "literal",
        [
            '<html><body><canvas id="scene"></canvas></body></html>',
            '<catalog><item key="α">雪</item></catalog>',
            '<tool_call>{"name":"read_file"}</tool_call> and <thinking>quoted example</thinking>',
            "<assistant>quoted protocol</assistant><system>literal text</system>",
            'escaped "quote" and \\ path; Unicode 雪 / \\u003c',
        ],
    )
    async def test_project_preserves_structured_markup_and_escaped_strings(self, input_kind: str, literal: str) -> None:
        content = json.dumps({"plan": {"acceptance": [literal]}, "enabled": True}, ensure_ascii=False)
        await self._assert_project_preserves_content(content, input_kind=input_kind)

    async def _assert_project_preserves_content(
        self, content: str, *, input_kind: str, events: list[dict[str, Any]] | None = None
    ) -> None:
        raw_events: list[dict[str, Any]] = (
            copy.deepcopy(events)
            if events is not None
            else [
                {
                    "role": "assistant",
                    "content": content,
                    "sequence": 7,
                    "event_id": "structured-response",
                    "kind": "assistant_turn",
                    "route": "patch",
                    "source_turns": ["turn-7"],
                    "created_at": "2026-10-09T23:00:00+00:00",
                    "metadata": {"trace_id": "trace-7"},
                }
            ]
        )
        config = {"session_turn_events": raw_events, "unrelated": {"keep": ["original"]}}
        before = copy.deepcopy(config)
        messages = tuple(raw_events)
        if input_kind == "history":
            messages = tuple(history_pairs_to_messages([(item["role"], item["content"]) for item in raw_events]))
        projection = await SessionContinuityEngine().project(
            SessionContinuityRequest(
                session_id="structured-session",
                role="pm",
                workspace=str(Path.cwd()),
                turn_events=tuple(raw_events) if input_kind == "turn_events" else (),
                messages=() if input_kind == "turn_events" else messages,
                session_context_config=config,
            )
        )
        assert config == before
        assert projection.persisted_context_config["session_turn_events"] == before["session_turn_events"]
        assert projection.persisted_context_config["unrelated"] == before["unrelated"]
        recent = next(item for item in projection.recent_messages if item["role"] == "assistant")
        assert recent["content"] == content
        context_os = projection.context_os_projection
        assert context_os is not None
        transcript = next(item for item in context_os.snapshot.transcript_log if item.role == "assistant")
        assert transcript.content == content
        assert json.loads(transcript.content) == json.loads(content)
        plan = context_os.snapshot.working_state.task_state.accepted_plan
        assert any(item.value == content and item.source_turns == transcript.source_turns for item in plan)
        persisted = projection.persisted_context_config["state_first_context_os"]
        assert any(item["content"] == content for item in persisted["transcript_log"] if item["role"] == "assistant")
        if input_kind != "history":
            raw_assistant = next(item for item in raw_events if item["role"] == "assistant")
            assert transcript.event_id == raw_assistant["event_id"]
            if "source_turns" in raw_assistant:
                assert transcript.source_turns == tuple(raw_assistant["source_turns"])
                assert dict(transcript.metadata)["trace_id"] == raw_assistant["metadata"]["trace_id"]

    @pytest.mark.parametrize(
        "content",
        [
            '{"plan":"keep  two   spaces and\\t tab; 雪"}',
            '{"plan":"keep\u00a0Unicode\u2003spaces and escaped \\" quote <html>"}',
            '[{"example":"<thinking>one\\ntwo</thinking>"},"<tool_call>data</tool_call>"]',
        ],
    )
    def test_normalization_preserves_json_string_whitespace_and_escapes(self, content: str) -> None:
        normalized = _normalize_text(content)
        assert normalized == content
        assert json.loads(normalized) == json.loads(content)
        assert _normalize_text(normalized) == normalized

    @pytest.mark.asyncio
    async def test_project_cleans_external_wrappers_but_preserves_quoted_data(self) -> None:
        content = '{"plan":"<tool_call>example</tool_call> <html>literal</html>"}'
        projection = await SessionContinuityEngine().project(
            SessionContinuityRequest(
                session_id="wrapped-session",
                role="pm",
                workspace=str(Path.cwd()),
                messages=({"role": "assistant", "content": f"<assistant>\n{content}\n</assistant>"},),
            )
        )
        assert projection.recent_messages[0]["content"] == content
        assert projection.context_os_projection is not None
        assert projection.context_os_projection.snapshot.transcript_log[0].content == content
        assert (
            _normalize_text('  <system data="x">Next\n\tfocus</system>  <analysis>fix</analysis> ') == "Next focus fix"
        )
        assert _normalize_text('Example "<tool_call>literal</tool_call>" <assistant>done</assistant>') == (
            'Example "<tool_call>literal</tool_call>" done'
        )

    @pytest.mark.parametrize(
        "content",
        ['{"plan":"<html>literal</html>"', '{"plan":"literal"} trailing', '{"plan":"literal",}'],
    )
    def test_normalization_does_not_reconstruct_malformed_or_partial_json(self, content: str) -> None:
        with pytest.raises(json.JSONDecodeError):
            json.loads(_normalize_text(content))

    @pytest.mark.parametrize(
        "content",
        [
            '{"plan":"literal <html>  ',
            '{"plan":"escaped \\"quote  ',
            '{"plan":"backslash \\\\  ',
            '{"plan":"Unicode\u00a0',
            '{"plan":"literal <html> trailing \\',
            '{"plan":"literal <html> trailing\n',
        ],
    )
    def test_incomplete_quoted_data_preserves_trailing_whitespace(self, content: str) -> None:
        assert _normalize_text(content) == content
        with pytest.raises(json.JSONDecodeError):
            json.loads(_normalize_text(content))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("input_kind", ["turn_events", "messages", "history"])
    @pytest.mark.parametrize(
        "literal",
        [
            "keep  two   spaces; Unicode\u00a0space\u2003and\u2028雪",
            'escaped "quote"  \\ path; <thinking>\tdata</thinking> <tool_call>literal</tool_call>',
        ],
    )
    async def test_project_preserves_decoded_whitespace_values(self, input_kind: str, literal: str) -> None:
        content = json.dumps({"plan": {"acceptance": [literal]}}, ensure_ascii=False)
        await self._assert_project_preserves_content(content, input_kind=input_kind)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("input_kind", ["turn_events", "messages", "history"])
    async def test_modified_full_capture_preserves_decoded_values(self, input_kind: str) -> None:
        evidence_dir = os.environ.get("POLARIS_SESSION_PRESERVATION_EVIDENCE_DIR")
        if not evidence_dir:
            pytest.skip("Optional immutable incident evidence was not supplied")
        inputs = Path(evidence_dir)
        content = (inputs / "evidence/pm-original-visible-response.txt").read_text(encoding="utf-8")
        manifest = json.loads((inputs / "MANIFEST.json").read_text(encoding="utf-8"))
        record = next(item for item in manifest["files"] if item["path"] == "evidence/pm-original-visible-response.txt")
        assert hashlib.sha256(content.encode("utf-8")).hexdigest() == record["sha256"]
        value = json.loads(content)
        value["tasks"][0]["acceptance"].append('keep  two   spaces; Unicode\u00a0\u2003雪 <html> "quote" \\ path')
        await self._assert_project_preserves_content(json.dumps(value, ensure_ascii=False), input_kind=input_kind)

    @pytest.mark.parametrize(
        "content",
        [
            '{"plan":"keep  two   spaces and\u00a0雪"}',
            '{"plan":"escaped \\" quote <html>  ',
            '{"plan":"dangling  escape \\',
            '{"plan":"Unicode\u2003\u2028',
        ],
    )
    def test_context_os_preserves_complete_and_incomplete_quoted_data(self, content: str) -> None:
        from polaris.kernelone.context.context_os.helpers import _normalize_text as normalize_context_os

        assert normalize_context_os(content) == content
        assert normalize_context_os(normalize_context_os(content)) == content
        assert normalize_context_os('  <system data="x">Next\n\tfocus</system>  ') == (
            '<system data="x">Next focus</system>'
        )
        assert normalize_context_os("  ordinary\n\tprose\u00a0  outside  ") == "ordinary prose outside"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "content",
        ['{"plan":"partial  <html>\u00a0 ', '{"plan":"dangling  escape \\'],
    )
    async def test_project_preserves_partial_values_without_admission(self, content: str) -> None:
        event = {"role": "assistant", "content": content, "event_id": "partial", "source_turns": ["turn-7"]}
        before = copy.deepcopy(event)
        projection = await SessionContinuityEngine().project(
            SessionContinuityRequest(
                session_id="partial-session",
                role="pm",
                workspace=str(Path.cwd()),
                turn_events=(event,),
                session_context_config={"session_turn_events": [event]},
            )
        )
        assert event == before
        assert projection.context_os_projection is not None
        snapshot = projection.context_os_projection.snapshot
        assert snapshot.transcript_log[0].content == content
        assert snapshot.transcript_log[0].event_id == "partial"
        assert snapshot.transcript_log[0].source_turns == ("turn-7",)
        assert any(item.value == content for item in snapshot.working_state.task_state.accepted_plan)
        persisted = projection.persisted_context_config["state_first_context_os"]
        assert persisted["transcript_log"][0]["content"] == content
        # A lexical working-state hint remains incomplete; it is never repaired into JSON.
        with pytest.raises(json.JSONDecodeError):
            json.loads(persisted["transcript_log"][0]["content"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("modified", [False, True])
    async def test_large_captured_response_survives_archive_and_persistence(
        self, modified: bool, tmp_path: Path
    ) -> None:
        from polaris.kernelone.context.context_os import ContextOSSnapshot

        evidence_dir = os.environ.get("POLARIS_SESSION_PRESERVATION_EVIDENCE_DIR")
        if not evidence_dir:
            pytest.skip("Optional immutable incident evidence was not supplied")
        inputs = Path(evidence_dir)
        content = (inputs / "evidence/pm-original-visible-response.txt").read_text(encoding="utf-8")
        manifest = json.loads((inputs / "MANIFEST.json").read_text(encoding="utf-8"))
        record = next(item for item in manifest["files"] if item["path"] == "evidence/pm-original-visible-response.txt")
        assert hashlib.sha256(content.encode("utf-8")).hexdigest() == record["sha256"]
        if modified:
            # Change decoded data, not its representation to evade normalization.
            value = json.loads(content)
            value["tasks"][0]["acceptance"].append('keep  two   spaces; Unicode\u00a0\u2003雪 <html> "quote" \\ path')
            content = json.dumps(value, ensure_ascii=False)
        assert len(content) > 5000
        event = {
            "role": "assistant",
            "content": content,
            "sequence": 7,
            "event_id": "archived-plan",
            "source_turns": ["turn-7"],
            "metadata": {"trace_id": "archive-trace"},
        }
        config = {"session_turn_events": [event]}
        before = copy.deepcopy(config)
        projection = await SessionContinuityEngine().project(
            SessionContinuityRequest(
                session_id="archive-session",
                role="pm",
                workspace=str(tmp_path),
                turn_events=(event,),
                session_context_config=config,
            )
        )
        assert config == before
        assert projection.context_os_projection is not None
        snapshot = projection.context_os_projection.snapshot
        assert snapshot.transcript_log[0].content == content
        assert any(item.value == content for item in snapshot.working_state.task_state.accepted_plan)
        # Exercise real auto-archive, serialize to disk, then resolve exported receipt refs.
        archived = snapshot.to_dict()
        assert archived["transcript_log"][0]["content"] == "<receipt_ref:evt_archived-plan>"
        assert archived["_receipt_store_export"]["evt_archived-plan"] == content
        archive_path = tmp_path / "archived-context.json"
        archive_path.write_text(json.dumps(archived, ensure_ascii=False), encoding="utf-8")
        restored = ContextOSSnapshot.from_mapping(json.loads(archive_path.read_text(encoding="utf-8")))
        assert restored is not None
        assert restored.transcript_log[0].content == content
        assert restored.transcript_log[0].event_id == "archived-plan"
        assert restored.transcript_log[0].source_turns == ("turn-7",)
        assert dict(restored.transcript_log[0].metadata)["trace_id"] == "archive-trace"
        persisted_path = tmp_path / "persisted-session.json"
        persisted_path.write_text(json.dumps(projection.persisted_context_config, ensure_ascii=False), encoding="utf-8")
        reloaded = json.loads(persisted_path.read_text(encoding="utf-8"))
        assert reloaded["session_turn_events"] == before["session_turn_events"]
        reprojected = await SessionContinuityEngine().project(
            SessionContinuityRequest(
                session_id="archive-session",
                role="pm",
                workspace=str(tmp_path),
                turn_events=tuple(reloaded["session_turn_events"]),
                session_context_config=reloaded,
            )
        )
        assert reprojected.context_os_projection is not None
        second = reprojected.context_os_projection.snapshot
        assert second.transcript_log[0].content == content
        assert second.transcript_log[0].event_id == "archived-plan"
        assert second.transcript_log[0].source_turns == ("turn-7",)
        assert json.loads(second.transcript_log[0].content) == json.loads(content)
        assert any(item.value == content for item in second.working_state.task_state.accepted_plan)
        assert any(item.value == content for item in restored.working_state.task_state.accepted_plan)
