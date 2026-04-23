"""Tests for the KernelOne session continuity engine."""

from __future__ import annotations

import pytest
from polaris.kernelone.context.session_continuity import (
    SessionContinuityEngine,
    SessionContinuityRequest,
    build_session_continuity_pack,
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
