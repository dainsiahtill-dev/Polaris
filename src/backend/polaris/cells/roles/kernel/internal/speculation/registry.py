from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import Any

from polaris.cells.roles.kernel.internal.speculation.budget import BudgetGovernor
from polaris.cells.roles.kernel.internal.speculation.events import (
    SpeculationEvent,
    emit,
)
from polaris.cells.roles.kernel.internal.speculation.metrics import (
    SpeculationMetrics,
)
from polaris.cells.roles.kernel.internal.speculation.models import (
    BudgetSnapshot,
    CancelToken,
    CandidateToolCall,
    SalvageDecision,
    ShadowTaskRecord,
    ShadowTaskState,
    ToolSpecPolicy,
    check_cancel,
)
from polaris.cells.roles.kernel.internal.speculation.salvage import SalvageGovernor
from polaris.cells.roles.kernel.internal.speculation.task_group import (
    TurnScopedTaskGroup,
)
from polaris.cells.roles.kernel.internal.speculative_executor import (
    SpeculativeExecutor,
)


def _new_id(prefix: str = "shadow") -> str:
    """生成轻量级唯一标识符."""
    return f"{prefix}_{int(time.time() * 1000)}_{id(object())}"


class ShadowTaskRegistry:
    """Shadow task 注册表：原子协调者，管理推测任务的全生命周期."""

    def __init__(
        self,
        *,
        speculative_executor: SpeculativeExecutor,
        metrics: SpeculationMetrics,
        cache: EphemeralSpecCache | None = None,
        budget_governor: BudgetGovernor | None = None,
        on_shadow_completed: Callable[[ShadowTaskRecord], Awaitable[Any]] | None = None,
    ) -> None:
        self._speculative_executor = speculative_executor
        self._metrics = metrics
        self._cache = cache
        self._budget_governor = budget_governor
        self._on_shadow_completed = on_shadow_completed
        self._tasks_by_id: dict[str, ShadowTaskRecord] = {}
        self._active_spec_index: dict[str, str] = {}
        self._turn_index: dict[str, set[str]] = {}
        self._chain_index: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()

    def _is_active(self, record: ShadowTaskRecord) -> bool:
        """判断任务是否仍处于可查询/可复用的活跃状态."""
        return record.state not in {
            ShadowTaskState.CANCELLED,
            ShadowTaskState.FAILED,
            ShadowTaskState.ABANDONED,
        } and not (
            record.state == ShadowTaskState.COMPLETED
            and record.expiry_at is not None
            and time.monotonic() > record.expiry_at
        )

    def _gc_expired(self, spec_key: str) -> None:
        """清理已过期任务的索引条目."""
        task_id = self._active_spec_index.get(spec_key)
        if not task_id:
            return
        record = self._tasks_by_id.get(task_id)
        if record is None or not self._is_active(record):
            self._active_spec_index.pop(spec_key, None)

    def lookup(self, spec_key: str) -> ShadowTaskRecord | None:
        """通过 spec_key 查询活跃的影子任务."""
        self._gc_expired(spec_key)
        task_id = self._active_spec_index.get(spec_key)
        if not task_id:
            return None
        return self._tasks_by_id.get(task_id)

    def exists_active(self, spec_key: str) -> bool:
        """检查是否存在可复用的活跃任务."""
        return self.lookup(spec_key) is not None

    def _build_budget_snapshot(self) -> BudgetSnapshot:
        """基于当前注册表和指标构建预算快照."""
        active_count = sum(
            1
            for record in self._tasks_by_id.values()
            if record.state in {ShadowTaskState.STARTING, ShadowTaskState.RUNNING}
        )
        mode = "balanced"
        if self._budget_governor is not None:
            mode = self._budget_governor.mode
        return BudgetSnapshot(
            mode=mode,
            active_shadow_tasks=active_count,
            abandonment_ratio=self._metrics.abandonment_ratio,
            timeout_ratio=self._metrics.timeout_ratio,
            queue_pressure=0.0,
            cpu_pressure=0.0,
            memory_pressure=0.0,
            external_quota_pressure=0.0,
            wrong_adoption_count=getattr(self._budget_governor, "wrong_adoption_count", 0),
        )

    async def start_shadow_task(
        self,
        *,
        turn_id: str,
        candidate_id: str,
        tool_name: str,
        normalized_args: dict[str, Any],
        spec_key: str,
        env_fingerprint: str,
        policy: ToolSpecPolicy,
        cost_estimate: float = 0.0,
        budget_snapshot: BudgetSnapshot | None = None,
        parent_task_id: str | None = None,
    ) -> ShadowTaskRecord:
        """启动一个新的 shadow task，保证同一 spec_key 同时最多一个 active task."""
        async with self._lock:
            self._gc_expired(spec_key)
            existing_id = self._active_spec_index.get(spec_key)
            if existing_id:
                existing = self._tasks_by_id.get(existing_id)
                if existing is not None:
                    return existing

            if self._budget_governor is not None:
                snapshot = budget_snapshot or self._build_budget_snapshot()
                decision = self._budget_governor.admit(policy, snapshot)
                if not decision["allowed"]:
                    reason = decision.get("reason") or "budget_denied"
                    self._metrics.record_skip(
                        CandidateToolCall(
                            candidate_id=candidate_id,
                            stream_id="",
                            turn_id=turn_id,
                            tool_name=tool_name,
                        ),
                        reason,
                    )
                    raise RuntimeError(f"shadow task denied: {reason}")

            task_id = _new_id("shadow")
            cancel_token = CancelToken()

            record = ShadowTaskRecord(
                task_id=task_id,
                origin_turn_id=turn_id,
                origin_candidate_id=candidate_id,
                tool_name=tool_name,
                normalized_args=normalized_args,
                spec_key=spec_key,
                env_fingerprint=env_fingerprint,
                policy_snapshot=policy,
                state=ShadowTaskState.STARTING,
                started_at=time.monotonic(),
                cost_estimate=cost_estimate,
            )
            self._tasks_by_id[task_id] = record
            self._active_spec_index[spec_key] = task_id
            self._turn_index.setdefault(turn_id, set()).add(task_id)
            if parent_task_id is not None:
                self._chain_index.setdefault(parent_task_id, set()).add(task_id)

            async def _runner() -> Any:
                record.state = ShadowTaskState.RUNNING
                started_at = time.monotonic()
                try:
                    check_cancel(cancel_token)
                    result = await self._speculative_executor.execute_speculative(
                        tool_name=tool_name,
                        args=normalized_args,
                        timeout_ms=policy.timeout_ms,
                        cancel_token=cancel_token,
                    )
                    record.result = result
                    record.state = ShadowTaskState.COMPLETED
                    record.finished_at = time.monotonic()
                    ttl_s = policy.cache_ttl_ms / 1000.0
                    record.expiry_at = record.finished_at + ttl_s
                    duration_ms = int((record.finished_at - started_at) * 1000)
                    self._metrics.record_completed(task_id, duration_ms)
                    if self._cache is not None:
                        await self._cache.put(record)
                    if self._on_shadow_completed is not None:
                        with contextlib.suppress(Exception):
                            await self._on_shadow_completed(record)
                    return result
                except asyncio.CancelledError as exc:
                    record.state = ShadowTaskState.CANCELLED
                    record.finished_at = time.monotonic()
                    record.cancel_reason = str(exc) if str(exc) else "cancelled"
                    self._metrics.record_cancel(task_id, record.cancel_reason)
                    raise
                except Exception as exc:
                    record.state = ShadowTaskState.FAILED
                    record.error = repr(exc)
                    record.finished_at = time.monotonic()
                    self._metrics.record_failed(task_id, record.error)
                    raise

            record.future = asyncio.create_task(_runner(), name=f"shadow:{task_id}")
            return record

    async def adopt(self, task_id: str, call_id: str) -> Any:
        """采用一个已完成的 shadow task 结果."""
        async with self._lock:
            record = self._tasks_by_id.get(task_id)
            if record is None:
                raise RuntimeError(f"cannot adopt unknown task: {task_id}")
            if record.state != ShadowTaskState.COMPLETED:
                raise RuntimeError(f"cannot adopt non-completed task: {task_id} (state={record.state.value})")
            record.state = ShadowTaskState.ADOPTED
            record.adopted_by_call_id = call_id
            return record.result

    async def join(self, task_id: str, call_id: str) -> Any:
        """加入一个正在运行的 shadow task，等待其完成并返回结果."""
        record = self._tasks_by_id.get(task_id)
        if record is None:
            raise RuntimeError(f"cannot join unknown task: {task_id}")
        record.adopted_by_call_id = call_id
        if record.future is None:
            raise RuntimeError(f"task {task_id} has no future to join")
        return await record.future

    async def cancel(self, task_id: str, reason: str) -> None:
        """请求取消指定任务，并级联取消所有下游任务."""
        async with self._lock:
            record = self._tasks_by_id.get(task_id)
            if record is not None and record.state in {
                ShadowTaskState.STARTING,
                ShadowTaskState.RUNNING,
            }:
                record.state = ShadowTaskState.CANCEL_REQUESTED
                record.cancel_reason = reason
                if record.future is not None:
                    record.future.cancel(msg=reason)
                self._metrics.record_cancel(task_id, reason)
        # 级联取消下游(在锁外避免死锁)
        await self._cascade_cancel(task_id, reason)

    async def _cascade_cancel(self, upstream_task_id: str, reason: str) -> None:
        """级联取消上游任务触发的所有下游 shadow task."""
        downstream_ids = list(self._chain_index.get(upstream_task_id, set()))
        for downstream_id in downstream_ids:
            await self.cancel(downstream_id, reason=reason)

    def get_turn_records(self, turn_id: str) -> list[ShadowTaskRecord]:
        """获取指定 turn 的所有 shadow task 记录(只读快照)."""
        task_ids = list(self._turn_index.get(turn_id, set()))
        return [self._tasks_by_id[task_id] for task_id in task_ids if task_id in self._tasks_by_id]

    async def drain_turn(
        self,
        turn_id: str,
        *,
        timeout_s: float = 0.2,
        salvage_governor: SalvageGovernor | None = None,
        task_group: TurnScopedTaskGroup | None = None,
    ) -> None:
        """Drain 指定 turn 的所有 shadow task：先 salvage 评估，再分别处理."""
        task_ids = list(self._turn_index.get(turn_id, set()))
        if not task_ids:
            return

        records = [self._tasks_by_id[task_id] for task_id in task_ids if task_id in self._tasks_by_id]

        # 先给 running tasks 一个机会在 timeout_s 内自然完成, 以便后续 ADOPT/JOIN
        running_futures = [r.future for r in records if r.future is not None and not r.future.done()]
        if running_futures:
            await asyncio.wait(running_futures, timeout=timeout_s, return_when=asyncio.ALL_COMPLETED)

        # Phase 2: 优先使用 salvage 策略分别处理运行中任务
        salvaged_ids: set[str] = set()
        if salvage_governor is not None and task_group is not None:
            running_records = [r for r in records if r.state in {ShadowTaskState.STARTING, ShadowTaskState.RUNNING}]
            if running_records:
                decisions = await task_group.cancel_with_salvage(running_records)
                for task_id, decision in decisions.items():
                    if decision in {SalvageDecision.LET_FINISH_AND_CACHE, SalvageDecision.JOIN_AUTHORITATIVE}:
                        salvaged_ids.add(task_id)

        # 对未被 salvage 保留的任务执行传统 cancel
        pending_cancels: list[asyncio.Task[Any]] = []
        for task_id in task_ids:
            record = self._tasks_by_id.get(task_id)
            if record is None:
                continue
            if task_id in salvaged_ids:
                continue
            if record.state in {
                ShadowTaskState.STARTING,
                ShadowTaskState.RUNNING,
            }:
                pending_cancels.append(
                    asyncio.create_task(
                        self.cancel(task_id, reason="turn_drain"),
                        name=f"cancel:{task_id}",
                    )
                )

        if pending_cancels:
            done, _ = await asyncio.wait(pending_cancels, timeout=timeout_s, return_when=asyncio.ALL_COMPLETED)
            for task in done:
                with contextlib.suppress(Exception):
                    task.result()

        # 清理该 turn 的过期/终止任务索引
        for task_id in task_ids:
            record = self._tasks_by_id.get(task_id)
            if record is None:
                continue
            if record.state in {
                ShadowTaskState.CANCELLED,
                ShadowTaskState.FAILED,
                ShadowTaskState.ABANDONED,
            }:
                self._active_spec_index.pop(record.spec_key, None)
                self._turn_index.get(turn_id, set()).discard(task_id)

        emit(
            SpeculationEvent(
                event_type="speculation.guardrail.turn_drained",
                turn_id=turn_id,
                action="drain",
                reason=f"done={len(task_ids) - len(pending_cancels)}, cancelled={len(pending_cancels)}",
            )
        )

    async def mark_abandoned(self, task_id: str, reason: str) -> None:
        """将任务标记为废弃(例如 refusal abort 后)."""
        async with self._lock:
            record = self._tasks_by_id.get(task_id)
            if record is None:
                return
            if record.state in {
                ShadowTaskState.ADOPTED,
                ShadowTaskState.CANCELLED,
                ShadowTaskState.FAILED,
            }:
                return
            record.state = ShadowTaskState.ABANDONED
            record.cancel_reason = reason
            self._active_spec_index.pop(record.spec_key, None)
            self._metrics.record_abandon(task_id, reason)

    async def abandon_turn(self, turn_id: str, reason: str) -> None:
        """批量将整个 turn 的 shadow task 标记为废弃或取消，并级联取消下游."""
        task_ids = list(self._turn_index.get(turn_id, set()))
        for task_id in task_ids:
            record = self._tasks_by_id.get(task_id)
            if record is None:
                continue
            if record.state in {
                ShadowTaskState.STARTING,
                ShadowTaskState.RUNNING,
                ShadowTaskState.CANCEL_REQUESTED,
            }:
                if record.future is not None and not record.future.done():
                    record.future.cancel(msg=reason)
                record.state = ShadowTaskState.CANCELLED
                record.cancel_reason = reason
                self._active_spec_index.pop(record.spec_key, None)
            elif record.state not in {
                ShadowTaskState.ADOPTED,
                ShadowTaskState.CANCELLED,
                ShadowTaskState.FAILED,
            }:
                record.state = ShadowTaskState.ABANDONED
                record.cancel_reason = reason
                self._active_spec_index.pop(record.spec_key, None)
        # 级联取消下游
        for task_id in task_ids:
            await self._cascade_cancel(task_id, reason=reason)
        emit(
            SpeculationEvent(
                event_type="speculation.guardrail.turn_abandoned",
                turn_id=turn_id,
                action="abandon_turn",
                reason=reason,
            )
        )


class EphemeralSpecCache:
    """短生命周期 speculative 结果缓存(Phase 1 最小实现)."""

    def __init__(self, *, ttl_ms: float = 3000.0) -> None:
        self._ttl_ms = ttl_ms
        self._store: dict[str, Any] = {}
        self._timestamps: dict[str, float] = {}

    async def put(self, record: ShadowTaskRecord) -> None:
        self._store[record.spec_key] = record.result
        self._timestamps[record.spec_key] = time.monotonic()

    def get(self, spec_key: str) -> Any | None:
        ts = self._timestamps.get(spec_key)
        if ts is None:
            return None
        if (time.monotonic() - ts) * 1000 > self._ttl_ms:
            self._store.pop(spec_key, None)
            self._timestamps.pop(spec_key, None)
            return None
        return self._store.get(spec_key)
