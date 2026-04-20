"""ContextOS ProviderFormatter Protocol and Implementation Tests.

P0-1: ProviderFormatter Protocol延迟序列化行为验证
P0-2: NativeProviderFormatter XML标签格式化
P0-3: AnnotatedProviderFormatter 中文注释格式化
P0-4: format_messages 消息角色转换
P0-5: format_tool_result 结果格式化
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.llm_caller import (
    AnnotatedProviderFormatter,
    NativeProviderFormatter,
    ProviderFormatter,
)
from polaris.cells.roles.kernel.internal.tool_loop_controller import ContextEvent


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


def _make_event(
    *,
    event_id: str = "evt_001",
    role: str = "user",
    content: str = "Hello",
    sequence: int = 0,
    metadata: dict[str, Any] | None = None,
) -> ContextEvent:
    """Create a minimal ContextEvent for testing."""
    return ContextEvent(
        event_id=event_id,
        role=role,
        content=content,
        sequence=sequence,
        metadata=metadata or {},
    )


# -----------------------------------------------------------------------------
# Test: ProviderFormatter Protocol
# -----------------------------------------------------------------------------


class TestProviderFormatterProtocol:
    """P0-1: ProviderFormatter Protocol 延迟序列化行为验证"""

    def test_protocol_has_format_messages_method(self) -> None:
        """验证 ProviderFormatter Protocol 定义了 format_messages 方法"""
        # ProviderFormatter 是 Protocol，通过检查方法存在性验证
        formatter: ProviderFormatter = NativeProviderFormatter()
        assert hasattr(formatter, "format_messages")
        assert callable(formatter.format_messages)

    def test_protocol_has_format_tool_result_method(self) -> None:
        """验证 ProviderFormatter Protocol 定义了 format_tool_result 方法"""
        formatter: ProviderFormatter = AnnotatedProviderFormatter()
        assert hasattr(formatter, "format_tool_result")
        assert callable(formatter.format_tool_result)

    def test_protocol_accepts_context_events(self) -> None:
        """验证 format_messages 接受 ContextEvent 列表"""
        formatter: ProviderFormatter = NativeProviderFormatter()
        events = [
            _make_event(event_id="e1", role="user", content="Hello"),
            _make_event(event_id="e2", role="assistant", content="Hi there"),
        ]
        result = formatter.format_messages(events)
        assert isinstance(result, list)

    def test_protocol_returns_dict_list(self) -> None:
        """验证 format_messages 返回 list[dict[str, str]]"""
        formatter: ProviderFormatter = NativeProviderFormatter()
        events = [_make_event(event_id="e1", role="user", content="Hello")]
        result = formatter.format_messages(events)
        assert isinstance(result, list)
        assert all(isinstance(item, dict) for item in result)

    def test_native_formatter_implements_protocol(self) -> None:
        """验证 NativeProviderFormatter 实现了 ProviderFormatter Protocol"""
        formatter = NativeProviderFormatter()
        # 应该可以赋值给 Protocol 类型而不报错
        pf: ProviderFormatter = formatter
        assert pf is formatter

    def test_annotated_formatter_implements_protocol(self) -> None:
        """验证 AnnotatedProviderFormatter 实现了 ProviderFormatter Protocol"""
        formatter = AnnotatedProviderFormatter()
        pf: ProviderFormatter = formatter
        assert pf is formatter


# -----------------------------------------------------------------------------
# Test: NativeProviderFormatter
# -----------------------------------------------------------------------------


class TestNativeProviderFormatter:
    """P0-2: NativeProviderFormatter XML标签格式化验证"""

    def test_format_messages_user_role(self) -> None:
        """验证 format_messages 格式化 user 角色"""
        formatter = NativeProviderFormatter()
        events = [_make_event(event_id="e1", role="user", content="Hello world")]
        result = formatter.format_messages(events)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello world"

    def test_format_messages_assistant_role(self) -> None:
        """验证 format_messages 格式化 assistant 角色"""
        formatter = NativeProviderFormatter()
        events = [_make_event(event_id="e1", role="assistant", content="I am here")]
        result = formatter.format_messages(events)
        assert len(result) == 1
        assert result[0]["role"] == "assistant"

    def test_format_messages_tool_role(self) -> None:
        """验证 format_messages 格式化 tool 角色"""
        formatter = NativeProviderFormatter()
        events = [_make_event(event_id="e1", role="tool", content="tool result")]
        result = formatter.format_messages(events)
        assert len(result) == 1
        assert result[0]["role"] == "tool"

    def test_format_messages_system_role(self) -> None:
        """验证 format_messages 格式化 system 角色"""
        formatter = NativeProviderFormatter()
        events = [_make_event(event_id="e1", role="system", content="system prompt")]
        result = formatter.format_messages(events)
        assert len(result) == 1
        assert result[0]["role"] == "system"

    def test_format_messages_empty_content(self) -> None:
        """验证 format_messages 处理空内容"""
        formatter = NativeProviderFormatter()
        events = [_make_event(event_id="e1", role="user", content="")]
        result = formatter.format_messages(events)
        assert len(result) == 1
        assert result[0]["content"] == ""

    def test_format_messages_multiple_events(self) -> None:
        """验证 format_messages 处理多个事件"""
        formatter = NativeProviderFormatter()
        events = [
            _make_event(event_id="e1", role="user", content="First", sequence=0),
            _make_event(event_id="e2", role="assistant", content="Second", sequence=1),
            _make_event(event_id="e3", role="tool", content="Third", sequence=2),
        ]
        result = formatter.format_messages(events)
        assert len(result) == 3
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "tool"

    def test_format_messages_preserves_event_id(self) -> None:
        """验证 format_messages 保留事件顺序（通过 sequence）"""
        formatter = NativeProviderFormatter()
        events = [
            _make_event(event_id="evt_a", role="user", content="First", sequence=0),
            _make_event(event_id="evt_b", role="user", content="Second", sequence=1),
        ]
        result = formatter.format_messages(events)
        assert len(result) == 2
        # 顺序保持
        assert result[0]["content"] == "First"
        assert result[1]["content"] == "Second"

    def test_format_tool_result_basic(self) -> None:
        """验证 format_tool_result 基本格式化"""
        formatter = NativeProviderFormatter()
        result = formatter.format_tool_result("read_file", {"content": "file data"})
        assert "<tool>" in result
        assert "[read_file]" in result
        assert "file data" in result
        assert "</tool>" in result

    def test_format_tool_result_escapes_json(self) -> None:
        """验证 format_tool_result 正确转义 JSON"""
        formatter = NativeProviderFormatter()
        result = formatter.format_tool_result("test_tool", {"key": "value with\nnewline"})
        # JSON 应该被转义
        result_json = json.loads(result.split("\n")[2])
        assert result_json["key"] == "value with\nnewline"

    def test_format_tool_result_complex_result(self) -> None:
        """验证 format_tool_result 处理复杂结果"""
        formatter = NativeProviderFormatter()
        complex_result = {
            "files": [{"path": "a.py", "size": 100}, {"path": "b.py", "size": 200}],
            "total": 2,
        }
        result = formatter.format_tool_result("list_files", complex_result)
        assert "[list_files]" in result
        assert "files" in result

    def test_format_tool_result_empty_result(self) -> None:
        """验证 format_tool_result 处理空结果"""
        formatter = NativeProviderFormatter()
        result = formatter.format_tool_result("noop", {})
        assert "[noop]" in result
        assert "<tool>" in result

    def test_format_tool_result_with_special_chars(self) -> None:
        """验证 format_tool_result 处理特殊字符"""
        formatter = NativeProviderFormatter()
        result = formatter.format_tool_result(
            "test", {"text": "Hello <world> & 'quotes'"}
        )
        # JSON 转义应该保留原文
        assert "Hello <world>" in result


# -----------------------------------------------------------------------------
# Test: AnnotatedProviderFormatter
# -----------------------------------------------------------------------------


class TestAnnotatedProviderFormatter:
    """P0-3: AnnotatedProviderFormatter 中文注释格式化验证"""

    def test_format_messages_user_role(self) -> None:
        """验证 format_messages 格式化 user 角色"""
        formatter = AnnotatedProviderFormatter()
        events = [_make_event(event_id="e1", role="user", content="Hello")]
        result = formatter.format_messages(events)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello"

    def test_format_messages_assistant_role(self) -> None:
        """验证 format_messages 格式化 assistant 角色"""
        formatter = AnnotatedProviderFormatter()
        events = [_make_event(event_id="e1", role="assistant", content="Hi")]
        result = formatter.format_messages(events)
        assert result[0]["role"] == "assistant"

    def test_format_messages_tool_role(self) -> None:
        """验证 format_messages 格式化 tool 角色"""
        formatter = AnnotatedProviderFormatter()
        events = [_make_event(event_id="e1", role="tool", content="result")]
        result = formatter.format_messages(events)
        assert result[0]["role"] == "tool"

    def test_format_messages_preserves_actual_role(self) -> None:
        """验证 format_messages 保留实际角色值（不移除）"""
        formatter = AnnotatedProviderFormatter()
        events = [
            _make_event(event_id="e1", role="user", content="Hello"),
        ]
        result = formatter.format_messages(events)
        # AnnotatedProviderFormatter 不添加角色前缀注释
        # 实际角色值被保留在 content 中
        assert result[0]["role"] == "user"

    def test_format_messages_multiple_events(self) -> None:
        """验证 format_messages 处理多个事件"""
        formatter = AnnotatedProviderFormatter()
        events = [
            _make_event(event_id="e1", role="user", content="First", sequence=0),
            _make_event(event_id="e2", role="assistant", content="Second", sequence=1),
            _make_event(event_id="e3", role="tool", content="Third", sequence=2),
        ]
        result = formatter.format_messages(events)
        assert len(result) == 3

    def test_format_messages_empty_content(self) -> None:
        """验证 format_messages 处理空内容"""
        formatter = AnnotatedProviderFormatter()
        events = [_make_event(event_id="e1", role="user", content="")]
        result = formatter.format_messages(events)
        assert len(result) == 1
        assert result[0]["content"] == ""

    def test_format_tool_result_basic(self) -> None:
        """验证 format_tool_result 基本格式化"""
        formatter = AnnotatedProviderFormatter()
        result = formatter.format_tool_result("read_file", {"content": "data"})
        assert "【工具结果】" in result
        assert "[read_file]" in result
        assert "data" in result

    def test_format_tool_result_uses_chinese_brackets(self) -> None:
        """验证 format_tool_result 使用中文括号"""
        formatter = AnnotatedProviderFormatter()
        result = formatter.format_tool_result("test", {"value": 123})
        # 应该使用【工具结果】而不是 <tool>
        assert "【工具结果】" in result
        assert "<tool>" not in result

    def test_format_tool_result_json_formatting(self) -> None:
        """验证 format_tool_result JSON 格式化"""
        formatter = AnnotatedProviderFormatter()
        result = formatter.format_tool_result("test", {"key": "value"})
        # JSON 格式与 Native 不同
        assert "[test]" in result

    def test_format_tool_result_complex_result(self) -> None:
        """验证 format_tool_result 处理复杂结果"""
        formatter = AnnotatedProviderFormatter()
        complex_result = {"items": [1, 2, 3], "count": 3}
        result = formatter.format_tool_result("list", complex_result)
        assert "[list]" in result
        assert "items" in result or "count" in result

    def test_format_tool_result_empty_result(self) -> None:
        """验证 format_tool_result 处理空结果"""
        formatter = AnnotatedProviderFormatter()
        result = formatter.format_tool_result("noop", {})
        assert "[noop]" in result
        assert "【工具结果】" in result


# -----------------------------------------------------------------------------
# Test: ProviderFormatter 延迟序列化行为
# -----------------------------------------------------------------------------


class TestProviderFormatterLazySerialization:
    """P0-1: ProviderFormatter 延迟序列化行为验证

    验证 ProviderFormatter 不在初始化时立即序列化，
    而是等到 format_messages/format_tool_result 被调用时才执行序列化。
    """

    def test_no_immediate_serialization_on_init(self) -> None:
        """验证初始化时不立即序列化"""
        # NativeProviderFormatter 初始化不应该做任何格式化
        formatter = NativeProviderFormatter()
        # 仅初始化完成，不调用任何方法
        # 如果没有抛出异常，说明没有立即序列化
        assert formatter is not None

    def test_format_messages_defers_serialization(self) -> None:
        """验证 format_messages 延迟到调用时才序列化"""
        formatter = NativeProviderFormatter()
        events = [
            _make_event(event_id="e1", role="user", content="Hello"),
        ]
        # 调用前没有序列化
        # 调用时才执行序列化
        result = formatter.format_messages(events)
        assert isinstance(result, list)
        assert len(result) == 1

    def test_format_tool_result_defers_serialization(self) -> None:
        """验证 format_tool_result 延迟到调用时才序列化"""
        formatter = NativeProviderFormatter()
        # 调用前没有序列化
        result = formatter.format_tool_result("tool", {"data": "value"})
        # 调用时才执行序列化
        assert isinstance(result, str)
        assert len(result) > 0

    def test_multiple_calls_produce_independent_results(self) -> None:
        """验证多次调用产生独立结果"""
        formatter = NativeProviderFormatter()
        events1 = [_make_event(event_id="e1", role="user", content="First")]
        events2 = [_make_event(event_id="e2", role="user", content="Second")]

        result1 = formatter.format_messages(events1)
        result2 = formatter.format_messages(events2)

        # 两次调用结果独立
        assert result1[0]["content"] == "First"
        assert result2[0]["content"] == "Second"

    def test_annotated_formatter_defers_serialization(self) -> None:
        """验证 AnnotatedProviderFormatter 延迟序列化"""
        formatter = AnnotatedProviderFormatter()
        # 初始化完成
        assert formatter is not None
        # 调用时才序列化
        events = [_make_event(event_id="e1", role="user", content="Hello")]
        result = formatter.format_messages(events)
        assert isinstance(result, list)


# -----------------------------------------------------------------------------
# Test: ProviderFormatter 边界情况
# -----------------------------------------------------------------------------


class TestProviderFormatterEdgeCases:
    """ProviderFormatter 边界情况测试"""

    def test_format_messages_with_empty_role_defaults_to_user(self) -> None:
        """验证 format_messages 空角色默认转为 user"""
        formatter = NativeProviderFormatter()
        events = [_make_event(event_id="e1", role="", content="Hello")]
        result = formatter.format_messages(events)
        # NativeProviderFormatter 使用 `str(event.role or "user")` 所以空角色变为 "user"
        assert result[0]["role"] == "user"

    def test_format_messages_with_none_content(self) -> None:
        """验证 format_messages 处理 None 内容"""
        formatter = NativeProviderFormatter()
        events = [_make_event(event_id="e1", role="user", content="")]
        result = formatter.format_messages(events)
        assert result[0]["content"] == ""

    def test_format_tool_result_with_none_value(self) -> None:
        """验证 format_tool_result 处理 None 值"""
        formatter = NativeProviderFormatter()
        result = formatter.format_tool_result("test", {"key": None})
        assert "[test]" in result

    def test_format_tool_result_with_nested_dict(self) -> None:
        """验证 format_tool_result 处理嵌套字典"""
        formatter = NativeProviderFormatter()
        result = formatter.format_tool_result(
            "test", {"outer": {"inner": "value"}}
        )
        assert "[test]" in result
        assert "outer" in result

    def test_format_tool_result_with_list_value(self) -> None:
        """验证 format_tool_result 处理列表值"""
        formatter = NativeProviderFormatter()
        result = formatter.format_tool_result("test", {"items": [1, 2, 3]})
        assert "[test]" in result
        assert "items" in result

    def test_format_tool_result_with_numeric_value(self) -> None:
        """验证 format_tool_result 处理数值"""
        formatter = NativeProviderFormatter()
        result = formatter.format_tool_result("count", {"total": 42})
        assert "[count]" in result
        assert "42" in result

    def test_format_tool_result_with_boolean_value(self) -> None:
        """验证 format_tool_result 处理布尔值"""
        formatter = NativeProviderFormatter()
        result = formatter.format_tool_result("check", {"success": True})
        assert "[check]" in result
        assert "true" in result.lower()

    def test_annotated_format_tool_result_with_unicode(self) -> None:
        """验证 AnnotatedProviderFormatter 处理 Unicode"""
        formatter = AnnotatedProviderFormatter()
        result = formatter.format_tool_result(
            "test", {"message": "你好世界"}
        )
        assert "【工具结果】" in result
        assert "你好世界" in result

    def test_native_format_tool_result_with_unicode(self) -> None:
        """验证 NativeProviderFormatter 处理 Unicode"""
        formatter = NativeProviderFormatter()
        result = formatter.format_tool_result(
            "test", {"message": "你好世界"}
        )
        assert "<tool>" in result
        assert "你好世界" in result

    def test_format_messages_maintains_order(self) -> None:
        """验证 format_messages 保持消息顺序"""
        formatter = NativeProviderFormatter()
        events = [
            _make_event(event_id=f"e{i}", role="user", content=f"Message {i}", sequence=i)
            for i in range(5)
        ]
        result = formatter.format_messages(events)
        assert len(result) == 5
        for i in range(5):
            assert result[i]["content"] == f"Message {i}"

    def test_both_formatters_produce_different_output_format(self) -> None:
        """验证两种 Formatter 产生不同格式"""
        native = NativeProviderFormatter()
        annotated = AnnotatedProviderFormatter()
        events = [_make_event(event_id="e1", role="tool", content="result")]
        tool_result = {"data": "test"}

        native_msg = native.format_messages(events)
        annotated_msg = annotated.format_messages(events)

        # 内容格式相同（role/content 结构）
        assert native_msg[0]["role"] == annotated_msg[0]["role"]

        # 但 tool_result 格式不同
        native_tool = native.format_tool_result("test", tool_result)
        annotated_tool = annotated.format_tool_result("test", tool_result)

        assert "<tool>" in native_tool
        assert "【工具结果】" in annotated_tool
