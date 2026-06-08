from __future__ import annotations

from polaris.cells.roles.kernel.internal.speculation.events import (
    SpeculationEvent,
    emit,
)
from polaris.cells.roles.kernel.internal.speculation.models import (
    CandidateToolCall,
)


class SpeculationMetrics:
    """推测执行指标记录器.

    Phase 1 先以统一事件日志为核心，后续可接入 Prometheus / statsd.
    """

    def __init__(self) -> None:
        self._started_count: int = 0
        self._abandoned_count: int = 0
        self._timed_out_count: int = 0
        self._completed_count: int = 0
        # 裁决侧细粒度计数器（per-turn 可观测，支撑评测矩阵）
        self._eligible_dropped_count: int = 0
        self._failed_count: int = 0
        self._cancelled_count: int = 0
        self._adopted_count: int = 0
        self._joined_count: int = 0
        self._replayed_count: int = 0
        self._saved_ms_total: int = 0
        self._wrong_adoption_count: int = 0
        self._summary_emitted: bool = False

    @property
    def abandonment_ratio(self) -> float:
        """计算废弃率 = abandoned / (completed + abandoned + cancelled + failed)."""
        denominator = self._completed_count + self._abandoned_count
        if denominator == 0:
            return 0.0
        return self._abandoned_count / denominator

    @property
    def timeout_ratio(self) -> float:
        """计算超时率 = timed_out / started."""
        if self._started_count == 0:
            return 0.0
        return self._timed_out_count / self._started_count

    @property
    def wrong_adoption_count(self) -> int:
        """错误领养计数（ADR-0077 守门指标，工业级要求恒为 0）."""
        return self._wrong_adoption_count

    @property
    def hit_rate(self) -> float:
        """命中率 = (adopted + joined) / (adopted + joined + replayed)."""
        resolved = self._adopted_count + self._joined_count + self._replayed_count
        if resolved == 0:
            return 0.0
        return (self._adopted_count + self._joined_count) / resolved

    def record_wrong_adoption(self, *, reason: str = "") -> None:
        """登记一次错误领养（correctness 被破坏）.

        由评测层在 spec ON/OFF 结果不一致时调用，也可由内核完整性校验触发。
        """
        self._wrong_adoption_count += 1
        emit(
            SpeculationEvent(
                event_type="speculation.resolve.wrong_adoption",
                turn_id="",
                action="wrong_adoption",
                reason=reason,
            )
        )

    def emit_turn_summary(self, turn_id: str) -> dict[str, float | int]:
        """在 turn 收尾发射一个携带完整快照的 per-turn 汇总事件.

        该事件以 ``turn_id`` 标记，并把 :meth:`snapshot` 放入 ``metadata``，
        让任意订阅者（评测矩阵、Prometheus 导出器）拿到一个干净的、可归因到
        具体 turn 的推测执行汇总，而不必逐条聚合细粒度事件。返回该快照。

        对每个 metrics 实例（= 每个 turn 一个）幂等：重复调用只返回快照、不再
        发射事件——用于保护重试等可能二次调用的路径，避免在评测里被重复计入。
        """
        snap = self.snapshot()
        if self._summary_emitted:
            return snap
        self._summary_emitted = True
        emit(
            SpeculationEvent(
                event_type="speculation.turn.summary",
                turn_id=turn_id,
                action="summary",
                saved_ms=int(snap["saved_ms_total"]),
                metadata=dict(snap),
            )
        )
        return snap

    def snapshot(self) -> dict[str, float | int]:
        """返回当前 turn 的推测执行指标快照（纯 dict，供 ledger/turn 结果记录）."""
        return {
            "started": self._started_count,
            "completed": self._completed_count,
            "eligible_dropped": self._eligible_dropped_count,
            "adopted": self._adopted_count,
            "joined": self._joined_count,
            "replayed": self._replayed_count,
            "cancelled": self._cancelled_count,
            "failed": self._failed_count,
            "abandoned": self._abandoned_count,
            "timed_out": self._timed_out_count,
            "saved_ms_total": self._saved_ms_total,
            "wrong_adoption": self._wrong_adoption_count,
            "hit_rate": round(self.hit_rate, 4),
            "abandonment_ratio": round(self.abandonment_ratio, 4),
            "timeout_ratio": round(self.timeout_ratio, 4),
        }

    def record_started(self, candidate: CandidateToolCall, spec_key: str) -> None:
        self._started_count += 1
        emit(
            SpeculationEvent(
                event_type="speculation.shadow.started",
                turn_id=candidate.turn_id,
                stream_id=candidate.stream_id,
                candidate_id=candidate.candidate_id,
                tool_name=candidate.tool_name,
                spec_key=spec_key,
                stability_score=candidate.stability_score,
                parse_state=candidate.parse_state,
                action="start",
                reason="eligible",
            )
        )

    def record_completed(self, task_id: str, duration_ms: int) -> None:
        self._completed_count += 1
        emit(
            SpeculationEvent(
                event_type="speculation.shadow.completed",
                turn_id="",
                task_id=task_id,
                latency_ms=duration_ms,
                action="complete",
            )
        )

    def record_failed(self, task_id: str, error: str) -> None:
        self._failed_count += 1
        emit(
            SpeculationEvent(
                event_type="speculation.shadow.failed",
                turn_id="",
                task_id=task_id,
                action="fail",
                reason=error,
            )
        )

    def record_cancel(self, task_id: str, reason: str) -> None:
        self._cancelled_count += 1
        if "timeout" in reason.lower() or "timed_out" in reason.lower():
            self._timed_out_count += 1
        emit(
            SpeculationEvent(
                event_type="speculation.shadow.cancelled",
                turn_id="",
                task_id=task_id,
                action="cancel",
                reason=reason,
            )
        )

    def record_abandon(self, task_id: str, reason: str) -> None:
        self._abandoned_count += 1
        emit(
            SpeculationEvent(
                event_type="speculation.shadow.abandoned",
                turn_id="",
                task_id=task_id,
                action="abandon",
                reason=reason,
            )
        )

    def record_skip(self, candidate: CandidateToolCall, reason: str) -> None:
        self._eligible_dropped_count += 1
        emit(
            SpeculationEvent(
                event_type="speculation.candidate.dropped",
                turn_id=candidate.turn_id,
                stream_id=candidate.stream_id,
                candidate_id=candidate.candidate_id,
                tool_name=candidate.tool_name,
                stability_score=candidate.stability_score,
                parse_state=candidate.parse_state,
                action="skip",
                reason=reason,
            )
        )

    def record_adopt(
        self,
        turn_id: str,
        call_id: str,
        tool_name: str,
        spec_key: str,
        saved_ms: int | None = None,
    ) -> None:
        self._adopted_count += 1
        if saved_ms is not None and saved_ms > 0:
            self._saved_ms_total += saved_ms
        emit(
            SpeculationEvent(
                event_type="speculation.resolve.adopt",
                turn_id=turn_id,
                call_id=call_id,
                tool_name=tool_name,
                spec_key=spec_key,
                action="adopt",
                saved_ms=saved_ms,
            )
        )

    def record_join(
        self,
        turn_id: str,
        call_id: str,
        tool_name: str,
        spec_key: str,
        saved_ms: int | None = None,
    ) -> None:
        self._joined_count += 1
        if saved_ms is not None and saved_ms > 0:
            self._saved_ms_total += saved_ms
        emit(
            SpeculationEvent(
                event_type="speculation.resolve.join",
                turn_id=turn_id,
                call_id=call_id,
                tool_name=tool_name,
                spec_key=spec_key,
                action="join",
                saved_ms=saved_ms,
            )
        )

    def record_replay(
        self,
        turn_id: str,
        call_id: str,
        tool_name: str,
        reason: str,
    ) -> None:
        self._replayed_count += 1
        emit(
            SpeculationEvent(
                event_type="speculation.resolve.replay",
                turn_id=turn_id,
                call_id=call_id,
                tool_name=tool_name,
                action="replay",
                reason=reason,
            )
        )
