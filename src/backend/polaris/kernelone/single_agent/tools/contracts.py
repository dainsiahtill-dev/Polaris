"""Tool execution contracts for KernelOne agent runtime.

Blueprint: §9 ToolRuntime / §9.1 ExecutionLane

Core types:
- ExecutionLane: 执行通道枚举（DIRECT | PROGRAMMATIC）
- ToolStatus: 工具执行状态枚举
- ToolExecutionResult: 单个工具调用的执行结果

设计约束:
- 所有工具执行结果通过 ToolExecutionResult 回流
- 工具执行失败不得直接把 turn 打崩
- 错误都转为 ToolResult(status=error|blocked|timeout)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# Re-export ToolSpec from tools layer for backwards compatibility
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpec


class ExecutionLane(Enum):
    """执行通道枚举

    Blueprint: §9.2 ExecutionLaneSelector

    DIRECT: 直接执行通道
        - 工具数量 <= 3
        - 预估结果体积 < 10KB
        - 不需要批处理/聚合/循环
        - 适用场景: 简单查询、文件读取、单个API调用

    PROGRAMMATIC: 程序化执行通道
        - 高 fan-out（工具数量 > 3）
        - 大量中间结果需要筛选/聚合
        - 需要条件分支/循环
        - 适用场景: 代码重构、多文件分析、测试生成
    """

    DIRECT = "direct"
    PROGRAMMATIC = "programmatic"


class ToolStatus(Enum):
    """工具执行状态枚举

    Blueprint: §9 ToolExecutionResult

    SUCCESS: 执行成功
    ERROR: 执行失败（异常/返回错误）
    BLOCKED: 被权限/策略阻止
    TIMEOUT: 执行超时
    CANCELLED: 被调用方取消
    """

    SUCCESS = "success"
    ERROR = "error"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """单个工具调用的执行结果

    Blueprint: §9 ToolExecutionResult

    设计约束:
    - 所有工具执行结果必须通过 ToolExecutionResult 回流
    - 工具执行失败不得直接把 turn 打崩
    - 错误都转为 ToolResult(status=error|blocked|timeout)

    Attributes:
        tool_name: 工具名称
        status: 执行状态
        result: 执行结果（成功时）
        error: 错误信息（失败时）
        metadata: 额外元数据（执行时间、token消耗等）
        timestamp: 执行时间戳（UTC）
    """

    tool_name: str
    status: ToolStatus
    result: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def ok(self) -> bool:
        """True when status == SUCCESS"""
        return self.status == ToolStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool_name,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
            "ok": self.ok,
        }

    @classmethod
    def from_gateway_result(
        cls,
        gateway_result: dict[str, Any],
        tool_name: str | None = None,
    ) -> ToolExecutionResult:
        """从 RoleToolGateway.execute_tool() 结果构造 ToolExecutionResult

        Args:
            gateway_result: RoleToolGateway.execute_tool() 返回的字典
            tool_name: 可选，工具名（优先取 gateway_result 内的值）

        Returns:
            ToolExecutionResult 实例
        """
        resolved_name = tool_name or gateway_result.get("tool", "unknown")
        success = gateway_result.get("success", False)

        if success:
            status = ToolStatus.SUCCESS
            result = gateway_result.get("result")
            error = None
        else:
            auth_result = gateway_result.get("authorized")
            status = ToolStatus.BLOCKED if auth_result is False else ToolStatus.ERROR
            result = None
            error = gateway_result.get("error") or "unknown_error"

        return cls(
            tool_name=resolved_name,
            status=status,
            result=result,
            error=error,
            metadata={"raw_result": gateway_result.get("raw_result")},
        )


@dataclass(frozen=True, slots=True)
class AgentToolSpec:
    """Agent工具元信息规格 - 用于 registry 中的工具元信息

    存储工具的元数据，不直接执行工具。
    由 ToolMaterializer 按需实例化为可执行工具。

    注意: 这是 Agent 层的工具元信息，与 kernelone.tool_execution.tool_spec_registry.ToolSpec
    是不同层级的抽象（LLM调用规格 vs 工具注册管理）。

    Attributes:
        tool_id: 工具唯一标识符
        name: 工具名称（用于调用）
        source: 工具来源（builtin|local|mcp|agent）
        description: 工具描述
        parameters: JSON Schema 格式的参数定义
        enabled: 是否启用
        tags: 工具标签（用于分类和搜索）
    """

    tool_id: str
    name: str
    source: str
    description: str
    parameters: dict[str, Any]
    enabled: bool = True
    tags: tuple[str, ...] = ()

    @property
    def schema_dict(self) -> dict[str, Any]:
        """以字典形式返回 schema（用于 tool_calls 传给 LLM）"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


__all__ = [
    "AgentToolSpec",  # 重命名: ToolSpec -> AgentToolSpec
    "ExecutionLane",
    "ToolExecutionResult",
    "ToolSpec",  # Re-export from tools layer for backwards compatibility
    "ToolStatus",
]
