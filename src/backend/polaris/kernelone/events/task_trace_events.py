"""统一任务追踪事件发射

提供统一的 task trace 事件发射接口，供 Cells 和 KernelOne 复用。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from polaris.kernelone.events import MessageType


def _sanitize_step_detail(detail: str, max_length: int = 280) -> str:
    """清理步骤详情，处理 UTF-8 并截断.

    This is inlined from cells/orchestration/workflow_runtime/internal/task_trace.py
    to avoid cross-Cell internal imports in KernelOne.
    """
    if not detail:
        return ""
    # Ensure UTF-8 encoding safety
    try:
        detail = detail.encode("utf-8", errors="ignore").decode("utf-8")
    except (UnicodeError, AttributeError):
        detail = str(detail)
    # Mask sensitive information (API keys, tokens)
    detail = re.sub(r"[a-zA-Z0-9]{32,}", "[MASKED]", detail)
    detail = re.sub(r"sk-[a-zA-Z0-9]{20,}", "[MASKED]", detail)
    # Truncate overly long content
    if len(detail) > max_length:
        detail = detail[: max_length - 3] + "..."
    return detail


async def emit_task_trace_event(
    workspace: str,
    task_id: str,
    trace_type: str,
    payload: dict[str, Any],
) -> None:
    """发射任务追踪 event

    Args:
        workspace: 工作空间路径
        task_id: 任务ID
        trace_type: 追踪类型 (start, step, complete, error)
        payload: 事件负载，包含 phase, step_kind, step_title, step_detail 等
    """
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        return

    bus = await _resolve_message_bus()
    if bus is None:
        return

    try:
        role = str(payload.get("role", "adapter") or "adapter").strip().lower()
        run_id = str(payload.get("run_id", "") or "").strip()
        phase = str(payload.get("phase", "executing") or "").strip() or "executing"
        step_kind = str(payload.get("step_kind", "system") or "").strip() or "system"
        step_title = str(payload.get("step_title", "adapter_event") or "").strip()[:120] or "adapter_event"
        step_detail = str(payload.get("step_detail", "") or "").strip()
        status = str(payload.get("status", "running") or "").strip().lower() or "running"
        attempt = max(0, int(payload.get("attempt", 0) or 0))
        visibility = str(payload.get("visibility", "debug") or "").strip() or "debug"

        refs_payload: dict[str, Any] = dict(payload.get("refs") or {})
        if payload.get("current_file"):
            refs_payload["current_file"] = str(payload["current_file"]).strip()
        if payload.get("code"):
            refs_payload["code"] = str(payload["code"]).strip()
        if payload.get("reason"):
            refs_payload["reason"] = str(payload["reason"]).strip()

        seq = _next_seq_for_task(normalized_task_id)
        event_payload = {
            "type": "task_trace",
            "event": {
                "event_id": str(uuid.uuid4()),
                "run_id": run_id,
                "role": role,
                "task_id": normalized_task_id,
                "seq": seq,
                "phase": phase,
                "step_kind": step_kind,
                "step_title": step_title,
                "step_detail": _sanitize_step_detail(step_detail),
                "status": status,
                "attempt": attempt,
                "visibility": visibility,
                "ts": datetime.now(timezone.utc).isoformat(),
                "refs": refs_payload,
            },
        }
        await bus.broadcast(
            MessageType.TASK_TRACE,
            f"{role}_adapter",
            event_payload,
        )
    except (RuntimeError, ValueError):
        # 事件发送失败不应中断主流程
        return


# Task trace sequence counter per task_id
_task_trace_seq: dict[str, int] = {}


def _next_seq_for_task(task_id: str) -> int:
    """获取下一个 task trace 序列号"""
    token = str(task_id or "").strip() or "unknown"
    current = int(_task_trace_seq.get(token, 0))
    next_seq = current + 1
    _task_trace_seq[token] = next_seq
    return next_seq


async def _resolve_message_bus():  # type: ignore[no-untyped-def]
    """解析 message bus.

    Uses TYPE_CHECKING pattern to avoid runtime import of DirectorService.
    The bus is obtained via container resolution using the type hint for type safety.
    """
    try:
        from polaris.infrastructure.di.container import get_container

        container = await get_container()
        # Use type hint with TYPE_CHECKING guard to get the service without runtime import
        director_service = await container.resolve_async("DirectorService")  # type: ignore[arg-type]
        bus = getattr(director_service, "_bus", None)
        return bus
    except (RuntimeError, ValueError):
        return None
