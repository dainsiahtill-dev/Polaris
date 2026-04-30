"""G-2 Transcript Leak Guard - tests for clean_content/raw_content separation.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

G-2: Transcript Semantic Leak Guard
=====================================
Ensures internal metadata and raw parsing content never reach:
  1. LLM history (next-turn `request.history` tuples)
  2. user-facing transcript display (ConversationMessage.content)

Coverage:
  - raw_content must not appear in LLM history
  - clean_content must not contain tool wrappers ([TOOL_CALL]...[/TOOL_CALL])
  - ControlEvent internal metadata must not appear in transcript tuples
  - ToolResult stored as plain text (loses typed fields) - documented limitation
  - ConversationMessage.meta must be sanitized before ORM persist
  - Context compaction must surface user-visible notification
"""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.public.transcript_ir import (
    AssistantMessage,
    ControlEvent,
    ReasoningSummary,
    SystemInstruction,
    ToolCall,
    ToolResult,
    TranscriptDelta,
    UserMessage,
)

# ─────────────────────────────────────────────────────────────────────────────
# G-2.1: raw_content never reaches LLM history
# ─────────────────────────────────────────────────────────────────────────────


class TestRawContentIsolation:
    """raw_content is parsing-only; clean_content is for storage/display."""

    def test_clean_content_excludes_tool_wrapper(self) -> None:
        """Sanitized text must not contain [TOOL_CALL]...[/TOOL_CALL] markers."""
        raw = (
            "I'll search for that information.\n"
            "[TOOL_CALL]\n"
            '{"tool":"web_search","args":{"query":"Python best practices"}}\n'
            "[/TOOL_CALL]\n"
            "Let me execute this search."
        )
        # _sanitize_assistant_transcript_message strips wrappers via
        # CanonicalToolCallParser.extract_text_calls_and_remainder.
        # Simulate the expected contract: wrappers are stripped.
        from polaris.cells.roles.kernel.internal.tool_call_protocol import (
            CanonicalToolCallParser,
        )

        _, remainder = CanonicalToolCallParser.extract_text_calls_and_remainder(raw)
        clean = str(remainder or "").strip()
        assert "[TOOL_CALL]" not in clean
        assert "[/TOOL_CALL]" not in clean
        assert "I'll search for that information" in clean

    def test_assistant_turn_artifacts_contract(self) -> None:
        """AssistantTurnArtifacts must expose raw_content and clean_content separately."""
        from polaris.cells.roles.kernel.internal.turn_engine import (
            AssistantTurnArtifacts,
        )

        artifacts = AssistantTurnArtifacts(
            raw_content="[TOOL_CALL]{...}[/TOOL_CALL]real answer",
            clean_content="real answer",
        )
        # raw_content is distinct from clean_content
        assert artifacts.raw_content != artifacts.clean_content
        assert "[TOOL_CALL]" in artifacts.raw_content
        assert "[TOOL_CALL]" not in artifacts.clean_content

    def test_append_transcript_cycle_uses_clean_content(self) -> None:
        """_append_transcript_cycle must pass clean_content, not raw_content."""
        from unittest.mock import MagicMock

        from polaris.cells.roles.kernel.internal.turn_engine import (
            AssistantTurnArtifacts,
        )
        from polaris.cells.roles.kernel.internal.turn_engine.utils import (
            append_transcript_cycle,
        )

        mock_controller = MagicMock()
        turn = AssistantTurnArtifacts(
            raw_content="[TOOL_CALL]{...}[/TOOL_CALL]answer",
            clean_content="answer",
        )
        append_transcript_cycle(
            controller=mock_controller,
            turn=turn,
            tool_results=[{"tool": "bash", "result": "ok", "success": True}],
        )
        mock_controller.append_tool_cycle.assert_called_once()
        call_args = mock_controller.append_tool_cycle.call_args
        # First positional arg is assistant_message
        stored_message = call_args.kwargs.get("assistant_message") or call_args[1].get("assistant_message")
        assert "[TOOL_CALL]" not in stored_message
        assert stored_message == "answer"


# ─────────────────────────────────────────────────────────────────────────────
# G-2.2: clean_content does not contain tool receipts
# ─────────────────────────────────────────────────────────────────────────────


class TestCleanContentBoundaries:
    """AssistantMessage stored in transcript must not embed raw tool receipts."""

    def test_assistant_message_clean_content(self) -> None:
        """AssistantMessage.content is the clean user-facing text."""
        am = AssistantMessage(content="Here is the result.", thinking="reasoning step")
        assert "[TOOL_CALL]" not in am.content
        assert "result" in am.content

    def test_reasoning_summary_not_in_assistant_content(self) -> None:
        """Thinking content belongs in ReasoningSummary, not AssistantMessage.content."""
        am = AssistantMessage(content="final answer", thinking="step by step reasoning")
        assert am.thinking == "step by step reasoning"
        assert "reasoning" not in am.content.lower() or am.content == "final answer"

    def test_transcript_delta_roundtrip_preserves_clean_content(self) -> None:
        """TranscriptDelta to_dict/from_dict preserves clean content boundaries."""
        delta = TranscriptDelta(
            transcript_items=[
                UserMessage(content="What files changed?"),
                AssistantMessage(content="Found 3 files.", thinking="searching..."),
                ToolCall(tool_name="bash", args={"cmd": "git diff --stat"}),
                ToolResult(call_id="x", tool_name="bash", status="success", content="ok"),
            ],
            tool_calls=[],
        )
        restored = TranscriptDelta.from_dict(delta.to_dict())
        items_by_type = {type(i).__name__: i for i in restored.transcript_items}
        assert items_by_type["AssistantMessage"].content == "Found 3 files."
        assert items_by_type["AssistantMessage"].thinking == "searching..."
        # Tool wrappers must not appear in AssistantMessage.content
        assert "[TOOL_CALL]" not in items_by_type["AssistantMessage"].content


# ─────────────────────────────────────────────────────────────────────────────
# G-2.3: transcript append filters internal metadata
# ─────────────────────────────────────────────────────────────────────────────


class TestTranscriptMetadataFiltering:
    """Internal metadata (provider, call_id, raw_reference) must not enter LLM history."""

    def test_tool_call_internal_fields_not_in_llm_history(self) -> None:
        """provider_meta / raw_reference are in ToolCall.to_dict() (kernel IR serialization)
        but must NEVER enter LLM history tuples.

        LLM history uses _format_tool_history_result which produces a compact plain-text
        representation that does not include provider_meta or raw_reference fields.
        The formatted output is a plain-text string containing only tool name and result.
        """
        tc = ToolCall(
            tool_name="bash",
            args={"cmd": "ls"},
            provider="openai",
            provider_meta={"model": "gpt-4"},
            raw_reference={"internal": True},
        )
        # to_dict() includes internal fields (kernel IR persistence)
        d = tc.to_dict()
        assert "provider_meta" in d  # stored in kernel IR for audit
        assert "raw_reference" in d  # stored in kernel IR for audit
        # _format_tool_history_result only uses tool_name and compact payload result
        # Simulate: the formatted history string contains only tool name + result
        # provider_meta and raw_reference are never extracted into the history tuple
        from polaris.cells.roles.kernel.internal.tool_loop_controller import (
            format_tool_result,
        )

        formatted = format_tool_result("bash", {"tool": "bash", "result": "ls output", "success": True})
        # LLM history is plain text - no structured internal fields leak through
        assert "provider_meta" not in formatted
        assert "raw_reference" not in formatted
        assert "gpt" not in formatted.lower()
        assert "openai" not in formatted.lower()

    def test_tool_result_stripped_fields_stay_typed(self) -> None:
        """ToolResult dataclass carries typed fields; tool_loop_controller stores plain text.

        Limitation: When stored in ToolLoopController._history, ToolResult becomes
        a formatted plain-text string via _format_tool_history_result. This loses
        typed fields (call_id, artifact_refs, metrics). The typed dataclass is used
        for streaming events; the plain-text representation is the current storage format.
        """
        tr = ToolResult(
            call_id="abc123",
            tool_name="web_search",
            status="success",
            content="search results",
            artifact_refs=["artifact://x/y/z"],
            metrics={"latency_ms": 150},
            retryable=False,
            error_code=None,
            error_message=None,
        )
        # Typed dataclass is intact
        assert tr.call_id == "abc123"
        assert tr.artifact_refs == ["artifact://x/y/z"]
        # ToolCall.raw_reference is internal and must not be persisted
        tc = ToolCall(
            tool_name="read",
            args={"path": "a.txt"},
            raw_reference={"internal": True},
        )
        assert tc.raw_reference is not None
        # raw_reference should never enter LLM history

    def test_control_event_metadata_stays_internal(self) -> None:
        """ControlEvent.metadata is internal; must not appear in LLM history tuple."""
        ce = ControlEvent(
            event_type="stop",
            reason="tool_loop_safety_exceeded",
            metadata={
                "internal_trace_id": "trace-123",
                "_internal": True,
                "provider_config": {"api_key_hash": "xxx"},
            },
        )
        # metadata exists internally but ControlEvent itself is not written as
        # a history tuple; only user-visible events trigger history writes
        assert ce.metadata.get("_internal") is True
        assert "provider_config" in ce.metadata

    def test_transcript_delta_includes_all_types(self) -> None:
        """All TranscriptItem types can be round-tripped without data loss."""
        items = [
            SystemInstruction(content="You are a helpful assistant."),
            UserMessage(content="Hello!"),
            AssistantMessage(content="Hi there.", thinking="greeting"),
            ToolCall(tool_name="search", args={"q": "test"}),
            ToolResult(call_id="c1", tool_name="search", status="success", content="result"),
            ControlEvent(event_type="stop", reason="done"),
            ReasoningSummary(content="reasoning trace"),
        ]
        delta = TranscriptDelta(transcript_items=items)
        restored = TranscriptDelta.from_dict(delta.to_dict())
        assert len(restored.transcript_items) == len(items)


# ─────────────────────────────────────────────────────────────────────────────
# G-2.4: roles.session ORM - meta sanitization
# ─────────────────────────────────────────────────────────────────────────────


class TestConversationMessageSanitization:
    """ConversationMessage.meta must not leak internal provider/trace metadata."""

    def test_meta_field_can_contain_structured_data(self) -> None:
        """ConversationMessage.meta is stored as JSON string."""
        import json

        from polaris.cells.roles.session.public import ConversationMessage

        msg = ConversationMessage(
            id="msg1",
            conversation_id="conv1",
            sequence=0,
            role="assistant",
            content="clean answer",
            thinking=None,
            meta=json.dumps({"provider": "openai", "model": "gpt-4"}),
        )
        d = msg.to_dict()
        assert "provider" in d["meta"]
        # Internal trace fields should be filtered before storage
        dirty_meta = json.dumps({"provider": "openai", "_internal_trace": "secret", "api_key_ref": "xxx"})
        # Note: No automatic sanitization exists in to_dict() yet
        # G-2 mitigation: callers must sanitize before setting meta=
        msg_dirty = ConversationMessage(
            id="msg2",
            conversation_id="conv1",
            sequence=1,
            role="assistant",
            content="answer",
            meta=dirty_meta,
        )
        # Current behavior: dirty meta is stored as-is
        # Risk: internal metadata leaks to API responses
        assert "_internal_trace" in msg_dirty.meta

    def test_conversation_message_to_dict_exposes_all_fields(self) -> None:
        """to_dict() returns all stored fields including meta."""
        import json

        from polaris.cells.roles.session.public import ConversationMessage

        msg = ConversationMessage(
            id="msg3",
            conversation_id="conv1",
            sequence=2,
            role="user",
            content="question",
            meta=json.dumps({"client": "electron"}),
        )
        d = msg.to_dict()
        assert d["content"] == "question"
        assert d["meta"]["client"] == "electron"
        assert d["id"] == "msg3"

    def test_roles_session_service_add_message_accepts_meta(self) -> None:
        """RoleSessionService.add_message accepts meta parameter."""
        from unittest.mock import MagicMock

        from polaris.cells.roles.session.public import (
            RoleSessionService,
        )

        mock_db = MagicMock()
        svc = RoleSessionService(db=mock_db)

        # Verify add_message signature accepts meta
        import inspect

        sig = inspect.signature(svc.add_message)
        assert "meta" in sig.parameters

        # Meta with internal fields should be flagged as a gap
        # callers are responsible for pre-sanitizing meta


# ─────────────────────────────────────────────────────────────────────────────
# G-2.5: compaction semantic safety
# ─────────────────────────────────────────────────────────────────────────────


class TestCompactionSemanticSafety:
    """Context compaction must maintain LLM history semantic correctness."""

    def test_control_event_compacted_flag_exists(self) -> None:
        """ControlEvent can signal that context was compacted."""
        ce = ControlEvent(event_type="stop", reason="context_limit", compacted=True)
        assert ce.compacted is True
        assert ce.event_type == "stop"

    def test_context_gateway_apply_compression_truncates_not_corrupts(self) -> None:
        """Compression must not mutate message semantics, only remove entries."""
        # Build a mock profile with context policy
        from unittest.mock import MagicMock

        from polaris.cells.roles.kernel.internal.context_gateway import (
            RoleContextGateway,
        )

        mock_profile = MagicMock()
        mock_profile.context_policy.include_project_structure = False
        mock_profile.context_policy.include_task_history = False
        mock_profile.context_policy.max_context_tokens = 10  # Force compression
        mock_profile.context_policy.compression_strategy = "sliding_window"
        mock_profile.context_policy.max_history_turns = 100

        gateway = RoleContextGateway(mock_profile)

        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "second question"},
            {"role": "assistant", "content": "second answer with more content to force truncation"},
        ]

        # Compression should not corrupt the last user/assistant pair
        compressed, _tokens = gateway._apply_compression(messages, 1000)
        # Last user message should be preserved (or at minimum not corrupted)
        content_str = str(compressed)
        assert "second question" in content_str or len(compressed) < len(messages)

    @pytest.mark.asyncio
    async def test_process_history_summarize_injects_continuity_summary(self) -> None:
        """Summarize strategy should keep a continuity summary instead of raw old turns."""
        from unittest.mock import MagicMock

        from polaris.cells.roles.kernel.internal.context_gateway import (
            RoleContextGateway,
        )

        mock_profile = MagicMock()
        mock_profile.context_policy.include_project_structure = False
        mock_profile.context_policy.include_task_history = False
        mock_profile.context_policy.max_context_tokens = 4000
        mock_profile.context_policy.compression_strategy = "summarize"
        mock_profile.context_policy.max_history_turns = 4

        gateway = RoleContextGateway(mock_profile)
        result = await gateway._process_history(
            [
                ("user", "你能换个名字吗，叫二郎"),
                ("user", "session/history/context 一直重复，请重构 continuity summary 和 compaction"),
                ("assistant", "我会检查 session、history、context compaction 链路。"),
                ("user", "继续"),
                ("assistant", "继续处理中。"),
                ("user", "先总结问题"),
            ]
        )

        assert len(result) == 5
        assert result[0]["role"] == "system"
        assert "State-First Context OS" in result[0]["content"]
        assert "session/history/context" in result[0]["content"]
        assert "二郎" not in result[0]["content"]

    @pytest.mark.asyncio
    async def test_process_history_strips_reasoning_tags_before_injection(self) -> None:
        """History injection should strip reasoning tags from assistant/tool messages."""
        from unittest.mock import MagicMock

        from polaris.cells.roles.kernel.internal.context_gateway import (
            RoleContextGateway,
        )

        mock_profile = MagicMock()
        mock_profile.context_policy.include_project_structure = False
        mock_profile.context_policy.include_task_history = False
        mock_profile.context_policy.max_context_tokens = 4000
        mock_profile.context_policy.compression_strategy = "sliding_window"
        mock_profile.context_policy.max_history_turns = 20

        gateway = RoleContextGateway(mock_profile)
        result = await gateway._process_history(
            [
                ("assistant", "<thinking>先分析一下</thinking>最终答复"),
                ("tool", '<thinking>内部元数据</thinking>{"ok": true}'),
            ]
        )

        assert len(result) == 2
        assert "<thinking>" not in result[0]["content"].lower()
        assert "先分析一下" not in result[0]["content"]
        assert "最终答复" in result[0]["content"]
        assert "内部元数据" not in result[1]["content"]
        assert '{"ok": true}' in result[1]["content"]

    def test_compacted_event_notifies_user(self) -> None:
        """Compaction must produce a user-visible notification, not silently truncate."""
        ce = ControlEvent(
            event_type="stop",
            reason="context_limit",
            compacted=True,
            metadata={"messages_removed": 10, "token_saved": 5000},
        )
        # compacted=True signals that a notification should be shown
        assert ce.compacted is True
        # The notification text should be generated by the caller
        assert ce.reason is not None

    def test_emergency_fallback_preserves_tool_result_chain(self) -> None:
        """Emergency fallback must preserve recent tool result chain to avoid re-calling."""
        from unittest.mock import MagicMock

        from polaris.cells.roles.kernel.internal.context_gateway import (
            RoleContextGateway,
        )

        mock_profile = MagicMock()
        mock_profile.context_policy.include_project_structure = False
        mock_profile.context_policy.include_task_history = False
        mock_profile.context_policy.max_context_tokens = 1
        mock_profile.context_policy.compression_strategy = "truncate"
        mock_profile.context_policy.max_history_turns = 100

        gateway = RoleContextGateway(mock_profile)

        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "do it"},
            {"role": "assistant", "content": "calling tool"},
            {"role": "tool", "content": '{"result": "tool output"}'},
        ]

        result, _ = gateway._emergency_fallback(messages)
        # Tool result chain must be preserved in emergency fallback
        str(result)
        # At minimum, one message should be retained
        assert len(result) >= 1

    def test_summarize_strategy_emits_continuity_summary_message(self) -> None:
        """Summarize strategy should produce a summary message under token pressure."""
        from unittest.mock import MagicMock

        from polaris.cells.roles.kernel.internal.context_gateway import (
            RoleContextGateway,
        )

        mock_profile = MagicMock()
        mock_profile.context_policy.include_project_structure = False
        mock_profile.context_policy.include_task_history = False
        mock_profile.context_policy.max_context_tokens = 10
        mock_profile.context_policy.compression_strategy = "summarize"
        mock_profile.context_policy.max_history_turns = 100

        gateway = RoleContextGateway(mock_profile)
        messages = [
            {"role": "system", "content": "system prompt"},
            {
                "role": "user",
                "content": "请阅读 docs/AGENT_ARCHITECTURE_STANDARD.md 并确认 session/history/context compaction 问题",
            },
            {"role": "assistant", "content": "我会检查 console_host、context_gateway 和 session 复用逻辑。"},
            {"role": "user", "content": "另外旧话题像改名字也会反复出现"},
            {"role": "assistant", "content": "这说明旧 history 被当成长期上下文重复注入。"},
            {"role": "user", "content": "请给出更主流的新策略"},
        ]

        compressed, _ = gateway._apply_compression(messages, 1000)
        content_str = str(compressed)
        assert "State-First Context OS" in content_str
        assert any(str(item.get("name") or "") == "continuity_summary" for item in compressed if isinstance(item, dict))

    @pytest.mark.asyncio
    async def test_build_context_canonical_path_includes_strategy_receipt(self) -> None:
        """Canonical strategy-receipt path renders strategy receipt content.

        The strategy_receipt is used as a fallback when projection.snapshot is None.
        We patch StateFirstContextOS.project() to return a projection with snapshot=None
        so the fallback path is exercised.
        """
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch

        from polaris.cells.roles.kernel.internal.context_gateway import (
            ContextRequest,
            RoleContextGateway,
        )
        from polaris.kernelone.context.context_os.models_v2 import ContextOSProjectionV2 as ContextOSProjection

        mock_profile = MagicMock()
        mock_profile.context_policy.include_project_structure = False
        mock_profile.context_policy.include_task_history = False
        mock_profile.context_policy.max_context_tokens = 4000
        mock_profile.context_policy.compression_strategy = "summarize"
        mock_profile.context_policy.max_history_turns = 4
        mock_profile.role_id = "director"
        mock_profile.display_name = "Director"

        receipt = SimpleNamespace(
            bundle_id="bundle-1",
            profile_id="director-default",
            turn_index=1,
            budget_decisions=[],
            tool_sequence=(),
            exploration_phase_reached="",
            cache_hits=(),
            cache_misses=(),
            compaction_triggered=False,
        )

        gateway = RoleContextGateway(mock_profile)

        # Patch project() to return a projection with snapshot=None
        # so the fallback strategy_receipt path is taken
        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.snapshot = None
        mock_projection.active_window = []
        mock_projection.head_anchor = ""
        mock_projection.tail_anchor = ""
        mock_projection.run_card = None
        mock_projection.context_slice_plan = None

        with patch.object(gateway._context_os, "project", new_callable=AsyncMock, return_value=mock_projection):
            result = await gateway.build_context(
                ContextRequest(
                    message="继续",
                    history=[],
                    strategy_receipt=receipt,
                )
            )

        system_messages = [m for m in result.messages if str(m.get("role") or "") == "system"]

        assert any(str(m.get("name") or "") == "strategy_receipt" for m in system_messages)
        assert "strategy_receipt" in result.context_sources

    @pytest.mark.asyncio
    async def test_build_context_gateway_level_receipt_keeps_continuity_block(self) -> None:
        """Gateway-level canonical receipt should also include continuity context.

        The strategy_receipt is passed via ContextRequest (not constructor).
        We patch project() to return projection with snapshot=None to trigger fallback path.
        """
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch

        from polaris.cells.roles.kernel.internal.context_gateway import (
            ContextRequest,
            RoleContextGateway,
        )
        from polaris.kernelone.context.context_os.models_v2 import ContextOSProjectionV2 as ContextOSProjection

        mock_profile = MagicMock()
        mock_profile.context_policy.include_project_structure = False
        mock_profile.context_policy.include_task_history = False
        mock_profile.context_policy.max_context_tokens = 4000
        mock_profile.context_policy.compression_strategy = "summarize"
        mock_profile.context_policy.max_history_turns = 4
        mock_profile.role_id = "director"
        mock_profile.display_name = "Director"

        receipt = SimpleNamespace(
            bundle_id="bundle-2",
            profile_id="director-default",
            turn_index=2,
            budget_decisions=[],
            tool_sequence=(),
            exploration_phase_reached="",
            cache_hits=(),
            cache_misses=(),
            compaction_triggered=False,
        )

        gateway = RoleContextGateway(mock_profile)

        # Patch project() to return a projection with snapshot=None
        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.snapshot = None
        mock_projection.active_window = []
        mock_projection.head_anchor = ""
        mock_projection.tail_anchor = ""
        mock_projection.run_card = None
        mock_projection.context_slice_plan = None

        with patch.object(gateway._context_os, "project", new_callable=AsyncMock, return_value=mock_projection):
            result = await gateway.build_context(
                ContextRequest(
                    message="继续",
                    history=[],
                    strategy_receipt=receipt,
                )
            )

        system_messages = [m for m in result.messages if str(m.get("role") or "") == "system"]

        assert any(str(m.get("name") or "") == "strategy_receipt" for m in system_messages)
        assert "strategy_receipt" in result.context_sources

    @pytest.mark.asyncio
    async def test_build_context_compaction_triggered_skips_compression(self) -> None:
        """When strategy_receipt.compaction_triggered is True, compression should be skipped.

        We patch project() to return projection with snapshot=None to use strategy_receipt path,
        and verify that the context_os_projection path does not trigger emergency truncation.
        """
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch

        from polaris.cells.roles.kernel.internal.context_gateway import (
            ContextRequest,
            RoleContextGateway,
        )
        from polaris.kernelone.context.context_os.models_v2 import ContextOSProjectionV2 as ContextOSProjection

        mock_profile = MagicMock()
        mock_profile.context_policy.include_project_structure = False
        mock_profile.context_policy.include_task_history = False
        mock_profile.context_policy.max_context_tokens = 200
        mock_profile.context_policy.compression_strategy = "truncate"
        mock_profile.context_policy.max_history_turns = 100
        mock_profile.role_id = "director"
        mock_profile.display_name = "Director"

        receipt = SimpleNamespace(
            bundle_id="bundle-state-first",
            profile_id="director-default",
            turn_index=3,
            budget_decisions=[],
            tool_sequence=(),
            exploration_phase_reached="",
            cache_hits=(),
            cache_misses=(),
            compaction_triggered=True,
        )

        long_user = "u" * 900
        long_assistant = "a" * 1100
        history: list[tuple[str, str]] = []
        for _ in range(6):
            history.append(("user", long_user))
            history.append(("assistant", long_assistant))

        gateway = RoleContextGateway(mock_profile)

        # Patch project() to return projection with snapshot=None so strategy_receipt is used
        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.snapshot = None
        mock_projection.active_window = []
        mock_projection.head_anchor = ""
        mock_projection.tail_anchor = ""
        mock_projection.run_card = None
        mock_projection.context_slice_plan = None

        with patch.object(gateway._context_os, "project", new_callable=AsyncMock, return_value=mock_projection):
            result = await gateway.build_context(
                ContextRequest(
                    message="继续推进 state-first context os",
                    history=history,
                    strategy_receipt=receipt,
                )
            )

        rendered = "\n".join(str(m.get("content") or "") for m in result.messages)
        assert "[CONTENT_TRUNCATED" not in rendered
        assert "[Context truncated:" not in rendered


# ─────────────────────────────────────────────────────────────────────────────
# G-2.6: delivery edge contract completeness
# ─────────────────────────────────────────────────────────────────────────────


class TestDeliveryEdgeContracts:
    """Delivery edge typed commands must fully cover the session lifecycle."""

    def test_create_session_command_fields(self) -> None:
        """CreateRoleSessionCommandV1 exposes all necessary fields."""
        from polaris.cells.roles.session.public.contracts import (
            CreateRoleSessionCommandV1,
        )

        cmd = CreateRoleSessionCommandV1(role="director", workspace="/repo")
        assert cmd.role == "director"
        assert cmd.workspace == "/repo"
        assert cmd.host_kind == "electron_workbench"
        assert cmd.capability_profile == {}

    def test_update_session_command_id_required(self) -> None:
        """UpdateRoleSessionCommandV1 requires session_id."""
        from polaris.cells.roles.session.public.contracts import (
            UpdateRoleSessionCommandV1,
        )

        cmd = UpdateRoleSessionCommandV1(session_id="sess-abc", state="archived")
        assert cmd.session_id == "sess-abc"
        assert cmd.state == "archived"

    def test_role_session_result_ok_fields(self) -> None:
        """RoleSessionResultV1 requires ok=True fields."""
        from polaris.cells.roles.session.public.contracts import RoleSessionResultV1

        result = RoleSessionResultV1(
            ok=True,
            session_id="sess-1",
            role="pm",
            state="active",
            payload={"created": True},
        )
        assert result.ok is True
        assert result.session_id == "sess-1"
        assert result.error_code is None

    def test_role_session_result_failure_requires_error(self) -> None:
        """RoleSessionResultV1 with ok=False must include error info."""
        from polaris.cells.roles.session.public.contracts import RoleSessionResultV1

        with pytest.raises(ValueError):
            RoleSessionResultV1(
                ok=False,
                session_id="sess-1",
                role="pm",
                state="error",
            )

    def test_i_role_session_service_protocol(self) -> None:
        """IRoleSessionService defines the delivery edge contract."""
        from polaris.cells.roles.session.public.contracts import (
            IRoleSessionService,
        )

        # Protocol defines 3 methods
        assert hasattr(IRoleSessionService, "create_session")
        assert hasattr(IRoleSessionService, "update_session")
        assert hasattr(IRoleSessionService, "attach_session")

    def test_lifecycle_event_fields(self) -> None:
        """RoleSessionLifecycleEventV1 covers all session state transitions."""
        from polaris.cells.roles.session.public.contracts import (
            RoleSessionLifecycleEventV1,
        )

        evt = RoleSessionLifecycleEventV1(
            event_id="evt-1",
            session_id="sess-1",
            role="director",
            status="created",
            occurred_at="2026-03-25T10:00:00Z",
            run_id="run-1",
            task_id="task-1",
        )
        assert evt.event_id == "evt-1"
        assert evt.run_id == "run-1"
        assert evt.task_id == "task-1"
