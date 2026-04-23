"""LLM Call Events - LLM 调用事件系统

提供统一的 LLM 调用事件，用于可观测性和调试。
"""

from __future__ import annotations

import logging
import os
import threading
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any

from polaris.kernelone.events.constants import (
    EVENT_TYPE_LLM_CALL_END,
    EVENT_TYPE_LLM_CALL_START,
    EVENT_TYPE_LLM_ERROR,
    EVENT_TYPE_LLM_RETRY,
    EVENT_TYPE_TOOL_CALL,
    EVENT_TYPE_TOOL_RESULT,
)
from polaris.kernelone.events.realtime_bridge import (
    LLMRealtimeEvent,
    publish_llm_realtime_event,
)

logger = logging.getLogger(__name__)


# 事件类型
class LLMEventType:
    """LLM 事件类型常量（使用 kernelone/events/constants.py 中的权威常量）。"""

    CALL_START = EVENT_TYPE_LLM_CALL_START
    CALL_END = EVENT_TYPE_LLM_CALL_END
    CALL_RETRY = EVENT_TYPE_LLM_RETRY
    CALL_ERROR = EVENT_TYPE_LLM_ERROR
    TOOL_EXECUTE = EVENT_TYPE_TOOL_CALL
    TOOL_RESULT = EVENT_TYPE_TOOL_RESULT
    VALIDATION_PASS = "validation_pass"
    VALIDATION_FAIL = "validation_fail"
    THINKING_PREVIEW = "thinking_preview"
    CONTENT_PREVIEW = "content_preview"


@dataclass
class LLMCallEvent:
    """LLM 调用事件

    记录 LLM 调用过程中的关键事件。
    """

    # 事件类型
    event_type: str

    # 上下文信息
    role: str
    run_id: str
    task_id: str | None = None
    attempt: int = 0

    # 时间戳
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    # LLM 信息
    model: str | None = None
    provider: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    # 错误信息
    error_category: str | None = None
    error_message: str | None = None

    # 重试信息
    retry_decision: str | None = None
    backoff_seconds: float | None = None

    # 上下文统计
    context_tokens_before: int | None = None
    context_tokens_after: int | None = None
    compression_strategy: str | None = None

    # 质量信息
    quality_score: float | None = None
    errors: list[str] | None = None

    # 工具信息
    tool_calls_count: int = 0
    tool_errors_count: int = 0

    # 元数据
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "event_type": self.event_type,
            "role": self.role,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "attempt": self.attempt,
            "timestamp": self.timestamp,
            "model": self.model,
            "provider": self.provider,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "error_category": self.error_category,
            "error_message": self.error_message,
            "retry_decision": self.retry_decision,
            "backoff_seconds": self.backoff_seconds,
            "context_tokens_before": self.context_tokens_before,
            "context_tokens_after": self.context_tokens_after,
            "compression_strategy": self.compression_strategy,
            "quality_score": self.quality_score,
            "errors": self.errors,
            "tool_calls_count": self.tool_calls_count,
            "tool_errors_count": self.tool_errors_count,
            "metadata": self.metadata,
        }


class LLMEventEmitter:
    """LLM 事件发射器

    负责发射 LLM 调用事件到注册的监听器。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._listeners: list[Callable[[LLMCallEvent], None]] = []
        self._event_history: list[LLMCallEvent] = []
        self._max_history_size = 1000
        self._open_lifecycle: dict[str, dict[str, Any]] = {}
        self._max_lifecycle_age_seconds = max(
            10.0,
            float(os.environ.get("KERNELONE_LLM_LIFECYCLE_MAX_AGE_SECONDS", "300")),
        )
        self._closed_without_start_count = 0
        self._reopened_without_close_count = 0

    def add_listener(self, listener: Callable[[LLMCallEvent], None]) -> None:
        """添加事件监听器

        Args:
            listener: 事件处理函数
        """
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[LLMCallEvent], None]) -> None:
        """移除事件监听器

        Args:
            listener: 事件处理函数
        """
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def emit(self, event: LLMCallEvent) -> None:
        """发射事件

        Args:
            event: LLM 调用事件
        """
        # 添加到历史记录（线程安全）
        with self._lock:
            self._track_lifecycle(event)
            self._event_history.append(event)
            if len(self._event_history) > self._max_history_size:
                self._event_history = self._event_history[-self._max_history_size :]
            listeners = self._listeners[:]

        # 通知所有监听器（遍历副本，防止修改时出错）
        for listener in listeners:
            try:
                listener(event)
            except (RuntimeError, ValueError) as e:
                logger.warning("事件监听器执行失败: %s", e)

    def get_events(
        self,
        run_id: str | None = None,
        task_id: str | None = None,
        role: str | None = None,
        limit: int = 100,
    ) -> list[LLMCallEvent]:
        """获取事件历史

        Args:
            run_id: 按 run_id 过滤
            task_id: 按 task_id 过滤
            role: 按 role 过滤
            limit: 返回数量限制

        Returns:
            事件列表
        """
        events = self._event_history

        if run_id:
            events = [e for e in events if e.run_id == run_id]
        if task_id:
            events = [e for e in events if e.task_id == task_id]
        if role:
            events = [e for e in events if e.role == role]

        return events[-limit:]

    def clear_history(self) -> None:
        """清空事件历史"""
        with self._lock:
            self._event_history.clear()
            self._open_lifecycle.clear()
            self._closed_without_start_count = 0
            self._reopened_without_close_count = 0

    def _track_lifecycle(self, event: LLMCallEvent) -> None:
        run_id = str(event.run_id or "").strip()
        if not run_id:
            return

        now = time.time()
        for open_run_id, state in list(self._open_lifecycle.items()):
            started_at = float(state.get("started_at", 0.0) or 0.0)
            if not started_at:
                continue
            age_seconds = now - started_at
            if age_seconds > self._max_lifecycle_age_seconds and not bool(state.get("warned")):
                state["warned"] = True
                logger.warning(
                    "LLM lifecycle appears unclosed (run_id=%s, age=%.2fs, role=%s, model=%s)",
                    open_run_id,
                    age_seconds,
                    state.get("role", "unknown"),
                    state.get("model", "unknown"),
                )

        if event.event_type == LLMEventType.CALL_START:
            previous = self._open_lifecycle.get(run_id)
            if previous:
                self._reopened_without_close_count += 1
                metadata = event.metadata if isinstance(event.metadata, dict) else {}
                metadata.setdefault(
                    "lifecycle_warning",
                    "call_start_reopened_without_previous_close",
                )
                event.metadata = metadata
                logger.warning(
                    "LLM lifecycle reopened without close (run_id=%s, previous_role=%s, previous_model=%s)",
                    run_id,
                    previous.get("role", "unknown"),
                    previous.get("model", "unknown"),
                )
            self._open_lifecycle[run_id] = {
                "started_at": now,
                "event_timestamp": event.timestamp,
                "role": event.role,
                "model": event.model,
                "task_id": event.task_id,
                "attempt": event.attempt,
                "warned": False,
            }
            return

        if event.event_type in {LLMEventType.CALL_END, LLMEventType.CALL_ERROR}:
            opened = self._open_lifecycle.pop(run_id, None)
            if opened is None:
                self._closed_without_start_count += 1
                # Check if this is a cancellation error - these can happen before
                # CALL_START is emitted (e.g., context cancellation before invoke).
                # Don't warn for cancellation since no call was made.
                metadata = event.metadata if isinstance(event.metadata, dict) else {}
                error_category = str(metadata.get("error_category", "") or "").strip().lower()
                is_cancellation = error_category in (
                    "cancelled",
                    "cancel",
                    "canceled",
                    "stream_cancelled",
                    "cancellation",
                )
                if not is_cancellation:
                    metadata.setdefault("lifecycle_warning", "call_close_without_start")
                    event.metadata = metadata
                    logger.warning(
                        "LLM lifecycle close without prior start (run_id=%s, event_type=%s, error_category=%s)",
                        run_id,
                        event.event_type,
                        error_category,
                    )

    def get_unclosed_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            result: list[dict[str, Any]] = []
            now = time.time()
            for run_id, state in self._open_lifecycle.items():
                snapshot = {"run_id": run_id, **state}
                started_at = float(state.get("started_at", 0.0) or 0.0)
                snapshot["age_seconds"] = max(0.0, now - started_at) if started_at else 0.0
                result.append(snapshot)
            return result

    def get_lifecycle_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "open_runs_count": len(self._open_lifecycle),
                "closed_without_start_count": self._closed_without_start_count,
                "reopened_without_close_count": self._reopened_without_close_count,
                "max_lifecycle_age_seconds": self._max_lifecycle_age_seconds,
            }


# 全局事件发射器实例
_global_emitter: LLMEventEmitter | None = None
_LLM_EVENT_FIELD_NAMES = {item.name for item in fields(LLMCallEvent)}


def get_global_emitter() -> LLMEventEmitter:
    """获取全局事件发射器"""
    global _global_emitter
    if _global_emitter is None:
        _global_emitter = LLMEventEmitter()
    return _global_emitter


def get_lifecycle_snapshot() -> dict[str, Any]:
    """Return current lifecycle open-runs and anomaly counters."""
    emitter = get_global_emitter()
    return {
        "stats": emitter.get_lifecycle_stats(),
        "unclosed_runs": emitter.get_unclosed_runs(),
    }


def emit_llm_event(
    event_type: str,
    role: str,
    run_id: str,
    publish_realtime: bool = True,
    **kwargs,
) -> None:
    """便捷的事件发射函数

    .. deprecated::
        Use UEPEventPublisher (polaris.kernelone.events.uep_publisher)
        for new code. This function is maintained for backward compatibility.

    Args:
        event_type: 事件类型
        role: 角色
        run_id: 运行 ID
        **kwargs: 其他字段
    """
    known_kwargs: dict[str, Any] = {}
    unknown_kwargs: dict[str, Any] = {}
    for key, value in kwargs.items():
        token = str(key or "").strip()
        if token in _LLM_EVENT_FIELD_NAMES:
            known_kwargs[token] = value
        else:
            unknown_kwargs[token] = value

    metadata = known_kwargs.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    if unknown_kwargs:
        extra_fields = metadata.get("extra_fields")
        merged_extra = dict(extra_fields) if isinstance(extra_fields, dict) else {}
        merged_extra.update(unknown_kwargs)
        metadata["extra_fields"] = merged_extra
        logger.debug(
            "emit_llm_event dropped unsupported kwargs for %s: %s",
            event_type,
            sorted(unknown_kwargs.keys()),
        )
    known_kwargs["metadata"] = metadata

    warnings.warn(
        "emit_llm_event is deprecated. Use UEPEventPublisher for new code.",
        DeprecationWarning,
        stacklevel=2,
    )

    event = LLMCallEvent(
        event_type=event_type,
        role=role,
        run_id=run_id,
        **known_kwargs,
    )
    get_global_emitter().emit(event)
    if publish_realtime:
        _publish_to_realtime_bridge(event)

    # Always write to disk for audit trail
    _emit_llm_event_to_disk(event)


def _emit_llm_event_to_disk(event: LLMCallEvent) -> None:
    """Write LLM event to disk JSONL file for audit trail.

    Path: {runtime_root}/events/{role}.llm.events.jsonl
    Uses workspace metadata directory for path resolution.
    """
    run_id = str(event.run_id or "").strip()
    if not run_id:
        return

    try:
        workspace = _resolve_runtime_workspace(event)
        safe_role = str(event.role or "unknown").strip().lower() or "unknown"
        iteration = _resolve_iteration(event)
        data = event.to_dict()

        # Use Polaris-specific path resolution
        from polaris.cells.storage.layout import resolve_polaris_roots

        roots = resolve_polaris_roots(workspace)
        runtime_root = roots.runtime_root

        # Build path: {runtime_root}/events/{role}.llm.events.jsonl
        import json

        events_dir = os.path.join(runtime_root, "events")
        os.makedirs(events_dir, exist_ok=True)
        llm_events_path = os.path.join(events_dir, f"{safe_role}.llm.events.jsonl")

        # Write event directly
        import time
        import uuid

        payload = {
            "schema_version": 1,
            "ts": datetime.now().isoformat(),
            "ts_epoch": time.time(),
            "seq": int(time.time() * 1000) % 1000000,
            "event_id": str(uuid.uuid4())[:8],
            "run_id": run_id,
            "iteration": iteration,
            "role": safe_role,
            "source": "roles.kernel.events",
            "event": str(event.event_type or "").strip(),
            "data": data,
        }

        with open(llm_events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

        logger.debug(
            "LLM event written to disk: path=%s, event=%s, run_id=%s",
            llm_events_path,
            event.event_type,
            run_id,
        )
    except (RuntimeError, ValueError):
        # Audit emission must never break the main flow, but failures must be
        # observable (not silently swallowed).
        logger.error("Failed to emit LLM audit event to disk", exc_info=True)


def _resolve_runtime_workspace(event: LLMCallEvent) -> str:
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    extra_fields = metadata.get("extra_fields")
    if not isinstance(extra_fields, dict):
        extra_fields = {}
    for candidate in (
        metadata.get("workspace"),
        extra_fields.get("workspace"),
        os.environ.get("KERNELONE_WORKSPACE"),
        os.getcwd(),
    ):
        token = str(candidate or "").strip()
        if token:
            return os.path.abspath(token)
    return os.path.abspath(os.getcwd())


def _resolve_iteration(event: LLMCallEvent) -> int:
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    extra_fields = metadata.get("extra_fields")
    if not isinstance(extra_fields, dict):
        extra_fields = {}
    for candidate in (
        metadata.get("iteration"),
        extra_fields.get("iteration"),
        event.attempt,
    ):
        try:
            return max(0, int(candidate))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return 0


def _publish_to_realtime_bridge(event: LLMCallEvent) -> None:
    run_id = str(event.run_id or "").strip()
    if not run_id:
        return
    try:
        publish_llm_realtime_event(
            LLMRealtimeEvent(
                workspace=_resolve_runtime_workspace(event),
                run_id=run_id,
                role=str(event.role or "unknown").strip().lower() or "unknown",
                event_type=str(event.event_type or "").strip(),
                source="application.roles.events",
                timestamp=str(event.timestamp or ""),
                iteration=_resolve_iteration(event),
                data=event.to_dict(),
            )
        )
    except (RuntimeError, ValueError):
        logger.warning("Failed to bridge LLM event to realtime pipeline", exc_info=True)
