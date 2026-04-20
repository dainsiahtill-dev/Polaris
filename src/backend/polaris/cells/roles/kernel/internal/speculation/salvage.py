from __future__ import annotations

import time

from polaris.cells.roles.kernel.internal.speculation.models import (
    SalvageDecision,
    ShadowTaskRecord,
    ShadowTaskState,
)


class SalvageGovernor:
    """Cancel-or-Salvage 策略引擎：在取消或 drain 时评估每个 shadow task 的命运."""

    def __init__(
        self,
        *,
        progress_high_threshold: float = 0.80,
        progress_low_threshold: float = 0.20,
        near_deadline_ratio: float = 0.90,
    ) -> None:
        self._progress_high = progress_high_threshold
        self._progress_low = progress_low_threshold
        self._near_deadline_ratio = near_deadline_ratio

    def evaluate(self, record: ShadowTaskRecord) -> SalvageDecision:
        """基于任务状态、策略快照和进度估计，返回 salvage 决策.

        决策优先级：
        1. 如果任务不可取消或已接近完成，允许完成并缓存。
        2. 如果任务刚开始且成本较低/可取消，立刻取消。
        3. 其余情况默认 JOIN_AUTHORITATIVE（由 authoritative 路径接管）。
        """
        if record.state not in {
            ShadowTaskState.STARTING,
            ShadowTaskState.RUNNING,
            ShadowTaskState.CANCEL_REQUESTED,
        }:
            # 非运行中任务无需 salvage 决策
            return SalvageDecision.CANCEL_NOW

        policy = record.policy_snapshot
        progress = self._estimate_progress(record)

        # 高进度或接近截止：允许完成并缓存
        if progress >= self._progress_high:
            return SalvageDecision.LET_FINISH_AND_CACHE

        elapsed_ms = self._elapsed_ms(record)
        timeout_ms = float(policy.timeout_ms)
        if timeout_ms > 0 and elapsed_ms >= timeout_ms * self._near_deadline_ratio:
            return SalvageDecision.LET_FINISH_AND_CACHE

        # 不可取消：优先 JOIN，否则允许完成
        if policy.cancellability == "non_cancelable":
            return SalvageDecision.JOIN_AUTHORITATIVE

        # 低进度且可取消：立刻取消
        if progress <= self._progress_low and policy.cancellability == "cooperative":
            return SalvageDecision.CANCEL_NOW

        # 默认：由 authoritative 路径接管（JOIN）
        return SalvageDecision.JOIN_AUTHORITATIVE

    def _estimate_progress(self, record: ShadowTaskRecord) -> float:
        """基于已用时间与超时设置估计进度 [0.0, 1.0].

        Phase 2 使用时间比例作为代理；Phase 4 可由 runner 上报真实进度。
        """
        timeout_ms = float(record.policy_snapshot.timeout_ms)
        if timeout_ms <= 0:
            return 0.0
        elapsed_ms = self._elapsed_ms(record)
        # 保守估计：假设时间与进度呈线性关系
        ratio = elapsed_ms / timeout_ms
        # 对快速完成的工具做轻微上调（缓存命中场景）
        if ratio < 0.1 and record.tool_name in {"stat_file", "exists", "list_directory", "cache_lookup"}:
            return min(ratio + 0.3, 0.95)
        return min(max(ratio, 0.0), 1.0)

    def _elapsed_ms(self, record: ShadowTaskRecord) -> float:
        """计算任务已运行毫秒数."""
        started_at = record.started_at
        if started_at is None:
            return 0.0
        return (time.monotonic() - started_at) * 1000.0

    def batch_evaluate(self, records: list[ShadowTaskRecord]) -> dict[str, SalvageDecision]:
        """批量评估多个 shadow task."""
        return {record.task_id: self.evaluate(record) for record in records}
