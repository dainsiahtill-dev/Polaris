"""Debug Strategy Models - 调试策略数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from polaris.cells.roles.kernel.internal.debug_strategy.types import (
    DebugPhase,
    DebugStrategy,
    DefenseLayer,
    ErrorCategory,
)


@dataclass(frozen=True)
class ErrorContext:
    """错误上下文。"""

    error_type: str
    error_message: str
    stack_trace: str
    recent_changes: list[str] = field(default_factory=list)  # 最近的代码变更
    environment: dict[str, str] = field(default_factory=dict)  # 环境信息
    previous_attempts: list[str] = field(default_factory=list)  # 之前的修复尝试
    file_path: str | None = None  # 相关文件路径
    line_number: int | None = None  # 行号
    tool_name: str | None = None  # 触发错误的工具名

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ErrorContext:
        """从字典创建ErrorContext。"""
        return cls(
            error_type=data.get("error_type", "unknown"),
            error_message=data.get("error_message", ""),
            stack_trace=data.get("stack_trace", ""),
            recent_changes=data.get("recent_changes", []),
            environment=data.get("environment", {}),
            previous_attempts=data.get("previous_attempts", []),
            file_path=data.get("file_path"),
            line_number=data.get("line_number"),
            tool_name=data.get("tool_name"),
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "error_type": self.error_type,
            "error_message": self.error_message,
            "stack_trace": self.stack_trace,
            "recent_changes": self.recent_changes,
            "environment": self.environment,
            "previous_attempts": self.previous_attempts,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "tool_name": self.tool_name,
        }


@dataclass(frozen=True)
class DefenseCheckpoint:
    """防御层检查点。"""

    layer: DefenseLayer
    description: str
    validation_command: str  # 验证命令
    expected_result: str  # 预期结果
    failure_action: str  # 失败时的动作


@dataclass(frozen=True)
class DebugStep:
    """调试步骤。"""

    phase: DebugPhase
    description: str
    commands: list[str]  # 要执行的命令
    expected_outcome: str  # 预期结果
    rollback_commands: list[str] = field(default_factory=list)  # 回滚命令
    defense_checkpoints: list[DefenseCheckpoint] = field(default_factory=list)  # 防御检查点
    timeout_seconds: int = 60  # 超时时间


@dataclass(frozen=True)
class DebugPlan:
    """调试计划。"""

    plan_id: str
    strategy: DebugStrategy
    steps: list[DebugStep]
    estimated_time: int  # 预计时间（分钟）
    rollback_plan: str  # 整体回滚策略
    success_criteria: list[str] = field(default_factory=list)  # 成功标准
    failure_criteria: list[str] = field(default_factory=list)  # 失败标准


@dataclass(frozen=True)
class Hypothesis:
    """调试假设。"""

    hypothesis_id: str
    description: str
    confidence: float  # 置信度 0-1
    test_approach: str  # 测试方法
    validation_criteria: list[str]  # 验证标准
    related_patterns: list[str] = field(default_factory=list)  # 相关模式


@dataclass(frozen=True)
class Evidence:
    """调试证据。"""

    evidence_id: str
    source: str  # 证据来源
    content: str  # 证据内容
    timestamp: float  # 时间戳
    confidence: float = 1.0  # 置信度
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ErrorClassification:
    """错误分类结果。"""

    category: ErrorCategory
    severity: str  # "low", "medium", "high", "critical"
    root_cause_likely: str  # 可能的根因
    debug_plan: DebugPlan | None = None  # 关联的调试计划
    related_patterns: list[str] = field(default_factory=list)
    suggested_strategies: list[DebugStrategy] = field(default_factory=list)


__all__ = [
    "DebugPlan",
    "DebugStep",
    "DefenseCheckpoint",
    "ErrorClassification",
    "ErrorContext",
    "Evidence",
    "Hypothesis",
]
