"""消息组模块

提供可折叠的消息组管理功能。

Example:
    >>> from polaris.delivery.cli.visualization.message_group import CollapsibleMessageGroup
    >>> group = CollapsibleMessageGroup(id="group-1", items=[])
    >>> group.add_item(item)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.delivery.cli.textual.models import MessageItem, MessageType


@dataclass
class CollapsibleMessageGroup:
    """可折叠的消息组

    管理一组消息项，支持批量折叠/展开操作。
    """

    id: str = field(default_factory=lambda: f"group-{uuid.uuid4().hex[:8]}")
    items: list[MessageItem] = field(default_factory=list)
    title: str = ""
    is_collapsed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_item(self, item: MessageItem) -> None:
        """添加消息项"""
        self.items.append(item)

    def remove_item(self, item_id: str) -> bool:
        """移除消息项"""
        for i, item in enumerate(self.items):
            if item.id == item_id:
                self.items.pop(i)
                return True
        return False

    def get_item(self, item_id: str) -> MessageItem | None:
        """获取消息项"""
        for item in self.items:
            if item.id == item_id:
                return item
        return None

    def get_items_by_type(self, msg_type: MessageType) -> list[MessageItem]:
        """按类型获取消息项"""
        return [item for item in self.items if item.type == msg_type]

    def expand_all(self) -> None:
        """展开所有"""
        self.is_collapsed = False
        for item in self.items:
            item.expand()

    def collapse_all(self) -> None:
        """折叠所有"""
        self.is_collapsed = True
        for item in self.items:
            item.collapse()

    def expand_by_type(self, msg_type: MessageType) -> None:
        """按类型展开"""
        for item in self.get_items_by_type(msg_type):
            item.expand()

    def collapse_by_type(self, msg_type: MessageType) -> None:
        """按类型折叠"""
        for item in self.get_items_by_type(msg_type):
            item.collapse()

    def toggle(self) -> None:
        """切换折叠状态"""
        if self.is_collapsed:
            self.expand_all()
        else:
            self.collapse_all()

    def clear(self) -> None:
        """清空所有消息"""
        self.items.clear()

    def count(self) -> int:
        """获取消息数量"""
        return len(self.items)

    def count_by_type(self, msg_type: MessageType) -> int:
        """按类型统计消息数量"""
        return len(self.get_items_by_type(msg_type))

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "id": self.id,
            "title": self.title,
            "is_collapsed": self.is_collapsed,
            "item_count": len(self.items),
            "metadata": self.metadata,
        }
