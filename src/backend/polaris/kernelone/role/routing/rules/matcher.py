"""Rule Matcher - 规则匹配器."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from polaris.kernelone.role.routing.rules.loader import RoutingRule, RoutingRuleLoader

logger = logging.getLogger(__name__)


@dataclass
class MatchedRule:
    """匹配结果"""

    rule: RoutingRule
    match_type: str  # "exact" | "prefix" | "regex" | "default"
    score: float  # 匹配度评分


class RuleMatcher:
    """规则匹配器

    支持多维度匹配: task_type, domain, intent, session_phase, user_preference
    匹配优先级: 精确匹配 > 前缀匹配 > 正则匹配 > 默认
    """

    def __init__(self, rule_loader: RoutingRuleLoader) -> None:
        self._rule_loader = rule_loader

    def match(
        self,
        task_type: str,
        domain: str,
        intent: str,
        session_phase: str | None = None,
        user_preference: dict[str, Any] | None = None,
    ) -> list[MatchedRule]:
        """匹配所有适用的规则

        Returns:
            按优先级排序的匹配结果列表
        """
        rules = self._rule_loader.load_rules()
        matched: list[MatchedRule] = []

        for rule in rules:
            if not rule.enabled:
                continue

            # 尝试各维度匹配
            match_type, score = self._try_match(rule, task_type, domain, intent, session_phase, user_preference)

            if match_type or rule.match.get("default"):
                matched.append(
                    MatchedRule(
                        rule=rule,
                        match_type=match_type or "default",
                        score=score,
                    )
                )

        # 按 score 排序
        matched.sort(key=lambda m: (m.score, m.rule.priority), reverse=True)
        return matched[:10]  # Top 10

    def _try_match(
        self,
        rule: RoutingRule,
        task_type: str,
        domain: str,
        intent: str,
        session_phase: str | None,
        user_preference: dict[str, Any] | None,
    ) -> tuple[str | None, float]:
        """尝试匹配单个规则"""
        match_spec = rule.match

        # 精确匹配
        exact_score = self._exact_match(match_spec, task_type, domain, intent, session_phase, user_preference)
        if exact_score > 0:
            return "exact", exact_score

        # 前缀匹配
        prefix_score = self._prefix_match(match_spec, task_type, domain, intent)
        if prefix_score > 0:
            return "prefix", prefix_score

        # 正则匹配
        regex_score = self._regex_match(match_spec, task_type, domain, intent)
        if regex_score > 0:
            return "regex", regex_score

        return None, 0.0

    def _exact_match(
        self,
        match_spec: dict[str, Any],
        task_type: str,
        domain: str,
        intent: str,
        session_phase: str | None,
        user_preference: dict[str, Any] | None,
    ) -> float:
        """Exact matching."""
        score = 0.0
        total = 0.0

        if "task_type" in match_spec:
            total += 1
            if match_spec["task_type"] == task_type:
                score += 1

        if "domain" in match_spec:
            total += 1
            if match_spec["domain"] == domain:
                score += 1

        if "intent" in match_spec:
            total += 1
            if match_spec["intent"] == intent:
                score += 1

        if "session_phase" in match_spec and session_phase:
            total += 1
            if match_spec["session_phase"] == session_phase:
                score += 1

        if "user_preference" in match_spec and user_preference:
            pref_spec = match_spec["user_preference"]
            for key, value in pref_spec.items():
                if user_preference.get(key) == value:
                    score += 0.5
                    total += 0.5

        return score / total if total > 0 else 0.0

    def _prefix_match(
        self,
        match_spec: dict[str, Any],
        task_type: str,
        domain: str,
        intent: str,
    ) -> float:
        """前缀匹配"""
        score = 0.0

        for key in ["task_type", "domain", "intent"]:
            if key in match_spec:
                prefix = match_spec[key]
                value = locals()[key]
                if value and value.startswith(prefix):
                    score += 0.8

        return score / 3.0 if score > 0 else 0.0

    def _regex_match(
        self,
        match_spec: dict[str, Any],
        task_type: str,
        domain: str,
        intent: str,
    ) -> float:
        """正则匹配"""
        score = 0.0

        for key in ["task_type", "domain", "intent"]:
            if key in match_spec and key.endswith("_regex"):
                pattern = match_spec[key]
                value = locals()[key[:-6]]  # 去掉 _regex 后缀
                try:
                    if re.match(pattern, value):
                        score += 0.6
                except re.error:
                    pass

        return score / 3.0 if score > 0 else 0.0
