"""Kernel Error Handler - 内核错误处理器

提供事件发射、观察值规范化等错误处理相关功能。
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import TYPE_CHECKING, Any, Literal

from polaris.cells.roles.kernel.internal.events import LLMEventType, emit_llm_event
from polaris.cells.roles.kernel.internal.kernel.helpers import (
    build_stream_event_message,
    make_json_safe,
)

if TYPE_CHECKING:
    from polaris.infrastructure.log_pipeline.writer import LogEventWriter

logger = logging.getLogger(__name__)


class KernelEventEmitter:
    """内核事件发射器

    负责发射 LLM 调用事件、工具执行事件等。
    """

    def resolve_observer_run_id(self, role: str, request_run_id: str | None) -> str:
        """Resolve a stable observability run identifier for one role turn.

        Args:
            role: 角色标识
            request_run_id: 请求中的 run_id

        Returns:
            规范化的 run_id
        """
        requested = str(request_run_id or "").strip()
        if requested:
            return requested
        role_token = str(role or "unknown").strip().lower() or "unknown"
        return f"llm_{role_token}_{uuid.uuid4().hex[:12]}"

    def emit_runtime_llm_event(
        self,
        *,
        event_type: str,
        role: str,
        run_id: str,
        task_id: str | None,
        attempt: int = 0,
        publish_realtime: bool = True,
        workspace: str = "",
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Emit one role-runtime LLM event with consistent workspace metadata.

        Args:
            event_type: 事件类型
            role: 角色标识
            run_id: 运行 ID
            task_id: 任务 ID
            attempt: 尝试次数
            publish_realtime: 是否发布到实时桥
            workspace: 工作区路径
            metadata: 元数据
            **kwargs: 其他事件参数
        """
        safe_run_id = str(run_id or "").strip()
        safe_role = str(role or "unknown").strip().lower() or "unknown"
        safe_task_id = str(task_id or "").strip() or None
        safe_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        safe_metadata.setdefault("workspace", str(workspace or "").strip() or os.getcwd())
        if safe_task_id:
            safe_metadata.setdefault("task_id", safe_task_id)
        # emit_llm_event now handles both realtime bridge and disk persistence
        emit_llm_event(
            event_type=event_type,
            role=safe_role,
            run_id=safe_run_id,
            task_id=safe_task_id,
            attempt=attempt,
            publish_realtime=publish_realtime,
            metadata=safe_metadata,
            **kwargs,
        )

    def emit_stream_log_event(
        self,
        *,
        writer: LogEventWriter | None,
        role: str,
        run_id: str,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Emit a streaming event into canonical llm journal.

        Args:
            writer: 日志事件写入器
            role: 角色标识
            run_id: 运行 ID
            task_id: 任务 ID
            event_type: 事件类型
            payload: 事件数据
        """
        if writer is None or not run_id:
            return

        safe_payload = payload if isinstance(payload, dict) else {}
        message = build_stream_event_message(event_type, safe_payload)
        refs: dict[str, Any] = {}
        if task_id:
            refs["task_id"] = task_id

        kind: Literal["state", "action", "observation", "output", "error"] = "observation"
        if event_type == "tool_call":
            kind = "action"
        elif event_type in {"thinking_chunk", "content_chunk", "tool_result"}:
            kind = "output"
        elif event_type == "error":
            kind = "error"

        raw_payload: dict[str, Any] = {"stream_event": str(event_type or "").strip()}
        raw_payload.update(safe_payload)
        # Ensure raw_payload is JSON-serializable
        raw_payload = make_json_safe(raw_payload)

        try:
            writer.write_event(
                message=message,
                channel="llm",
                domain="llm",
                severity="error" if event_type == "error" else "info",
                kind=kind,
                actor=str(role or "").strip() or "unknown",
                source="role_execution_kernel.stream",
                run_id=run_id,
                refs=refs,
                tags=["stream", str(event_type or "").strip()],
                raw=raw_payload,
            )
        except (RuntimeError, ValueError):
            logger.warning("Failed to write stream event to canonical journal", exc_info=True)


class ObserverValueNormalizer:
    """观察值规范化器

    负责将观察值规范化为可序列化的格式，限制大小。
    """

    @staticmethod
    def normalize(
        value: Any,
        *,
        max_string_length: int = 800,
        max_items: int = 16,
        max_depth: int = 4,
        _depth: int = 0,
    ) -> Any:
        """Bound observer payload size while preserving useful structure.

        Args:
            value: 要规范化的值
            max_string_length: 最大字符串长度
            max_items: 最大列表/字典项数
            max_depth: 最大递归深度
            _depth: 当前递归深度

        Returns:
            规范化后的值
        """
        if _depth >= max_depth:
            return str(value)[:max_string_length]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return value if len(value) <= max_string_length else f"{value[:max_string_length]}..."
        if isinstance(value, dict):
            return ObserverValueNormalizer.normalize_dict(value, max_string_length, max_items, max_depth, _depth)
        if isinstance(value, list):
            return ObserverValueNormalizer.normalize_list(value, max_string_length, max_items, max_depth, _depth)
        if isinstance(value, tuple):
            return ObserverValueNormalizer.normalize(
                list(value),
                max_string_length=max_string_length,
                max_items=max_items,
                max_depth=max_depth,
                _depth=_depth,
            )
        return str(value)[:max_string_length]

    @staticmethod
    def normalize_dict(
        value: dict[str, Any],
        max_string_length: int,
        max_items: int,
        max_depth: int,
        _depth: int,
    ) -> dict[str, Any]:
        """规范化字典值。

        Args:
            value: 字典值
            max_string_length: 最大字符串长度
            max_items: 最大项数
            max_depth: 最大深度
            _depth: 当前深度

        Returns:
            规范化后的字典
        """
        normalized: dict[str, Any] = {}
        for key, item_value in list(value.items())[:max_items]:
            normalized[str(key)] = ObserverValueNormalizer.normalize(
                item_value,
                max_string_length=max_string_length,
                max_items=max_items,
                max_depth=max_depth,
                _depth=_depth + 1,
            )
        if len(value) > max_items:
            normalized["_truncated_keys"] = len(value) - max_items
        return normalized

    @staticmethod
    def normalize_list(
        value: list[Any],
        max_string_length: int,
        max_items: int,
        max_depth: int,
        _depth: int,
    ) -> list[Any]:
        """规范化列表值。

        Args:
            value: 列表值
            max_string_length: 最大字符串长度
            max_items: 最大项数
            max_depth: 最大深度
            _depth: 当前深度

        Returns:
            规范化后的列表
        """
        normalized_list = [
            ObserverValueNormalizer.normalize(
                item,
                max_string_length=max_string_length,
                max_items=max_items,
                max_depth=max_depth,
                _depth=_depth + 1,
            )
            for item in value[:max_items]
        ]
        if len(value) > max_items:
            normalized_list.append({"_truncated_items": len(value) - max_items})
        return normalized_list


# 全局实例
_global_event_emitter: KernelEventEmitter | None = None
_global_normalizer: ObserverValueNormalizer | None = None


def get_event_emitter() -> KernelEventEmitter:
    """获取全局事件发射器实例"""
    global _global_event_emitter
    if _global_event_emitter is None:
        _global_event_emitter = KernelEventEmitter()
    return _global_event_emitter


def get_normalizer() -> ObserverValueNormalizer:
    """获取全局观察值规范化器实例"""
    global _global_normalizer
    if _global_normalizer is None:
        _global_normalizer = ObserverValueNormalizer()
    return _global_normalizer


def normalize_observer_value(
    value: Any,
    *,
    max_string_length: int = 800,
    max_items: int = 16,
    max_depth: int = 4,
) -> Any:
    """规范化观察值（便捷函数）

    Args:
        value: 要规范化的值
        max_string_length: 最大字符串长度
        max_items: 最大项数
        max_depth: 最大深度

    Returns:
        规范化后的值
    """
    return get_normalizer().normalize(
        value,
        max_string_length=max_string_length,
        max_items=max_items,
        max_depth=max_depth,
    )


__all__ = [
    "KernelEventEmitter",
    "LLMEventType",
    "ObserverValueNormalizer",
    "get_event_emitter",
    "get_normalizer",
    "normalize_observer_value",
]
