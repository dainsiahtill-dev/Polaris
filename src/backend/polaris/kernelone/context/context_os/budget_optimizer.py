"""Budget Optimizer - OR-Tools 背包优化

ADR-0067: ContextOS 2.0 摘要策略选型 - Phase 4

基于 Google OR-Tools 的预算感知事件选择优化。

使用背包问题求解器在 token 预算约束下最大化信息价值。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EventItem:
    """事件项用于背包优化"""

    event_id: str
    sequence: int
    route: str
    token_cost: int
    importance_score: float  # 0.0 - 1.0
    is_root: bool  # 是否必须保留
    content_preview: str = ""


@dataclass(frozen=True)
class BudgetAllocation:
    """预算分配结果"""

    selected_events: list[str]  # 选中的事件 ID 列表
    total_tokens: int
    total_value: float
    optimization_time_ms: float
    algorithm: str  # "knapsack" | "greedy" | "fallback"


class ORToolsBudgetOptimizer:
    """基于 OR-Tools 的预算优化器

    使用背包求解器在 token 预算约束下最大化信息价值。

    Example:
        ```python
        optimizer = ORToolsBudgetOptimizer()

        events = [
            EventItem("e1", 1, "patch", 100, 0.9, True),
            EventItem("e2", 2, "archive", 200, 0.3, False),
            EventItem("e3", 3, "clear", 150, 0.7, False),
        ]

        result = optimizer.optimize(events, token_budget=300)
        print(f"Selected: {result.selected_events}")
        ```
    """

    def __init__(
        self,
        enable_ortools: bool = True,
        fallback_to_greedy: bool = True,
    ) -> None:
        """初始化预算优化器

        Args:
            enable_ortools: 是否启用 OR-Tools (False 则使用贪心)
            fallback_to_greedy: OR-Tools 不可用时是否回退到贪心
        """
        self._enable_ortools = enable_ortools
        self._fallback_to_greedy = fallback_to_greedy
        self._solver: Any | None = None

    def _ensure_ortools(self) -> bool:
        """确保 OR-Tools 可用

        Returns:
            True 如果 OR-Tools 可用
        """
        if not self._enable_ortools:
            return False

        if self._solver is not None:
            return True

        try:
            from ortools.algorithms import pywrapknapsack_solver

            self._solver = pywrapknapsack_solver
            return True
        except ImportError:
            logger.debug("OR-Tools not available, using greedy fallback")
            return False

    def optimize(
        self,
        events: list[EventItem],
        token_budget: int,
    ) -> BudgetAllocation:
        """优化事件选择

        在 token 预算约束下选择价值最大化的事件组合。

        Args:
            events: 事件项列表
            token_budget: token 预算上限

        Returns:
            预算分配结果
        """
        if not events:
            return BudgetAllocation(
                selected_events=[],
                total_tokens=0,
                total_value=0.0,
                optimization_time_ms=0.0,
                algorithm="empty",
            )

        import time

        start_time = time.time()

        # 分离必选事件和其他
        root_events = [e for e in events if e.is_root]
        optional_events = [e for e in events if not e.is_root]

        # 计算必选事件的 token 消耗
        root_tokens = sum(e.token_cost for e in root_events)
        root_value = sum(e.importance_score for e in root_events)

        if root_tokens > token_budget:
            # 预算不足以容纳必选事件，使用贪心裁剪
            logger.warning("Token budget insufficient for root events, using fallback")
            return self._greedy_select(events, token_budget, start_time)

        # 可用于可选事件的预算
        optional_budget = token_budget - root_tokens

        if not optional_events:
            # 没有可选事件
            return BudgetAllocation(
                selected_events=[e.event_id for e in root_events],
                total_tokens=root_tokens,
                total_value=root_value,
                optimization_time_ms=(time.time() - start_time) * 1000,
                algorithm="roots_only",
            )

        # 尝试 OR-Tools 背包求解
        if self._ensure_ortools():
            result = self._knapsack_solve(optional_events, optional_budget, root_events, start_time)
            if result:
                return result

        # 回退到贪心算法
        return self._greedy_select(events, token_budget, start_time)

    def _knapsack_solve(
        self,
        optional_events: list[EventItem],
        optional_budget: int,
        root_events: list[EventItem],
        start_time: float,
    ) -> BudgetAllocation | None:
        """使用 OR-Tools 背包求解器

        Args:
            optional_events: 可选事件列表
            optional_budget: 可选事件的预算
            root_events: 必选事件列表
            start_time: 开始时间

        Returns:
            优化结果，如果失败返回 None
        """
        try:
            from ortools.algorithms import pywrapknapsack_solver

            # 准备数据
            values = [int(e.importance_score * 1000) for e in optional_events]
            weights = [[e.token_cost for e in optional_events]]
            capacities = [optional_budget]

            # 创建求解器
            solver = pywrapknapsack_solver.PywrapKnapsackSolver(
                pywrapknapsack_solver.KNAPSACK_MULTIDIMENSIONAL_BRANCH_AND_BOUND_SOLVER,
                "ContextOSBudgetOptimizer",
            )
            solver.Init(values, weights, capacities)
            solver.Solve()

            # 提取选中的事件
            selected = []
            total_tokens = 0
            total_value = 0.0
            for i, e in enumerate(optional_events):
                if solver.BestSolutionContains(i):
                    selected.append(e.event_id)
                    total_tokens += e.token_cost
                    total_value += e.importance_score

            # 添加必选事件
            root_ids = [e.event_id for e in root_events]
            root_tokens = sum(e.token_cost for e in root_events)
            root_value = sum(e.importance_score for e in root_events)

            import time

            return BudgetAllocation(
                selected_events=root_ids + selected,
                total_tokens=root_tokens + total_tokens,
                total_value=root_value + total_value,
                optimization_time_ms=(time.time() - start_time) * 1000,
                algorithm="knapsack",
            )

        except (RuntimeError, ValueError) as e:
            logger.debug(f"OR-Tools knapsack failed: {e}")
            return None

    def _greedy_select(
        self,
        events: list[EventItem],
        token_budget: int,
        start_time: float,
    ) -> BudgetAllocation:
        """贪心选择算法

        按价值密度 (importance_score / token_cost) 排序选择。

        Args:
            events: 事件列表
            token_budget: token 预算
            start_time: 开始时间

        Returns:
            贪心选择结果
        """
        import time

        # 分离必选和其他
        root_events = [e for e in events if e.is_root]
        optional_events = [e for e in events if not e.is_root]

        root_tokens = sum(e.token_cost for e in root_events)
        root_value = sum(e.importance_score for e in root_events)

        if root_tokens > token_budget:
            # 需要裁剪必选事件
            root_events.sort(key=lambda e: e.token_cost / max(e.importance_score, 0.01))
            selected_roots = []
            used_tokens = 0
            used_value = 0.0
            for e in root_events:
                if used_tokens + e.token_cost <= token_budget:
                    selected_roots.append(e.event_id)
                    used_tokens += e.token_cost
                    used_value += e.importance_score
            return BudgetAllocation(
                selected_events=selected_roots,
                total_tokens=used_tokens,
                total_value=used_value,
                optimization_time_ms=(time.time() - start_time) * 1000,
                algorithm="greedy",
            )

        # 贪心选择可选事件
        optional_events.sort(
            key=lambda e: e.importance_score / max(e.token_cost, 1),
            reverse=True,
        )

        selected_optional = []
        used_tokens = root_tokens
        used_value = root_value

        for e in optional_events:
            if used_tokens + e.token_cost <= token_budget:
                selected_optional.append(e.event_id)
                used_tokens += e.token_cost
                used_value += e.importance_score

        return BudgetAllocation(
            selected_events=[e.event_id for e in root_events] + selected_optional,
            total_tokens=used_tokens,
            total_value=used_value,
            optimization_time_ms=(time.time() - start_time) * 1000,
            algorithm="greedy",
        )

    def compute_importance_score(
        self,
        event: Any,
        route: str,
        is_recent: bool = False,
        is_related_to_goal: bool = False,
    ) -> float:
        """计算事件重要性分数

        综合考虑路由类型、时效性、目标相关性等因素。

        Args:
            event: 事件对象
            route: 路由类型
            is_recent: 是否是最近事件
            is_related_to_goal: 是否与当前目标相关

        Returns:
            重要性分数 (0.0 - 1.0)
        """
        # 基础分数来自路由
        route_scores = {
            "patch": 1.0,
            "clear": 0.8,
            "summarize": 0.6,
            "archive": 0.3,
        }
        base_score = route_scores.get(route.lower(), 0.5)

        # 时效性调整
        if is_recent:
            base_score = min(1.0, base_score * 1.2)

        # 目标相关性调整
        if is_related_to_goal:
            base_score = min(1.0, base_score * 1.3)

        # 错误关键字调整（使用 event.content 如果可用）
        content = getattr(event, "content", "")
        content_lower = content.lower() if isinstance(content, str) else ""
        critical_keywords = {
            "error",
            "exception",
            "failed",
            "failure",
            "crash",
            "timeout",
            "deadlock",
            "corruption",
            "traceback",
        }
        if any(kw in content_lower for kw in critical_keywords):
            base_score = min(1.0, base_score * 1.4)

        return base_score

    def is_available(self) -> bool:
        """检查 OR-Tools 是否可用"""
        try:
            from ortools.algorithms import pywrapknapsack_solver  # noqa: F401

            return True
        except ImportError:
            return False


class HeuristicBudgetOptimizer:
    """启发式预算优化器 (OR-Tools 不可用时的替代方案)"""

    def optimize(
        self,
        events: list[EventItem],
        token_budget: int,
    ) -> BudgetAllocation:
        """使用启发式规则优化事件选择

        规则:
        1. 必选事件必须保留
        2. 优先保留 PATCH > CLEAR > SUMMARIZE > ARCHIVE
        3. 近期事件优先
        4. 同等重要性时，选择 token 成本低的

        Args:
            events: 事件列表
            token_budget: token 预算

        Returns:
            优化结果
        """
        import time

        start_time = time.time()

        # 分离必选和其他
        root_events = {e.event_id: e for e in events if e.is_root}
        optional_events = [e for e in events if not e.is_root]

        root_tokens = sum(e.token_cost for e in root_events.values())

        if root_tokens > token_budget:
            # 裁剪必选事件
            sorted_roots = sorted(
                root_events.values(),
                key=lambda e: (e.token_cost / max(e.importance_score, 0.01), e.sequence),
            )
            selected = []
            used_tokens = 0
            for e in sorted_roots:
                if used_tokens + e.token_cost <= token_budget:
                    selected.append(e.event_id)
                    used_tokens += e.token_cost

            return BudgetAllocation(
                selected_events=selected,
                total_tokens=used_tokens,
                total_value=sum(root_events[eid].importance_score for eid in selected),
                optimization_time_ms=(time.time() - start_time) * 1000,
                algorithm="heuristic",
            )

        # 可选事件按规则排序
        route_priority = {"patch": 0, "clear": 1, "summarize": 2, "archive": 3}
        optional_events.sort(
            key=lambda e: (
                route_priority.get(e.route.lower(), 99),
                -e.importance_score,
                e.token_cost,
                -e.sequence,
            )
        )

        selected_optional = []
        used_tokens = root_tokens
        used_value = sum(e.importance_score for e in root_events.values())

        for e in optional_events:
            if used_tokens + e.token_cost <= token_budget:
                selected_optional.append(e.event_id)
                used_tokens += e.token_cost
                used_value += e.importance_score

        return BudgetAllocation(
            selected_events=list(root_events.keys()) + selected_optional,
            total_tokens=used_tokens,
            total_value=used_value,
            optimization_time_ms=(time.time() - start_time) * 1000,
            algorithm="heuristic",
        )
