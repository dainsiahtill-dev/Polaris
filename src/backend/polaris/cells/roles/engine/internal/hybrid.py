"""Hybrid Engine - 混合引擎

实现推理引擎的自动选择和混合编排：
- 根据任务特征自动选择合适的引擎策略
- 支持策略间的动态切换
- 提供统一的执行接口
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .base import (
    BaseEngine,
    EngineBudget,
    EngineContext,
    EngineResult,
    EngineStrategy,
)
from .classifier import TaskClassifier, classify_task
from .plan_solve import PlanSolveEngine
from .react import ReActEngine
from .registry import EngineRegistry
from .tot import ToTEngine

logger = logging.getLogger(__name__)


class HybridEngine:
    """混合推理引擎

    根据任务特征自动选择合适的推理策略，并支持策略间的动态切换。

    特点：
    - 自动任务分类
    - 策略动态切换
    - 结果聚合与对比

    使用示例:
        >>> engine = HybridEngine(workspace=".")
        >>> result = await engine.run("探索代码库结构", context)
        >>> print(result.strategy)  # EngineStrategy.REACT
    """

    def __init__(
        self,
        workspace: str = "",
        budget: EngineBudget | None = None,
        auto_select: bool = True,
        enable_switching: bool = True,
    ) -> None:
        """初始化混合引擎

        Args:
            workspace: 工作区路径
            budget: 默认预算配置
            auto_select: 是否自动选择策略
            enable_switching: 是否允许运行时切换策略
        """
        self.workspace = workspace
        self.budget = budget or EngineBudget()
        self.auto_select = auto_select
        self.enable_switching = enable_switching

        # 任务分类器
        self._classifier = TaskClassifier()

        # 引擎注册表
        self._registry = EngineRegistry()
        self._register_default_engines()

        # 当前引擎
        self._current_strategy: EngineStrategy | None = None
        self._current_engine: BaseEngine | None = None

        # 执行历史（添加上限控制防止内存泄漏）
        self._execution_history: list[dict[str, Any]] = []
        self._max_history_length: int = 100  # 最多保留100条历史记录

    def _register_default_engines(self) -> None:
        """注册默认引擎"""
        self._registry.register(ReActEngine, workspace=self.workspace, budget=self.budget)
        self._registry.register(PlanSolveEngine, workspace=self.workspace, budget=self.budget)
        self._registry.register(ToTEngine, workspace=self.workspace, budget=self.budget)

        # 集成现有的 SequentialEngine
        try:
            from .sequential_adapter import SequentialEngineAdapter

            self._registry.register(SequentialEngineAdapter, workspace=self.workspace, budget=self.budget)
        except ImportError:
            logger.warning("SequentialEngineAdapter not available")

    async def run(
        self,
        task: str,
        context: EngineContext | None = None,
        strategy: EngineStrategy | None = None,
    ) -> EngineResult:
        """运行混合引擎

        Args:
            task: 任务描述
            context: 执行上下文
            strategy: 指定策略（可选，优先使用）

        Returns:
            EngineResult: 执行结果
        """
        # 创建上下文
        if context is None:
            context = EngineContext(
                workspace=self.workspace,
                role="director",
                task=task,
            )

        # 选择策略
        if strategy:
            selected_strategy = strategy
        elif self.auto_select:
            selected_strategy = self._select_strategy(task, context)
        else:
            selected_strategy = EngineStrategy.SEQUENTIAL

        logger.info(f"HybridEngine: selected strategy = {selected_strategy.value}")

        # 获取引擎
        engine = self._get_engine(selected_strategy)
        if not engine:
            # 回退到 Sequential
            selected_strategy = EngineStrategy.SEQUENTIAL
            engine = self._get_engine(selected_strategy)

        if not engine:
            raise RuntimeError("No engine available")

        # 执行
        self._current_strategy = selected_strategy
        self._current_engine = engine

        start_time = time.time()
        result = await engine.execute(context)

        # 修复：安全处理执行结果
        if result is None:
            logger.warning("Engine returned None result")
            result = EngineResult(
                success=False,
                final_answer="Engine execution returned None",
                strategy=selected_strategy,
            )

        # 记录执行历史
        try:
            history_entry = {
                "task": task,
                "strategy": selected_strategy.value,
                "result": result.to_dict() if hasattr(result, "to_dict") else {"error": "result has no to_dict"},
                "execution_time": time.time() - start_time,
            }
            self._execution_history.append(history_entry)

            # 修复：防止历史记录无限增长，超过上限时移除最旧的记录
            max_history = getattr(self, "_max_history_length", 100)
            while len(self._execution_history) > max_history:
                self._execution_history.pop(0)
        except (RuntimeError, ValueError) as e:
            logger.warning(f"Failed to record execution history: {e}")

        return result

    async def run_with_switching(
        self,
        task: str,
        context: EngineContext | None = None,
    ) -> EngineResult:
        """运行混合引擎，支持动态策略切换

        Args:
            task: 任务描述
            context: 执行上下文

        Returns:
            EngineResult: 执行结果
        """
        if not self.enable_switching:
            return await self.run(task, context)

        # 初始选择
        if context is None:
            context = EngineContext(
                workspace=self.workspace,
                role="director",
                task=task,
            )

        selected_strategy = self._select_strategy(task, context)
        engine = self._get_engine(selected_strategy)

        if not engine:
            return EngineResult(
                success=False,
                final_answer="No engine available",
                strategy=EngineStrategy.SEQUENTIAL,
            )

        # 初始执行
        result = await engine.execute(context)

        # 检查是否需要切换
        if self._should_switch(result):
            logger.info("Switching strategy due to poor performance")
            switch_result = await self._switch_and_execute(task, context, result)
            if switch_result:
                return switch_result

        return result

    def _select_strategy(
        self,
        task: str,
        context: EngineContext | None,
    ) -> EngineStrategy:
        """选择策略

        Args:
            task: 任务描述
            context: 执行上下文

        Returns:
            EngineStrategy: 选中的策略
        """
        # 使用分类器
        context_dict = {}
        if context:
            context_dict = {
                "role": context.role,
                # 修复：正确访问 state 属性
                "complexity": context.state.get("complexity", "medium"),
            }

        return classify_task(task, context_dict)

    def _get_engine(self, strategy: EngineStrategy) -> BaseEngine | None:
        """获取引擎实例

        Args:
            strategy: 引擎策略

        Returns:
            BaseEngine: 引擎实例
        """
        return self._registry.get(strategy)

    def _should_switch(self, result: EngineResult) -> bool:
        """判断是否需要切换策略

        Args:
            result: 当前执行结果

        Returns:
            bool: 是否需要切换
        """
        # 修复：安全检查 result
        if not result:
            return True

        if not result.success:
            return True

        # 检查执行效率
        if result.total_steps > self.budget.max_steps * 0.8:
            return True

        # 检查工具调用效率
        return result.total_tool_calls > self.budget.max_tool_calls_total * 0.8

    async def _switch_and_execute(
        self,
        task: str,
        context: EngineContext,
        previous_result: EngineResult,
    ) -> EngineResult | None:
        """切换策略并重新执行

        Args:
            task: 任务描述
            context: 执行上下文
            previous_result: 之前的执行结果

        Returns:
            EngineResult: 新的执行结果
        """
        # 根据之前结果选择更好的策略
        alternative_strategies = self._get_alternative_strategies(previous_result.strategy)

        for strategy in alternative_strategies:
            logger.info(f"Attempting alternative strategy: {strategy.value}")

            engine = self._get_engine(strategy)
            if not engine:
                continue

            # 重置引擎状态
            engine.reset()

            # 重新执行
            new_result = await engine.execute(context)

            if new_result.success:
                new_result.metadata["switched_from"] = previous_result.strategy.value
                return new_result

        return None

    def _get_alternative_strategies(
        self,
        current: EngineStrategy,
    ) -> list[EngineStrategy]:
        """获取备选策略列表

        Args:
            current: 当前策略

        Returns:
            List[EngineStrategy]: 备选策略列表
        """
        strategy_order = {
            EngineStrategy.REACT: [EngineStrategy.PLAN_SOLVE, EngineStrategy.SEQUENTIAL],
            EngineStrategy.PLAN_SOLVE: [EngineStrategy.REACT, EngineStrategy.SEQUENTIAL],
            EngineStrategy.TOT: [EngineStrategy.PLAN_SOLVE, EngineStrategy.REACT],
            EngineStrategy.SEQUENTIAL: [EngineStrategy.PLAN_SOLVE, EngineStrategy.REACT],
        }

        return strategy_order.get(current, [])

    def get_execution_history(self) -> list[dict[str, Any]]:
        """获取执行历史

        Returns:
            执行历史列表
        """
        return self._execution_history

    def get_current_strategy(self) -> EngineStrategy | None:
        """获取当前策略

        Returns:
            当前策略
        """
        return self._current_strategy

    def register_engine(self, engine_class: type) -> None:
        """注册自定义引擎

        Args:
            engine_class: 引擎类
        """
        self._registry.register(engine_class, workspace=self.workspace, budget=self.budget)

    def set_strategy(self, strategy: EngineStrategy) -> None:
        """设置策略（禁用自动选择）

        Args:
            strategy: 引擎策略
        """
        self._current_strategy = strategy
        self._current_engine = self._get_engine(strategy)


# 全局混合引擎实例
_hybrid_engine: HybridEngine | None = None


def get_hybrid_engine(
    workspace: str = "",
    budget: EngineBudget | None = None,
) -> HybridEngine:
    """获取全局混合引擎实例

    Args:
        workspace: 工作区路径
        budget: 预算配置

    Returns:
        HybridEngine: 混合引擎实例
    """
    global _hybrid_engine
    if _hybrid_engine is None:
        _hybrid_engine = HybridEngine(workspace=workspace, budget=budget)
    return _hybrid_engine
