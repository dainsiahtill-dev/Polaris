"""Permission Condition Evaluator - Evaluates permission conditions"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


class ConditionType(str, Enum):
    """条件类型枚举"""

    FILE_PATH = "file_path"
    TIME_RANGE = "time_range"
    RESOURCE_LIMIT = "resource_limit"
    CUSTOM = "custom"


@dataclass
class PermissionCondition:
    """权限条件定义

    用于在策略中定义额外的约束条件。
    """

    type: ConditionType
    pattern: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    resource_type: str | None = None
    limit: int | None = None
    custom_evaluator: str | None = None
    config: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为字典表示"""
        result: dict[str, Any] = {"type": self.type.value}
        if self.pattern is not None:
            result["pattern"] = self.pattern
        if self.start_time is not None:
            result["start_time"] = self.start_time.isoformat()
        if self.end_time is not None:
            result["end_time"] = self.end_time.isoformat()
        if self.resource_type is not None:
            result["resource_type"] = self.resource_type
        if self.limit is not None:
            result["limit"] = self.limit
        if self.custom_evaluator is not None:
            result["custom_evaluator"] = self.custom_evaluator
        if self.config is not None:
            result["config"] = self.config
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PermissionCondition:
        """从字典创建条件"""
        condition_type = ConditionType(data.get("type", "custom"))

        # Parse time strings if present
        start_time = None
        end_time = None
        if data.get("start_time"):
            start_time = time.fromisoformat(data["start_time"])
        if data.get("end_time"):
            end_time = time.fromisoformat(data["end_time"])

        return cls(
            type=condition_type,
            pattern=data.get("pattern"),
            start_time=start_time,
            end_time=end_time,
            resource_type=data.get("resource_type"),
            limit=data.get("limit"),
            custom_evaluator=data.get("custom_evaluator"),
            config=data.get("config", {}),
        )


@dataclass
class EvaluationContext:
    """条件评估上下文

    包含评估条件所需的所有上下文信息。
    """

    action: str
    target_path: str | None = None
    user_id: str | None = None
    role: str | None = None
    resource_usage: dict[str, int] | None = None
    timestamp: datetime | None = None
    custom_data: dict[str, Any] | None = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典表示"""
        result: dict[str, Any] = {"action": self.action}
        if self.target_path is not None:
            result["target_path"] = self.target_path
        if self.user_id is not None:
            result["user_id"] = self.user_id
        if self.role is not None:
            result["role"] = self.role
        if self.resource_usage is not None:
            result["resource_usage"] = self.resource_usage
        if self.timestamp is not None:
            result["timestamp"] = self.timestamp.isoformat()
        if self.custom_data:
            result["custom_data"] = self.custom_data
        return result


@dataclass
class ConditionResult:
    """条件评估结果"""

    matched: bool
    reason: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为字典表示"""
        result: dict[str, Any] = {
            "matched": self.matched,
            "reason": self.reason,
        }
        if self.details is not None:
            result["details"] = self.details
        return result


class PermissionConditionEvaluator:
    """权限条件评估器

    负责评估各种权限条件，包括：
    - 文件路径匹配（glob/regex）
    - 时间范围限制
    - 资源使用限制
    - 自定义条件
    """

    def evaluate(
        self,
        condition: PermissionCondition,
        context: EvaluationContext,
    ) -> ConditionResult:
        """评估单个条件

        Args:
            condition: 要评估的条件
            context: 评估上下文

        Returns:
            ConditionResult: 评估结果
        """
        if condition.type == ConditionType.FILE_PATH:
            return self._evaluate_path_condition(condition, context)
        elif condition.type == ConditionType.TIME_RANGE:
            return self._evaluate_time_condition(condition, context)
        elif condition.type == ConditionType.RESOURCE_LIMIT:
            return self._evaluate_resource_condition(condition, context)
        elif condition.type == ConditionType.CUSTOM:
            return self._evaluate_custom_condition(condition, context)

        return ConditionResult(matched=False, reason="Unknown condition type")

    def _evaluate_path_condition(
        self,
        condition: PermissionCondition,
        context: EvaluationContext,
    ) -> ConditionResult:
        """评估文件路径条件

        支持以下模式格式：
        - glob:**/*.py - glob 模式
        - regex:.*\\.py$ - 正则表达式
        - **/*.py - 默认 glob 模式
        """
        if not context.target_path:
            return ConditionResult(
                matched=False,
                reason="No target path in context",
            )

        path = context.target_path
        pattern = condition.pattern or "*"

        try:
            if pattern.startswith("regex:"):
                regex = pattern[6:]
                matched = bool(re.match(regex, path))
            elif pattern.startswith("glob:"):
                glob_pattern = pattern[5:]
                matched = Path(path).match(glob_pattern)
            else:
                # Default: simple glob matching
                matched = Path(path).match(pattern)

            return ConditionResult(
                matched=matched,
                reason=f"Path {'matched' if matched else 'did not match'} {pattern}",
            )
        except re.error as e:
            return ConditionResult(
                matched=False,
                reason=f"Invalid regex pattern: {e}",
            )

    def _evaluate_time_condition(
        self,
        condition: PermissionCondition,
        context: EvaluationContext,
    ) -> ConditionResult:
        """评估时间范围条件

        支持跨天时间范围（如 22:00 - 06:00）。
        """
        timestamp = context.timestamp or datetime.now()
        current_time = timestamp.time()

        start = condition.start_time or time.min
        end = condition.end_time or time.max

        if start <= end:
            matched = start <= current_time <= end
        else:
            # Handle overnight ranges (e.g., 22:00 - 06:00)
            matched = current_time >= start or current_time <= end

        return ConditionResult(
            matched=matched,
            reason=f"Time {current_time} is {'within' if matched else 'outside'} range {start}-{end}",
        )

    def _evaluate_resource_condition(
        self,
        condition: PermissionCondition,
        context: EvaluationContext,
    ) -> ConditionResult:
        """评估资源限制条件"""
        usage = context.resource_usage or {}
        resource_type = condition.resource_type or "default"
        limit = condition.limit or 0

        current_usage = usage.get(resource_type, 0)
        matched = current_usage < limit

        return ConditionResult(
            matched=matched,
            reason=f"Resource {resource_type}: {current_usage}/{limit} ({'under' if matched else 'over'} limit)",
            details={
                "current": current_usage,
                "limit": limit,
                "resource_type": resource_type,
            },
        )

    def _evaluate_custom_condition(
        self,
        condition: PermissionCondition,
        context: EvaluationContext,
    ) -> ConditionResult:
        """评估自定义条件

        自定义条件必须显式注册评估器才能通过。未注册评估器的自定义条件默认
        **拒绝**（fail-closed），以防止遗漏的配置绕过权限控制。

        若需要注册自定义评估器，请在业务层通过
        ``PermissionConditionEvaluator.register_custom_evaluator(name, fn)``
        提供具体实现。
        """
        evaluator_name = condition.custom_evaluator or "default"
        config = condition.config or {}

        registered = getattr(self, "_custom_evaluators", {})
        evaluator_fn = registered.get(evaluator_name)

        if evaluator_fn is None:
            # fail-closed: 未注册的自定义评估器不允许通过
            return ConditionResult(
                matched=False,
                reason=(
                    f"Custom evaluator '{evaluator_name}' is not registered. "
                    "Register it via PermissionConditionEvaluator.register_custom_evaluator() "
                    "before using CUSTOM condition type."
                ),
                details={"evaluator": evaluator_name, "config": config},
            )

        try:
            result = evaluator_fn(condition, context)
            if isinstance(result, ConditionResult):
                return result
            # 允许评估器返回 bool
            matched = bool(result)
            return ConditionResult(
                matched=matched,
                reason=f"Custom evaluator '{evaluator_name}' returned {matched}",
                details={"evaluator": evaluator_name, "config": config},
            )
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as exc:
            return ConditionResult(
                matched=False,
                reason=f"Custom evaluator '{evaluator_name}' raised an error: {exc}",
                details={"evaluator": evaluator_name, "config": config, "error": str(exc)},
            )

    def register_custom_evaluator(
        self,
        name: str,
        evaluator_fn: Callable[[PermissionCondition, EvaluationContext], ConditionResult | bool],
    ) -> None:
        """Register a custom condition evaluator.

        Args:
            name: The evaluator name matching ``PermissionCondition.custom_evaluator``.
            evaluator_fn: Callable(condition, context) -> ConditionResult | bool.
        """
        if not hasattr(self, "_custom_evaluators"):
            object.__setattr__(self, "_custom_evaluators", {})
        self._custom_evaluators[str(name).strip()] = evaluator_fn  # type: ignore[attr-defined]

    def evaluate_all(
        self,
        conditions: list[PermissionCondition],
        context: EvaluationContext,
        match_mode: str = "all",  # "all" or "any"
    ) -> ConditionResult:
        """评估多个条件

        Args:
            conditions: 条件列表
            context: 评估上下文
            match_mode: 匹配模式，"all" 表示所有条件必须满足，"any" 表示至少一个满足

        Returns:
            ConditionResult: 综合评估结果
        """
        if not conditions:
            return ConditionResult(matched=True, reason="No conditions to evaluate")

        results = [self.evaluate(c, context) for c in conditions]

        if match_mode == "all":
            matched = all(r.matched for r in results)
            reason = "All conditions matched" if matched else "Some conditions failed"
        else:  # any
            matched = any(r.matched for r in results)
            reason = "At least one condition matched" if matched else "No conditions matched"

        return ConditionResult(
            matched=matched,
            reason=reason,
            details={"results": [{"matched": r.matched, "reason": r.reason} for r in results]},
        )
