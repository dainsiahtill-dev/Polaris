"""MessageItem 测试套件

测试消息级折叠核心功能。
测试文件位置: src/backend/tests/delivery/cli/visualization/
源代码位置: src/backend/polaris/delivery/cli/visualization/
"""

from __future__ import annotations

import pytest

from polaris.delivery.cli.visualization import (
    CollapsibleMessageGroup,
    MessageItem,
    MessageType,
)


class TestMessageItemCollapse:
    """测试 MessageItem 折叠逻辑"""

    def test_default_not_collapsed(self):
        """默认不折叠"""
        msg = MessageItem(
            id="test-debug",
            type=MessageType.DEBUG,
            title="Test",
            content="...",
        )
        assert msg.is_collapsed is False

    def test_explicit_override(self):
        """显式设置覆盖默认值"""
        msg = MessageItem(
            id="debug-1",
            type=MessageType.DEBUG,
            title="永久展开的 DEBUG",
            content="...",
            is_collapsed=True,
        )
        assert msg.is_collapsed is True

    def test_toggle(self):
        """切换折叠状态"""
        msg = MessageItem(
            id="debug-1",
            type=MessageType.DEBUG,
            title="Kernel 操作",
            content="完整调用栈...",
        )
        assert msg.is_collapsed is False
        msg.toggle()
        assert msg.is_collapsed is True
        msg.toggle()
        assert msg.is_collapsed is False

    def test_author_label(self):
        """作者标签映射"""
        msg = MessageItem(
            id="u1",
            type=MessageType.USER,
            title="User",
            content="hello",
        )
        assert msg.author_label == "You"

        msg2 = MessageItem(
            id="t1",
            type=MessageType.TOOL_CALL,
            title="Tool",
            content="call",
        )
        assert msg2.author_label == "Tool"

    def test_summary(self):
        """摘要内容"""
        msg = MessageItem(
            id="u1",
            type=MessageType.USER,
            title="User",
            content="hello world",
        )
        assert msg.summary == "You: hello world"

        msg2 = MessageItem(
            id="t1",
            type=MessageType.TOOL_CALL,
            title="Tool",
            content="call",
            metadata={"tool_name": "read_file"},
        )
        assert msg2.summary == "Tool: read_file"


class TestCollapsibleMessageGroup:
    """测试 CollapsibleMessageGroup"""

    def test_collapse_by_type_selective(self):
        """验证按类型折叠只影响目标类型"""
        group = CollapsibleMessageGroup(
            id="test",
            items=[
                MessageItem(id="d1", type=MessageType.DEBUG, title="D1", content="..."),
                MessageItem(id="d2", type=MessageType.DEBUG, title="D2", content="..."),
                MessageItem(id="u1", type=MessageType.USER, title="U1", content="..."),
            ]
        )

        group.collapse_by_type(MessageType.DEBUG)

        assert group.items[0].is_collapsed is True
        assert group.items[1].is_collapsed is True
        assert group.items[2].is_collapsed is False  # USER 不受影响

    def test_expand_by_type(self):
        """测试按类型展开"""
        group = CollapsibleMessageGroup(
            id="test",
            items=[
                MessageItem(id="d1", type=MessageType.DEBUG, title="D1", content="...", is_collapsed=True),
                MessageItem(id="u1", type=MessageType.USER, title="U1", content="...", is_collapsed=True),
            ]
        )

        group.expand_by_type(MessageType.DEBUG)

        assert group.items[0].is_collapsed is False
        assert group.items[1].is_collapsed is True  # USER 不受影响

    def test_get_items_by_type(self):
        """测试按类型获取消息"""
        group = CollapsibleMessageGroup(
            id="test",
            items=[
                MessageItem(id="d1", type=MessageType.DEBUG, title="D1", content="..."),
                MessageItem(id="u1", type=MessageType.USER, title="U1", content="..."),
                MessageItem(id="d2", type=MessageType.DEBUG, title="D2", content="..."),
            ]
        )

        debug_items = group.get_items_by_type(MessageType.DEBUG)
        assert len(debug_items) == 2
        assert all(item.type == MessageType.DEBUG for item in debug_items)

    def test_add_item_and_count(self):
        """添加消息项并计数"""
        group = CollapsibleMessageGroup(id="test")
        assert group.count() == 0

        group.add_item(MessageItem(id="d1", type=MessageType.DEBUG, title="D1", content="..."))
        assert group.count() == 1

    def test_remove_item(self):
        """移除消息项"""
        group = CollapsibleMessageGroup(
            id="test",
            items=[
                MessageItem(id="d1", type=MessageType.DEBUG, title="D1", content="..."),
            ]
        )
        assert group.remove_item("d1") is True
        assert group.count() == 0
        assert group.remove_item("d1") is False

    def test_collapse_all_and_expand_all(self):
        """全部折叠和展开"""
        group = CollapsibleMessageGroup(
            id="test",
            items=[
                MessageItem(id="d1", type=MessageType.DEBUG, title="D1", content="..."),
                MessageItem(id="u1", type=MessageType.USER, title="U1", content="..."),
            ]
        )

        group.collapse_all()
        assert all(item.is_collapsed for item in group.items)

        group.expand_all()
        assert all(not item.is_collapsed for item in group.items)

    def test_to_dict(self):
        """转换为字典"""
        group = CollapsibleMessageGroup(
            id="test",
            title="Test Group",
            items=[
                MessageItem(id="d1", type=MessageType.DEBUG, title="D1", content="..."),
            ]
        )
        d = group.to_dict()
        assert d["id"] == "test"
        assert d["title"] == "Test Group"
        assert d["item_count"] == 1
