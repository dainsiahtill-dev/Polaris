"""Polaris AI Platform - Telemetry

统一观测：事件、token、耗时、错误分类。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name
from polaris.kernelone.constants import DEFAULT_TELEMETRY_BUFFER_SIZE
from polaris.kernelone.utils import utc_now_iso

if TYPE_CHECKING:
    from collections.abc import Callable

    from .contracts import (
        AIRequest,
        AIResponse,
        AIStreamEvent,
        ErrorCategory,
        EvaluationReport,
    )

logger = logging.getLogger(__name__)


@dataclass
class TelemetryEvent:
    """观测事件"""

    event_id: str
    event_type: str  # invoke_start, invoke_end, stream_chunk, error, etc.
    timestamp: str
    trace_id: str
    task_type: str | None = None
    role: str | None = None
    provider_id: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    tokens: dict[str, int] | None = None  # prompt, completion, total
    error_category: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "trace_id": self.trace_id,
        }
        if self.task_type:
            result["task_type"] = self.task_type
        if self.role:
            result["role"] = self.role
        if self.provider_id:
            result["provider_id"] = self.provider_id
        if self.model:
            result["model"] = self.model
        if self.latency_ms is not None:
            result["latency_ms"] = self.latency_ms
        if self.tokens:
            result["tokens"] = self.tokens
        if self.error_category:
            result["error_category"] = self.error_category
        if self.error_message:
            result["error_message"] = self.error_message
        if self.metadata:
            result["metadata"] = self.metadata
        return result

    def to_json_line(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False) + "\n"


class TelemetryCollector:
    """观测收集器"""

    def __init__(
        self,
        events_file: Path | None = None,
        enabled: bool = True,
    ) -> None:
        self.enabled = enabled
        self.events_file = events_file
        self._buffer: list[TelemetryEvent] = []
        self._buffer_size = DEFAULT_TELEMETRY_BUFFER_SIZE
        self._listeners: list[Callable[[TelemetryEvent], None]] = []

    def add_listener(self, listener: Callable[[TelemetryEvent], None]) -> None:
        """添加事件监听器"""
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[TelemetryEvent], None]) -> None:
        """移除事件监听器"""
        if listener in self._listeners:
            self._listeners.remove(listener)

    def emit(self, event: TelemetryEvent) -> None:
        """发射事件"""
        if not self.enabled:
            return

        # 通知监听器
        for listener in self._listeners:
            try:
                listener(event)
            except (RuntimeError, ValueError) as e:
                logger.warning("Telemetry listener error: %s", e)

        # 添加到缓冲区
        self._buffer.append(event)

        # 持久化到文件
        if self.events_file:
            self._persist_event(event)

        # 控制缓冲区大小
        if len(self._buffer) > self._buffer_size:
            self._buffer = self._buffer[-self._buffer_size :]

    def _persist_event(self, event: TelemetryEvent) -> None:
        """持久化单个事件"""
        if not self.events_file:
            return

        try:
            self.events_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.events_file, "a", encoding="utf-8") as f:
                f.write(event.to_json_line())
        except (RuntimeError, ValueError) as e:
            logger.warning("Failed to persist telemetry event: %s", e)

    def record_invoke_start(
        self,
        trace_id: str,
        request: AIRequest,
    ) -> TelemetryEvent:
        """记录调用开始"""
        event = TelemetryEvent(
            event_id=self._new_id(),
            event_type="invoke_start",
            timestamp=self._now(),
            trace_id=trace_id,
            task_type=request.task_type.value,
            role=request.role,
            provider_id=request.provider_id,
            model=request.model,
            metadata={
                "input_length": len(request.input),
                "options": {k: v for k, v in request.options.items() if k not in ("api_key", "password")},
            },
        )
        self.emit(event)
        return event

    def record_invoke_end(
        self,
        trace_id: str,
        request: AIRequest,
        response: AIResponse,
        start_time: float,
    ) -> TelemetryEvent:
        """记录调用结束"""
        latency_ms = int((time.time() - start_time) * 1000)

        event = TelemetryEvent(
            event_id=self._new_id(),
            event_type="invoke_end",
            timestamp=self._now(),
            trace_id=trace_id,
            task_type=request.task_type.value,
            role=request.role,
            provider_id=request.provider_id,
            model=request.model,
            latency_ms=latency_ms,
            tokens={
                "prompt": response.usage.prompt_tokens,
                "completion": response.usage.completion_tokens,
                "total": response.usage.total_tokens,
            }
            if response.usage
            else None,
            error_category=response.error_category.value if response.error_category else None,
            error_message=response.error,
            metadata={
                "ok": response.ok,
                "output_length": len(response.output),
                "has_structured": response.structured is not None,
            },
        )
        self.emit(event)
        return event

    def record_stream_chunk(
        self,
        trace_id: str,
        chunk_event: AIStreamEvent,
        chunk_index: int,
    ) -> TelemetryEvent:
        """记录流式块"""
        event = TelemetryEvent(
            event_id=self._new_id(),
            event_type="stream_chunk",
            timestamp=self._now(),
            trace_id=trace_id,
            metadata={
                "chunk_type": chunk_event.type.value,
                "chunk_index": chunk_index,
                "chunk_length": len(chunk_event.chunk or ""),
                "reasoning_length": len(chunk_event.reasoning or ""),
                "done": chunk_event.done,
            },
        )
        self.emit(event)
        return event

    def record_stream_end(
        self,
        trace_id: str,
        total_chunks: int,
        total_chars: int,
        latency_ms: int,
    ) -> TelemetryEvent:
        """记录流式结束"""
        event = TelemetryEvent(
            event_id=self._new_id(),
            event_type="stream_end",
            timestamp=self._now(),
            trace_id=trace_id,
            latency_ms=latency_ms,
            metadata={
                "total_chunks": total_chunks,
                "total_chars": total_chars,
            },
        )
        self.emit(event)
        return event

    def record_evaluation_start(
        self,
        trace_id: str,
        provider_id: str,
        model: str,
        suites: list[str],
    ) -> TelemetryEvent:
        """记录评测开始"""
        event = TelemetryEvent(
            event_id=self._new_id(),
            event_type="evaluation_start",
            timestamp=self._now(),
            trace_id=trace_id,
            provider_id=provider_id,
            model=model,
            metadata={
                "suites": suites,
                "suite_count": len(suites),
            },
        )
        self.emit(event)
        return event

    def record_evaluation_end(
        self,
        trace_id: str,
        report: EvaluationReport,
        start_time: float,
    ) -> TelemetryEvent:
        """记录评测结束"""
        latency_ms = int((time.time() - start_time) * 1000)

        event = TelemetryEvent(
            event_id=self._new_id(),
            event_type="evaluation_end",
            timestamp=self._now(),
            trace_id=trace_id,
            provider_id=report.provider_id,
            model=report.model,
            role=report.role,
            latency_ms=latency_ms,
            metadata={
                "total_cases": report.total_cases,
                "passed_cases": report.passed_cases,
                "failed_cases": report.failed_cases,
                "pass_rate": report.pass_rate,
                "suite_count": len(report.suites),
            },
        )
        self.emit(event)
        return event

    def record_error(
        self,
        trace_id: str,
        error: str,
        category: ErrorCategory,
        context: dict[str, Any] | None = None,
    ) -> TelemetryEvent:
        """记录错误"""
        event = TelemetryEvent(
            event_id=self._new_id(),
            event_type="error",
            timestamp=self._now(),
            trace_id=trace_id,
            error_category=category.value,
            error_message=error,
            metadata=context or {},
        )
        self.emit(event)
        return event

    def get_events(self, trace_id: str | None = None) -> list[TelemetryEvent]:
        """获取事件（可选按 trace_id 过滤）"""
        if trace_id is None:
            return list(self._buffer)
        return [e for e in self._buffer if e.trace_id == trace_id]

    def flush(self) -> None:
        """刷新缓冲区"""
        self._buffer.clear()

    @staticmethod
    def _new_id() -> str:
        return str(uuid.uuid4())[:8]

    @staticmethod
    def _now() -> str:
        return utc_now_iso()


class MetricsAggregator:
    """指标聚合器"""

    def __init__(self, window_size: int = 1000) -> None:
        self.window_size = window_size
        self._latencies: list[int] = []
        self._token_counts: list[int] = []
        self._error_counts: dict[str, int] = {}
        self._total_requests = 0
        self._successful_requests = 0

    def record_request(
        self,
        latency_ms: int,
        tokens: int,
        success: bool,
        error_category: str | None = None,
    ) -> None:
        """记录请求指标"""
        self._total_requests += 1
        if success:
            self._successful_requests += 1

        self._latencies.append(latency_ms)
        self._token_counts.append(tokens)

        if not success and error_category:
            self._error_counts[error_category] = self._error_counts.get(error_category, 0) + 1

        # 保持窗口大小
        if len(self._latencies) > self.window_size:
            self._latencies = self._latencies[-self.window_size :]
        if len(self._token_counts) > self.window_size:
            self._token_counts = self._token_counts[-self.window_size :]

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        if not self._latencies:
            return {
                "total_requests": 0,
                "success_rate": 0.0,
                "avg_latency_ms": 0,
                "p95_latency_ms": 0,
                "p99_latency_ms": 0,
                "avg_tokens": 0,
                "error_breakdown": {},
            }

        sorted_latencies = sorted(self._latencies)
        n = len(sorted_latencies)

        return {
            "total_requests": self._total_requests,
            "success_rate": self._successful_requests / self._total_requests if self._total_requests > 0 else 0.0,
            "avg_latency_ms": sum(self._latencies) // n,
            "p95_latency_ms": sorted_latencies[int(n * 0.95)],
            "p99_latency_ms": sorted_latencies[int(n * 0.99)],
            "avg_tokens": sum(self._token_counts) // len(self._token_counts) if self._token_counts else 0,
            "error_breakdown": dict(self._error_counts),
        }


def create_telemetry_collector(
    workspace: str | None = None,
    events_dir: Path | None = None,
) -> TelemetryCollector:
    """创建观测收集器

    Args:
        workspace: 工作空间路径
        events_dir: 事件目录（可选，默认使用 workspace/<metadata_dir>/telemetry）

    Returns:
        TelemetryCollector 实例
    """
    if events_dir is None and workspace:
        metadata_dir = get_workspace_metadata_dir_name()
        events_dir = Path(workspace) / metadata_dir / "telemetry"

    events_file = None
    if events_dir:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        events_file = events_dir / f"events_{timestamp}.jsonl"

    return TelemetryCollector(events_file=events_file, enabled=True)
