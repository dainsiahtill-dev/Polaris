"""Engine Base - 推理引擎基类

定义所有推理引擎的通用接口和数据结构。
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, cast

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 引擎策略枚举
# ═══════════════════════════════════════════════════════════════════════════


class EngineStrategy(Enum):
    """推理引擎策略类型"""

    REACT = "react"
    PLAN_SOLVE = "plan_solve"
    TOT = "tot"
    SEQUENTIAL = "sequential"


class EngineStatus(Enum):
    """引擎执行状态"""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


# ═══════════════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class EngineBudget:
    """引擎预算配置"""

    max_steps: int = 12
    max_tool_calls_total: int = 24
    max_no_progress_steps: int = 3
    max_wall_time_seconds: int = 120
    max_same_error_fingerprint: int = 2
    progress_info_incremental: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EngineBudget:
        """从字典创建预算配置

        Args:
            data: 字典数据

        Returns:
            EngineBudget 实例

        Raises:
            TypeError: 如果数据类型不匹配
            ValueError: 如果必需字段缺失或值无效
        """
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict, got {type(data).__name__}")

        # 验证数值字段的类型和范围
        int_fields = {
            "max_steps": (int, 1, None),
            "max_tool_calls_total": (int, 1, None),
            "max_no_progress_steps": (int, 0, None),
            "max_wall_time_seconds": (int, 1, None),
            "max_same_error_fingerprint": (int, 0, None),
        }

        validated = {}
        for field_name, (expected_type, min_val, max_val) in int_fields.items():
            if field_name in data:
                value = data[field_name]
                if not isinstance(value, expected_type):
                    raise TypeError(
                        f"Field '{field_name}' must be {expected_type.__name__}, got {type(value).__name__}"
                    )
                if min_val is not None and value < min_val:
                    raise ValueError(f"Field '{field_name}' must be >= {min_val}")
                if max_val is not None and value > max_val:
                    raise ValueError(f"Field '{field_name}' must be <= {max_val}")
                validated[field_name] = value

        # 处理布尔字段
        if "progress_info_incremental" in data:
            value = data["progress_info_incremental"]
            if not isinstance(value, bool):
                raise TypeError(f"Field 'progress_info_incremental' must be bool, got {type(value).__name__}")
            validated["progress_info_incremental"] = value

        return cls(**cast("dict[str, Any]", validated))

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "max_steps": self.max_steps,
            "max_tool_calls_total": self.max_tool_calls_total,
            "max_no_progress_steps": self.max_no_progress_steps,
            "max_wall_time_seconds": self.max_wall_time_seconds,
            "max_same_error_fingerprint": self.max_same_error_fingerprint,
            "progress_info_incremental": self.progress_info_incremental,
        }


@dataclass
class StepResult:
    """单步执行结果"""

    step_index: int
    status: EngineStatus
    thought: str = ""  # 推理过程
    action: str = ""  # 采取的行动
    action_input: dict[str, Any] = field(default_factory=dict)
    observation: str = ""  # 观察结果
    tool_result: dict[str, Any] | None = None
    error: str | None = None
    progress_detected: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "status": self.status.value,
            "thought": self.thought,
            "action": self.action,
            "action_input": self.action_input,
            "observation": self.observation,
            "tool_result": self.tool_result,
            "error": self.error,
            "progress_detected": self.progress_detected,
            "metadata": self.metadata,
        }


@dataclass
class EngineResult:
    """引擎执行结果"""

    success: bool
    final_answer: str
    strategy: EngineStrategy
    steps: list[StepResult] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    total_steps: int = 0
    total_tool_calls: int = 0
    execution_time_seconds: float = 0.0
    termination_reason: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "final_answer": self.final_answer,
            "strategy": self.strategy.value,
            "steps": [s.to_dict() for s in self.steps],
            "tool_calls": self.tool_calls,
            "total_steps": self.total_steps,
            "total_tool_calls": self.total_tool_calls,
            "execution_time_seconds": self.execution_time_seconds,
            "termination_reason": self.termination_reason,
            "error": self.error,
            "metadata": self.metadata,
        }


@dataclass
class EngineContext:
    """引擎执行上下文"""

    workspace: str
    role: str
    task: str
    profile: Any = None
    tool_gateway: Any = None
    llm_caller: Any = None
    state: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """获取上下文变量"""
        return self.state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """设置上下文变量"""
        self.state[key] = value


# ═══════════════════════════════════════════════════════════════════════════
# 引擎基类
# ═══════════════════════════════════════════════════════════════════════════


class BaseEngine(ABC):
    """推理引擎基类

    所有推理引擎必须继承此类并实现抽象方法。

    属性:
        strategy: 引擎策略类型
        status: 当前执行状态
        budget: 预算配置

    示例:
        >>> class MyEngine(BaseEngine):
        ...     @property
        ...     def strategy(self) -> EngineStrategy:
        ...         return EngineStrategy.REACT
        ...
        ...     async def execute(self, context, budget):
        ...         # 实现具体逻辑
        ...         pass
    """

    def __init__(
        self,
        workspace: str = "",
        budget: EngineBudget | None = None,
    ) -> None:
        """初始化引擎

        Args:
            workspace: 工作区路径
            budget: 预算配置（默认使用 EngineBudget()）
        """
        self.workspace = workspace
        self.budget = budget or EngineBudget()
        self._status = EngineStatus.IDLE
        self._current_step = 0
        self._start_time: float | None = None
        self._steps: list[StepResult] = []
        self._tool_calls: list[dict[str, Any]] = []
        # 进度跟踪
        self._no_progress_count = 0
        self._last_progress_hash: str | None = None
        self._consecutive_error_count = 0

    @property
    @abstractmethod
    def strategy(self) -> EngineStrategy:
        """引擎策略类型（子类必须实现）"""
        pass

    @property
    def status(self) -> EngineStatus:
        """当前执行状态"""
        return self._status

    @property
    def current_step(self) -> int:
        """当前步骤索引"""
        return self._current_step

    @abstractmethod
    async def execute(
        self,
        context: EngineContext,
        initial_message: str = "",
    ) -> EngineResult:
        """执行推理任务（子类必须实现）

        Args:
            context: 引擎执行上下文
            initial_message: 初始消息

        Returns:
            EngineResult: 执行结果
        """
        pass

    @abstractmethod
    async def step(self, context: EngineContext) -> StepResult:
        """执行单步推理（子类必须实现）

        Args:
            context: 引擎执行上下文

        Returns:
            StepResult: 步骤执行结果
        """
        pass

    @abstractmethod
    def can_continue(self) -> bool:
        """检查是否继续执行

        Returns:
            bool: 是否可以继续执行
        """
        pass

    def reset(self) -> None:
        """重置引擎状态"""
        self._status = EngineStatus.IDLE
        self._current_step = 0
        self._start_time = None
        self._steps = []
        self._tool_calls = []
        # 重置进度跟踪
        self._no_progress_count = 0
        self._last_progress_hash = None
        self._consecutive_error_count = 0

    def _check_budget(self) -> bool:
        """检查预算是否耗尽

        Returns:
            bool: 是否可以继续执行
        """
        # 检查步骤数
        if self._current_step >= self.budget.max_steps:
            return False

        # 检查工具调用数
        if len(self._tool_calls) >= self.budget.max_tool_calls_total:
            return False

        # 检查时间预算
        if self._start_time is not None:
            elapsed = time.time() - self._start_time
            if elapsed > self.budget.max_wall_time_seconds:
                return False

        # 检查无进展步数
        if self._no_progress_count >= self.budget.max_no_progress_steps:
            return False

        # 检查连续错误指纹
        return not self._consecutive_error_count >= self.budget.max_same_error_fingerprint

    def _update_progress(self, progress_detected: bool, error_fingerprint: str | None = None) -> None:
        """更新进度跟踪状态

        Args:
            progress_detected: 是否有进展
            error_fingerprint: 错误指纹（用于检测重复错误）
        """
        if progress_detected:
            self._no_progress_count = 0
            self._last_progress_hash = None
        else:
            self._no_progress_count += 1

        if error_fingerprint:
            if error_fingerprint == self._last_progress_hash:
                self._consecutive_error_count += 1
            else:
                self._consecutive_error_count = 1
                self._last_progress_hash = error_fingerprint

    async def _call_llm(
        self,
        context: EngineContext,
        prompt: str,
        max_tokens: int = 2000,
    ) -> str:
        """调用 LLM 获取响应。

        通过 EngineContext.llm_caller 间接调用，llm_caller 由 adapter 层注入，
        adapter 层统一通过 llm.dialogue public service（generate_role_response）调用。
        此方法不直接访问任何 LLM provider，是纯粹的 DI 委托包装。

        测试时可向 EngineContext.llm_caller 注入 mock callable，无需 monkey-patch。
        """
        if context.llm_caller:
            response = await context.llm_caller(
                prompt=prompt,
                role=context.role,
                max_tokens=max_tokens,
            )
            return response
        # llm_caller 未注入（纯单元测试场景）——返回空字符串而非模拟数据，
        # 让调用方的解析逻辑走降级路径，便于测试覆盖真实降级行为。
        return ""

    def _create_result(
        self,
        success: bool,
        final_answer: str,
        termination_reason: str = "",
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EngineResult:
        """创建执行结果"""
        execution_time = 0.0
        if self._start_time:
            execution_time = time.time() - self._start_time

        return EngineResult(
            success=success,
            final_answer=final_answer,
            strategy=self.strategy,
            steps=self._steps.copy(),
            tool_calls=self._tool_calls.copy(),
            total_steps=len(self._steps),
            total_tool_calls=len(self._tool_calls),
            execution_time_seconds=execution_time,
            termination_reason=termination_reason,
            error=error,
            metadata=metadata or {},
        )


# ═══════════════════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════════════════


def create_engine_budget(
    max_steps: int | None = None,
    max_tool_calls_total: int | None = None,
    max_no_progress_steps: int | None = None,
    max_wall_time_seconds: int | None = None,
    **kwargs,
) -> EngineBudget:
    """创建预算配置的便捷函数"""
    config = {
        "max_steps": max_steps,
        "max_tool_calls_total": max_tool_calls_total,
        "max_no_progress_steps": max_no_progress_steps,
        "max_wall_time_seconds": max_wall_time_seconds,
    }
    config = {k: v for k, v in config.items() if v is not None}
    config.update(kwargs)
    return EngineBudget(**cast("dict[str, Any]", config))
