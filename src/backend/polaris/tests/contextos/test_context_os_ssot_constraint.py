"""ContextOS SSOT 约束验证测试

P0-1 SSOT 约束验证:
- ToolLoopController 必须从 context_os_snapshot 获取历史
- 不再支持 request.history 回退

P0-2 ContextEvent 元数据保留:
- ContextEvent 保留所有元数据 (event_id, sequence, route, dialog_act)
- _history 类型为 list[ContextEvent]
- append_tool_cycle() 创建 ContextEvent
- to_tuple() 向后兼容
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.tool_loop_controller import (
    ContextEvent,
    ToolLoopController,
    ToolLoopSafetyPolicy,
)
from polaris.cells.roles.profile.internal.schema import (
    RoleExecutionMode,
    RolePromptPolicy,
    RoleToolPolicy,
    RoleTurnRequest,
)

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class _MinimalProfile:
    """Minimal RoleProfile for testing."""

    role_id: str = "test_role"
    display_name: str = "Test Role"
    description: str = "Test role for SSOT constraint tests"
    responsibilities: list[str] = field(default_factory=list)
    provider_id: str = ""
    model: str = ""
    prompt_policy: RolePromptPolicy = field(default_factory=lambda: RolePromptPolicy(core_template_id="default"))
    tool_policy: RoleToolPolicy = field(default_factory=RoleToolPolicy)
    version: str = "1.0.0"


def _make_request(
    *,
    message: str = "test message",
    context_override: dict[str, Any] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
) -> RoleTurnRequest:
    """Create a minimal RoleTurnRequest for testing."""
    return RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=".",
        message=message,
        context_override=context_override,
        tool_results=tool_results,
        task_id="test-task-1",
    )


def _make_snapshot_with_transcript(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a context_os_snapshot dict from transcript events.

    Note: When events is empty, the snapshot will have transcript_log=[] but
    _extract_snapshot_history returns _NO_SNAPSHOT for empty results.
    Therefore, tests requiring valid snapshot should provide at least 1 event.
    """
    return {
        "context_os_snapshot": {
            "version": 1,
            "mode": "state_first_context_os_v1",
            "adapter_id": "test",
            "transcript_log": events,
            "working_state": {},
            "artifact_store": [],
            "episode_store": [],
            "updated_at": "2026-03-30T00:00:00Z",
        }
    }


# Default seed event for tests that need valid snapshots with non-empty history
_DEFAULT_SEED_EVENT: list[dict[str, Any]] = [
    {
        "event_id": "seed_evt_0",
        "role": "user",
        "content": "seed message",
        "sequence": 0,
        "metadata": {"dialog_act": "status_ack"},
    },
]


# -----------------------------------------------------------------------------
# Test: ContextEvent Metadata Preservation
# -----------------------------------------------------------------------------


class TestContextEventMetadata:
    """P0-2: ContextEvent 元数据保留测试"""

    def test_event_has_required_fields(self) -> None:
        """验证 ContextEvent 包含所有必需字段"""
        event = ContextEvent(
            event_id="evt_001",
            role="user",
            content="Hello",
            sequence=0,
            metadata={"dialog_act": "affirm", "route": "clear"},
        )
        assert event.event_id == "evt_001"
        assert event.role == "user"
        assert event.content == "Hello"
        assert event.sequence == 0
        assert event.metadata["dialog_act"] == "affirm"
        assert event.metadata["route"] == "clear"

    def test_event_id_is_accessible(self) -> None:
        """验证 event_id 字段可访问"""
        event = ContextEvent(
            event_id="unique_evt_id",
            role="assistant",
            content="response",
            sequence=5,
        )
        assert event.event_id == "unique_evt_id"

    def test_sequence_is_preserved(self) -> None:
        """验证 sequence 字段被保留"""
        event = ContextEvent(
            event_id="evt_seq",
            role="tool",
            content="tool result",
            sequence=42,
        )
        assert event.sequence == 42

    def test_dialog_act_property(self) -> None:
        """验证 dialog_act 属性从 metadata 提取"""
        event = ContextEvent(
            event_id="evt_da",
            role="user",
            content="yes please",
            sequence=0,
            metadata={"dialog_act": "affirm"},
        )
        assert event.dialog_act == "affirm"

    def test_dialog_act_defaults_to_empty_string(self) -> None:
        """验证 dialog_act 缺失时返回空字符串"""
        event = ContextEvent(
            event_id="evt_no_da",
            role="user",
            content="hello",
            sequence=0,
            metadata={},
        )
        assert event.dialog_act == ""

    def test_route_property(self) -> None:
        """验证 route 属性从 metadata 提取"""
        event = ContextEvent(
            event_id="evt_route",
            role="assistant",
            content="thinking",
            sequence=1,
            metadata={"route": "patch"},
        )
        assert event.route == "patch"

    def test_route_defaults_to_empty_string(self) -> None:
        """验证 route 缺失时返回空字符串"""
        event = ContextEvent(
            event_id="evt_no_route",
            role="assistant",
            content="thinking",
            sequence=1,
            metadata={},
        )
        assert event.route == ""

    def test_metadata_is_dict(self) -> None:
        """验证 metadata 类型为 dict"""
        event = ContextEvent(
            event_id="evt_meta",
            role="user",
            content="test",
            sequence=0,
            metadata={"key": "value", "nested": {"a": 1}},
        )
        assert isinstance(event.metadata, dict)
        assert event.metadata["key"] == "value"
        assert event.metadata["nested"]["a"] == 1

    def test_kind_property(self) -> None:
        """验证 kind 属性从 metadata 提取 (Blueprint定义的语义事件类型)"""
        event = ContextEvent(
            event_id="evt_kind",
            role="tool",
            content="tool result",
            sequence=0,
            metadata={"kind": "tool_result"},
        )
        assert event.kind == "tool_result"

    def test_kind_defaults_to_empty_string(self) -> None:
        """验证 kind 缺失时返回空字符串"""
        event = ContextEvent(
            event_id="evt_no_kind",
            role="assistant",
            content="thinking",
            sequence=0,
            metadata={},
        )
        assert event.kind == ""

    def test_kind_distinguishes_tool_call_from_tool_result(self) -> None:
        """验证 kind 能区分 tool_call 和 tool_result (两者 role='tool')"""
        tool_call_event = ContextEvent(
            event_id="evt_tool_call",
            role="tool",
            content="read_file(path='foo.py')",
            sequence=0,
            metadata={"kind": "tool_call"},
        )
        tool_result_event = ContextEvent(
            event_id="evt_tool_result",
            role="tool",
            content="file content here",
            sequence=1,
            metadata={"kind": "tool_result"},
        )
        assert tool_call_event.role == "tool"
        assert tool_result_event.role == "tool"
        assert tool_call_event.kind == "tool_call"
        assert tool_result_event.kind == "tool_result"

    def test_source_turns_in_metadata(self) -> None:
        """验证 source_turns 被保留在 metadata 中"""
        event = ContextEvent(
            event_id="evt_source",
            role="assistant",
            content="summary",
            sequence=5,
            metadata={"source_turns": ["t_001", "t_002", "t_003"]},
        )
        assert event.metadata.get("source_turns") == ["t_001", "t_002", "t_003"]

    def test_artifact_id_in_metadata(self) -> None:
        """验证 artifact_id 被保留在 metadata 中"""
        event = ContextEvent(
            event_id="evt_artifact",
            role="assistant",
            content="archived content reference",
            sequence=0,
            metadata={"artifact_id": "artifact_12345"},
        )
        assert event.metadata.get("artifact_id") == "artifact_12345"

    def test_created_at_in_metadata(self) -> None:
        """验证 created_at 被保留在 metadata 中"""
        event = ContextEvent(
            event_id="evt_ts",
            role="user",
            content="timestamped message",
            sequence=0,
            metadata={"created_at": "2026-03-30T10:30:00Z"},
        )
        assert event.metadata.get("created_at") == "2026-03-30T10:30:00Z"


class TestContextEventBackwardCompatibility:
    """P0-2: ContextEvent 向后兼容测试"""

    def test_to_tuple_returns_correct_format(self) -> None:
        """验证 to_tuple() 返回 (role, content) 元组"""
        event = ContextEvent(
            event_id="evt_tuple",
            role="assistant",
            content="Hello, how can I help?",
            sequence=0,
        )
        result = event.to_tuple()
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert result == ("assistant", "Hello, how can I help?")

    def test_to_tuple_loses_metadata(self) -> None:
        """验证 to_tuple() 丢失 metadata (预期行为)"""
        event = ContextEvent(
            event_id="evt_lose_meta",
            role="user",
            content="test",
            sequence=0,
            metadata={"dialog_act": "affirm", "route": "clear"},
        )
        result = event.to_tuple()
        # 元组不携带 metadata
        assert result == ("user", "test")

    def test_from_tuple_creates_context_event(self) -> None:
        """验证 from_tuple() 从元组创建 ContextEvent"""
        tuple_event = ("user", "Hello")
        event = ContextEvent.from_tuple(tuple_event, sequence=0)
        assert isinstance(event, ContextEvent)
        assert event.role == "user"
        assert event.content == "Hello"
        assert event.sequence == 0
        assert event.metadata == {}  # 元组没有 metadata


# -----------------------------------------------------------------------------
# Test: ToolLoopController SSOT Constraint
# -----------------------------------------------------------------------------


class TestToolLoopControllerSSOT:
    """P0-1: ToolLoopController SSOT 约束测试"""

    def test_requires_context_os_snapshot(self) -> None:
        """验证 context_os_snapshot 由 RoleTurnRequest 自动引导 (SSOT Bootstrap).

        注意: RoleTurnRequest._post_init() 确保 context_os_snapshot 始终存在，
        因此 ToolLoopController 永远不会看到 context_os_snapshot 缺失的情况。
        这是 SSOT Bootstrap 设计的选择: context_os_snapshot 永远被保证存在。
        """
        profile = _MinimalProfile()
        request = _make_request(message="test without snapshot")

        # RoleTurnRequest._post_init() 会自动注入空的 context_os_snapshot
        # 因此 ToolLoopController 应该接受它 (不抛出异常)
        controller = ToolLoopController(
            request=request,
            profile=profile,  # type: ignore[arg-type]
            safety_policy=ToolLoopSafetyPolicy(),
        )
        assert controller is not None
        # 新 session: _history 为空 (因为 transcript_log 是空的)
        assert controller._history == []

    def test_rejects_empty_context_override(self) -> None:
        """验证空 context_override 被 RoleTurnRequest 自动补充 snapshot.

        RoleTurnRequest._post_init() 会自动注入空的 context_os_snapshot，
        因此 ToolLoopController 应该接受它。
        """
        profile = _MinimalProfile()
        request = _make_request(message="test", context_override={})

        # RoleTurnRequest._post_init() 注入 context_os_snapshot
        controller = ToolLoopController(
            request=request,
            profile=profile,  # type: ignore[arg-type]
            safety_policy=ToolLoopSafetyPolicy(),
        )
        assert controller is not None
        assert controller._history == []

    def test_rejects_dict_without_snapshot(self) -> None:
        """验证 context_override dict 但无 snapshot 时 RoleTurnRequest 自动补充.

        RoleTurnRequest._post_init() 会注入空的 context_os_snapshot。
        """
        profile = _MinimalProfile()
        request = _make_request(
            message="test",
            context_override={"other_key": "value"},
        )

        # RoleTurnRequest._post_init() 注入 context_os_snapshot
        controller = ToolLoopController(
            request=request,
            profile=profile,  # type: ignore[arg-type]
            safety_policy=ToolLoopSafetyPolicy(),
        )
        assert controller is not None
        assert controller._history == []

    def test_accepts_valid_snapshot(self) -> None:
        """验证有效的 context_os_snapshot 被接受"""
        profile = _MinimalProfile()
        snapshot_ctx = _make_snapshot_with_transcript(_DEFAULT_SEED_EVENT)
        request = _make_request(
            message="test with snapshot",
            context_override=snapshot_ctx,
        )

        # 不应抛出异常
        controller = ToolLoopController(
            request=request,
            profile=profile,  # type: ignore[arg-type]
            safety_policy=ToolLoopSafetyPolicy(),
        )
        assert controller is not None

    def test_accepts_empty_snapshot_for_new_session(self) -> None:
        """验证空的 context_os_snapshot 被接受（用于新 session 引导）"""
        profile = _MinimalProfile()
        # 新 session: context_os_snapshot 存在但 transcript_log 为空
        empty_snapshot_ctx = {
            "context_os_snapshot": {
                "version": 1,
                "mode": "state_first_context_os_v1",
                "adapter_id": "generic",
                "transcript_log": [],
                "working_state": {},
                "artifact_store": [],
                "episode_store": [],
                "updated_at": "",
            }
        }
        request = _make_request(
            message="first message in new session",
            context_override=empty_snapshot_ctx,
        )

        # 不应抛出异常 - 新 session 应该能引导
        controller = ToolLoopController(
            request=request,
            profile=profile,  # type: ignore[arg-type]
            safety_policy=ToolLoopSafetyPolicy(),
        )
        assert controller is not None
        assert controller._history == []

    def test_history_seeded_from_snapshot(self) -> None:
        """验证 _history 从 snapshot.transcript_log 种子化"""
        profile = _MinimalProfile()
        events = [
            {
                "event_id": "evt_1",
                "role": "user",
                "content": "Hello",
                "sequence": 0,
                "metadata": {"dialog_act": "affirm"},
            },
            {
                "event_id": "evt_2",
                "role": "assistant",
                "content": "Hi there!",
                "sequence": 1,
                "metadata": {},
            },
        ]
        snapshot_ctx = _make_snapshot_with_transcript(events)
        request = _make_request(
            message="test",
            context_override=snapshot_ctx,
        )

        controller = ToolLoopController(
            request=request,
            profile=profile,  # type: ignore[arg-type]
            safety_policy=ToolLoopSafetyPolicy(),
        )

        # _history 应该是 list[ContextEvent]
        assert isinstance(controller._history, list)
        assert len(controller._history) == 2
        assert all(isinstance(e, ContextEvent) for e in controller._history)

        # 验证元数据被保留
        assert controller._history[0].event_id == "evt_1"
        assert controller._history[0].role == "user"
        assert controller._history[0].content == "Hello"
        assert controller._history[0].sequence == 0
        assert controller._history[0].dialog_act == "affirm"

    def test_history_seeded_preserves_kind(self) -> None:
        """验证 _history 种子化时保留 kind 字段 (P0 Bug Fix)"""
        profile = _MinimalProfile()
        events = [
            {
                "event_id": "evt_tool_call",
                "role": "tool",
                "content": "read_file(path='foo.py')",
                "sequence": 0,
                "kind": "tool_call",
                "metadata": {},
            },
            {
                "event_id": "evt_tool_result",
                "role": "tool",
                "content": "file content here",
                "sequence": 1,
                "kind": "tool_result",
                "metadata": {},
            },
            {
                "event_id": "evt_user",
                "role": "user",
                "content": "show me the file",
                "sequence": 2,
                "kind": "user_turn",
                "source_turns": ["t_001"],
                "artifact_id": "artifact_abc",
                "created_at": "2026-03-30T10:00:00Z",
                "metadata": {},
            },
        ]
        snapshot_ctx = _make_snapshot_with_transcript(events)
        request = _make_request(
            message="test",
            context_override=snapshot_ctx,
        )

        controller = ToolLoopController(
            request=request,
            profile=profile,  # type: ignore[arg-type]
            safety_policy=ToolLoopSafetyPolicy(),
        )

        assert len(controller._history) == 3

        # tool_call 事件: kind 在 metadata 中
        assert controller._history[0].role == "tool"
        assert controller._history[0].kind == "tool_call"
        assert controller._history[0].metadata.get("kind") == "tool_call"

        # tool_result 事件: kind 在 metadata 中
        assert controller._history[1].role == "tool"
        assert controller._history[1].kind == "tool_result"
        assert controller._history[1].metadata.get("kind") == "tool_result"

        # user_turn 事件: 完整元数据保留
        assert controller._history[2].role == "user"
        assert controller._history[2].kind == "user_turn"
        assert controller._history[2].metadata.get("source_turns") == ["t_001"]
        assert controller._history[2].metadata.get("artifact_id") == "artifact_abc"
        assert controller._history[2].metadata.get("created_at") == "2026-03-30T10:00:00Z"


# -----------------------------------------------------------------------------
# Test: _history Type and append_tool_cycle
# -----------------------------------------------------------------------------


class TestHistoryIsListOfContextEvent:
    """P0-2: _history 类型验证"""

    def test_history_type_is_list(self) -> None:
        """验证 _history 类型是 list"""
        profile = _MinimalProfile()
        snapshot_ctx = _make_snapshot_with_transcript(_DEFAULT_SEED_EVENT)
        request = _make_request(
            message="test",
            context_override=snapshot_ctx,
        )

        controller = ToolLoopController(
            request=request,
            profile=profile,  # type: ignore[arg-type]
            safety_policy=ToolLoopSafetyPolicy(),
        )

        assert isinstance(controller._history, list)

    def test_history_contains_context_events(self) -> None:
        """验证 _history 包含 ContextEvent 实例"""
        profile = _MinimalProfile()
        events = [
            {
                "event_id": "evt_first",
                "role": "user",
                "content": "First message",
                "sequence": 0,
                "metadata": {},
            },
        ]
        snapshot_ctx = _make_snapshot_with_transcript(events)
        request = _make_request(
            message="test",
            context_override=snapshot_ctx,
        )

        controller = ToolLoopController(
            request=request,
            profile=profile,  # type: ignore[arg-type]
            safety_policy=ToolLoopSafetyPolicy(),
        )

        assert len(controller._history) == 1
        assert isinstance(controller._history[0], ContextEvent)
        assert controller._history[0].event_id == "evt_first"


class TestAppendToolCycleUsesContextEvent:
    """P0-2: append_tool_cycle 使用 ContextEvent"""

    def test_append_tool_cycle_creates_context_events(self) -> None:
        """验证 append_tool_cycle() 创建 ContextEvent"""
        profile = _MinimalProfile()
        snapshot_ctx = _make_snapshot_with_transcript(_DEFAULT_SEED_EVENT)
        request = _make_request(
            message="test",
            context_override=snapshot_ctx,
        )

        controller = ToolLoopController(
            request=request,
            profile=profile,  # type: ignore[arg-type]
            safety_policy=ToolLoopSafetyPolicy(),
        )

        # 手动设置 _last_consumed_message (build_context_request 有运行时依赖问题)
        controller._last_consumed_message = "test user message"

        # 调用 append_tool_cycle
        controller.append_tool_cycle(
            assistant_message="I am responding",
            tool_results=[
                {
                    "tool": "read_file",
                    "success": True,
                    "result": {"content": "file content"},
                }
            ],
        )

        # 验证所有事件都是 ContextEvent
        # 至少有 user message, assistant message, 和 tool result
        assert len(controller._history) >= 3
        for event in controller._history:
            assert isinstance(event, ContextEvent)

    def test_append_tool_cycle_preserves_assistant_event_id(self) -> None:
        """验证 append_tool_cycle 创建的 assistant 事件有正确 event_id"""
        profile = _MinimalProfile()
        snapshot_ctx = _make_snapshot_with_transcript(_DEFAULT_SEED_EVENT)
        request = _make_request(
            message="test",
            context_override=snapshot_ctx,
        )

        controller = ToolLoopController(
            request=request,
            profile=profile,  # type: ignore[arg-type]
            safety_policy=ToolLoopSafetyPolicy(),
        )

        controller._last_consumed_message = "test"
        controller.append_tool_cycle(
            assistant_message="Assistant response",
            tool_results=[],
        )

        # 找到 assistant 事件
        assistant_events = [e for e in controller._history if e.role == "assistant"]
        assert len(assistant_events) >= 1
        assert assistant_events[0].event_id.startswith("assistant_")

    def test_append_tool_cycle_preserves_tool_event_metadata(self) -> None:
        """验证 append_tool_cycle 创建的 tool 事件保留 metadata"""
        profile = _MinimalProfile()
        snapshot_ctx = _make_snapshot_with_transcript(_DEFAULT_SEED_EVENT)
        request = _make_request(
            message="test",
            context_override=snapshot_ctx,
        )

        controller = ToolLoopController(
            request=request,
            profile=profile,  # type: ignore[arg-type]
            safety_policy=ToolLoopSafetyPolicy(),
        )

        controller._last_consumed_message = "test"
        controller.append_tool_cycle(
            assistant_message="Reading file",
            tool_results=[
                {
                    "tool": "read_file",
                    "success": True,
                    "result": {"content": "hello world"},
                }
            ],
        )

        # 找到 tool 事件
        tool_events = [e for e in controller._history if e.role == "tool"]
        assert len(tool_events) >= 1
        assert tool_events[0].metadata.get("tool") == "read_file"


class TestAppendToolResultUsesContextEvent:
    """P0-2: append_tool_result 使用 ContextEvent"""

    def test_append_tool_result_creates_context_event(self) -> None:
        """验证 append_tool_result() 创建 ContextEvent"""
        profile = _MinimalProfile()
        snapshot_ctx = _make_snapshot_with_transcript(_DEFAULT_SEED_EVENT)
        request = _make_request(
            message="test",
            context_override=snapshot_ctx,
        )

        controller = ToolLoopController(
            request=request,
            profile=profile,  # type: ignore[arg-type]
            safety_policy=ToolLoopSafetyPolicy(),
        )

        # 记录初始历史长度 (seed event)
        initial_history_len = len(controller._history)

        controller.append_tool_result(
            {
                "tool": "write_file",
                "success": True,
                "result": {"path": "test.py"},
            }
        )

        # 验证历史增加了 tool event
        assert len(controller._history) == initial_history_len + 1
        # 最新事件应该是 tool event
        tool_event = controller._history[-1]
        assert isinstance(tool_event, ContextEvent)
        assert tool_event.role == "tool"
        assert tool_event.metadata.get("tool") == "write_file"
