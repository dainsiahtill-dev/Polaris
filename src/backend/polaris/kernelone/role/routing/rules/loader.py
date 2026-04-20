"""Routing Rule Loader - YAML 规则加载器."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent / "config"


@dataclass
class RoutingRule:
    """单条路由规则"""

    id: str
    name: str
    priority: int = 0
    match: dict[str, Any] = field(default_factory=dict)
    recommendation: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


class RoutingRuleLoader:
    """路由规则加载器

    从 YAML 文件加载路由规则,支持动态重载。
    """

    def __init__(self, config_dir: Path | None = None) -> None:
        self._config_dir = config_dir or _CONFIG_DIR
        self._rules: list[RoutingRule] = []
        self._last_modified: float = 0.0

    def load_rules(self, force_reload: bool = False) -> list[RoutingRule]:
        """加载所有路由规则"""
        rules_file = self._config_dir / "default_rules.yaml"

        if not force_reload and self._rules:
            return self._rules

        try:
            with open(rules_file, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            rules_data = data.get("rules", [])
            self._rules = [self._parse_rule(r) for r in rules_data]
            self._rules.sort(key=lambda r: r.priority, reverse=True)

            logger.info(f"Loaded {len(self._rules)} routing rules")
            return self._rules

        except FileNotFoundError:
            logger.warning(f"Rules file not found: {rules_file}")
            return []
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse rules YAML: {e}")
            return []

    def _parse_rule(self, data: dict[str, Any]) -> RoutingRule:
        """解析单条规则"""
        return RoutingRule(
            id=data["id"],
            name=data.get("name", data["id"]),
            priority=data.get("priority", 0),
            match=data.get("match", {}),
            recommendation=data.get("recommendation", {}),
            enabled=data.get("enabled", True),
        )

    def get_rule(self, rule_id: str) -> RoutingRule | None:
        """根据 ID 获取规则"""
        for rule in self._rules:
            if rule.id == rule_id:
                return rule
        return None
