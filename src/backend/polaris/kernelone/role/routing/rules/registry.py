"""Rule Registry - 全局规则注册表."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from polaris.kernelone.role.routing.rules.loader import RoutingRule

logger = logging.getLogger(__name__)


# 规则变更监听器类型
RuleChangeListener = Callable[[list[RoutingRule]], None]


@dataclass
class RuleChangeEvent:
    """规则变更事件"""

    action: str  # "added" | "removed" | "updated"
    rule_id: str
    timestamp: float


class RuleRegistry:
    """全局规则注册表

    管理规则的注册、注销和变更通知。
    """

    def __init__(self) -> None:
        self._rules: dict[str, RoutingRule] = {}
        self._listeners: list[RuleChangeListener] = []

    def register_rule(self, rule: RoutingRule) -> None:
        """注册新规则"""
        if rule.id in self._rules:
            logger.warning(f"Rule already registered, updating: {rule.id}")

        self._rules[rule.id] = rule
        self._notify_listeners("added", rule.id)
        logger.info(f"Registered rule: {rule.id}")

    def unregister_rule(self, rule_id: str) -> bool:
        """注销规则"""
        if rule_id not in self._rules:
            logger.warning(f"Rule not found for unregister: {rule_id}")
            return False

        del self._rules[rule_id]
        self._notify_listeners("removed", rule_id)
        logger.info(f"Unregistered rule: {rule_id}")
        return True

    def update_rule(self, rule: RoutingRule) -> bool:
        """更新现有规则"""
        if rule.id not in self._rules:
            logger.warning(f"Rule not found for update: {rule.id}")
            return False

        self._rules[rule.id] = rule
        self._notify_listeners("updated", rule.id)
        logger.info(f"Updated rule: {rule.id}")
        return True

    def get_rule(self, rule_id: str) -> RoutingRule | None:
        """获取规则"""
        return self._rules.get(rule_id)

    def get_rules(self) -> list[RoutingRule]:
        """获取所有规则"""
        return list(self._rules.values())

    def get_enabled_rules(self) -> list[RoutingRule]:
        """获取所有启用的规则"""
        return [r for r in self._rules.values() if r.enabled]

    def add_listener(self, listener: RuleChangeListener) -> None:
        """添加变更监听器"""
        self._listeners.append(listener)

    def remove_listener(self, listener: RuleChangeListener) -> None:
        """移除变更监听器"""
        self._listeners.remove(listener)

    def _notify_listeners(self, action: str, rule_id: str) -> None:
        """通知所有监听器"""
        for listener in self._listeners:
            try:
                listener(self.get_rules())
            except Exception as e:
                # 监听器可能抛出任意异常,为保证其他监听器收到通知,此处捕获所有异常
                logger.exception("Listener notification failed: %s", e)
