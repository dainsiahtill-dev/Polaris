from __future__ import annotations

from typing import Any

from polaris.cells.roles.kernel.internal.speculation.models import (
    BudgetSnapshot,
    ToolSpecPolicy,
)


class BudgetGovernor:
    """推测执行预算治理器：负责准入控制与降级决策.

    根据实时指标快照和运行模式，决定是否允许新的 speculative task 启动。
    支持 turbo / balanced / safe 三种模式，并在指标恶化时自动降级。
    """

    def __init__(
        self,
        *,
        mode: str = "balanced",
        wrong_adoption_count: int = 0,
    ) -> None:
        """初始化预算治理器.

        Args:
            mode: 运行模式，可选 "turbo" | "balanced" | "safe"
            wrong_adoption_count: 历史错误采用计数
        """
        self._mode = mode
        self._wrong_adoption_count = wrong_adoption_count

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        self._mode = value

    @property
    def wrong_adoption_count(self) -> int:
        return self._wrong_adoption_count

    @wrong_adoption_count.setter
    def wrong_adoption_count(self, value: int) -> None:
        self._wrong_adoption_count = value

    def _spec_tier(self, policy: ToolSpecPolicy) -> int:
        """将工具策略映射到推测层级 S0-S3.

        S0: 纯读取、低成本、可取消（最安全）
        S1: 纯读取或 dry_run，中成本
        S2: 外部可见副作用，高置信度
        S3: 其他允许推测的场景
        """
        if policy.speculate_mode == "forbid":
            return -1
        if policy.side_effect == "pure" and policy.cost == "cheap":
            return 0
        if policy.side_effect in {"pure", "readonly"} and policy.cost in {"cheap", "medium"}:
            return 1
        if policy.speculate_mode == "high_confidence_only":
            return 2
        if policy.speculate_mode == "speculative_allowed":
            return 3
        return 2

    def _max_allowed_tier(self, snapshot: BudgetSnapshot) -> int:
        """根据当前快照和模式计算允许的最大推测层级.

        降级规则：
        - abandonment_ratio > 60%  → 降级到 S0/S1
        - timeout_ratio > 20%      → 降级一级
        - wrong_adoption_count > 0 → 暂停所有推测
        """
        base_max = {"turbo": 3, "balanced": 2, "safe": 1}.get(self._mode, 2)

        if self._wrong_adoption_count > 0:
            return -1

        effective_max = base_max

        if snapshot.abandonment_ratio > 0.6:
            effective_max = min(effective_max, 1)

        if snapshot.timeout_ratio > 0.2:
            effective_max = max(effective_max - 1, 0)

        return effective_max

    def admit(
        self,
        tool_policy: ToolSpecPolicy,
        snapshot: BudgetSnapshot,
    ) -> dict[str, Any]:
        """评估是否允许启动新的 speculative task.

        Args:
            tool_policy: 目标工具的推测策略
            snapshot: 当前预算与压力快照

        Returns:
            {"allowed": bool, "reason": str | None}
        """
        tier = self._spec_tier(tool_policy)
        if tier < 0:
            return {"allowed": False, "reason": "speculation_forbidden_by_policy"}

        max_tier = self._max_allowed_tier(snapshot)

        if max_tier < 0:
            return {"allowed": False, "reason": "speculation_paused_due_to_wrong_adoption"}

        if tier > max_tier:
            return {
                "allowed": False,
                "reason": f"tier_{tier}_exceeds_max_allowed_{max_tier}",
            }

        mode_limits = {"turbo": 8, "balanced": 4, "safe": 2}
        max_active = mode_limits.get(self._mode, 4)
        if snapshot.active_shadow_tasks >= max_active:
            return {
                "allowed": False,
                "reason": f"active_tasks_{snapshot.active_shadow_tasks}_at_limit_{max_active}",
            }

        return {"allowed": True, "reason": None}
