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
    ) -> None:
        emit(
            SpeculationEvent(
                event_type="speculation.resolve.join",
                turn_id=turn_id,
                call_id=call_id,
                tool_name=tool_name,
                spec_key=spec_key,
                action="join",
            )
        )

    def record_replay(
        self,
        turn_id: str,
        call_id: str,
        tool_name: str,
        reason: str,
    ) -> None:
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
