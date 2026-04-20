from __future__ import annotations

import json
import logging
from typing import Any, Literal

from polaris.infrastructure.log_pipeline.writer import get_writer
from polaris.kernelone.events.constants import (
    EVENT_TYPE_CONTENT_CHUNK,
    EVENT_TYPE_LLM_CALL_END,
    EVENT_TYPE_LLM_CALL_START,
    EVENT_TYPE_LLM_COMPLETED,
    EVENT_TYPE_LLM_ERROR,
    EVENT_TYPE_LLM_FAILED,
    EVENT_TYPE_LLM_RETRY,
    EVENT_TYPE_LLM_WAITING,
    EVENT_TYPE_THINKING_CHUNK,
    EVENT_TYPE_TOOL_CALL,
    EVENT_TYPE_TOOL_RESULT,
)
from polaris.kernelone.events.realtime_bridge import (
    LLMRealtimeEvent,
    LLMRealtimeEventBridge,
)

logger = logging.getLogger(__name__)

_WAITING_EVENT_TYPES = {
    "call_start",
    EVENT_TYPE_LLM_CALL_START,
    "call_retry",
    EVENT_TYPE_LLM_RETRY,
    "llm_waiting",
}
_COMPLETED_EVENT_TYPES = {
    "call_end",
    EVENT_TYPE_LLM_CALL_END,
    "validation_pass",
    "llm_completed",
}
_FAILED_EVENT_TYPES = {
    "call_error",
    EVENT_TYPE_LLM_ERROR,
    "validation_fail",
    "llm_failed",
    "invoke_error",
}
_TOOL_CALL_EVENT_TYPES = {
    "tool_execute",
    EVENT_TYPE_TOOL_CALL,
}
_TOOL_RESULT_EVENT_TYPES = {
    EVENT_TYPE_TOOL_RESULT,
}
_THINKING_EVENT_TYPES = {
    EVENT_TYPE_THINKING_CHUNK,
    "thinking_preview",
}
_CONTENT_EVENT_TYPES = {
    EVENT_TYPE_CONTENT_CHUNK,
    "content_preview",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().lower()


def _coalesce_text(*values: Any) -> str:
    for value in values:
        token = str(value or "").strip()
        if token:
            return token
    return ""


def _safe_json_compact(value: Any, *, max_chars: int = 160) -> str:
    if value is None:
        return ""
    try:
        compact = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (RuntimeError, ValueError):
        compact = str(value)
    compact = compact.strip()
    if len(compact) > max_chars:
        return f"{compact[:max_chars]}..."
    return compact


def _metadata_maps(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = _as_dict(data.get("metadata"))
    extra_fields = _as_dict(metadata.get("extra_fields"))
    return metadata, extra_fields


def _extract_tool_name(data: dict[str, Any]) -> str:
    metadata, extra_fields = _metadata_maps(data)
    return _coalesce_text(
        data.get("tool_name"),
        data.get("tool"),
        metadata.get("tool_name"),
        metadata.get("tool"),
        extra_fields.get("tool_name"),
        extra_fields.get("tool"),
    )


def _extract_tool_args(data: dict[str, Any]) -> dict[str, Any]:
    metadata, extra_fields = _metadata_maps(data)
    for candidate in (
        data.get("args"),
        metadata.get("args"),
        extra_fields.get("args"),
    ):
        if isinstance(candidate, dict):
            return candidate
    return {}


def _extract_result_payload(data: dict[str, Any]) -> dict[str, Any]:
    metadata, extra_fields = _metadata_maps(data)
    for candidate in (
        data.get("result"),
        metadata.get("result"),
        extra_fields.get("result"),
    ):
        if isinstance(candidate, dict):
            return candidate
    return {}


def _extract_success(data: dict[str, Any]) -> bool | None:
    metadata, extra_fields = _metadata_maps(data)
    result_payload = _extract_result_payload(data)
    for candidate in (
        data.get("success"),
        metadata.get("success"),
        extra_fields.get("success"),
        result_payload.get("success"),
    ):
        if isinstance(candidate, bool):
            return candidate
    return None


def _extract_error_text(data: dict[str, Any]) -> str:
    metadata, extra_fields = _metadata_maps(data)
    result_payload = _extract_result_payload(data)
    return _coalesce_text(
        data.get("error_message"),
        data.get("error"),
        metadata.get("error"),
        extra_fields.get("error"),
        result_payload.get("error"),
        result_payload.get("message"),
    )


def _extract_content_text(data: dict[str, Any]) -> str:
    metadata, extra_fields = _metadata_maps(data)
    return _coalesce_text(
        data.get("content"),
        data.get("preview"),
        data.get("message"),
        data.get("summary"),
        metadata.get("content"),
        metadata.get("preview"),
        metadata.get("message"),
        metadata.get("summary"),
        extra_fields.get("content"),
        extra_fields.get("preview"),
        extra_fields.get("message"),
        extra_fields.get("summary"),
    )


def _extract_iteration(event: LLMRealtimeEvent) -> int:
    data = _as_dict(event.data)
    metadata, extra_fields = _metadata_maps(data)
    for candidate in (
        event.iteration,
        data.get("iteration"),
        metadata.get("iteration"),
        extra_fields.get("iteration"),
        data.get("attempt"),
    ):
        if candidate is None:
            continue
        try:
            return max(0, int(candidate))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return 0


def _extract_task_id(data: dict[str, Any]) -> str:
    metadata, extra_fields = _metadata_maps(data)
    return _coalesce_text(
        data.get("task_id"),
        metadata.get("task_id"),
        extra_fields.get("task_id"),
    )


def _build_projection_event_type(raw_event_type: str, data: dict[str, Any]) -> str:
    """Build projection event type from raw event type and data.

    Uses constants from polaris.kernelone.events.constants for canonical
    event type strings.

    Args:
        raw_event_type: Raw event type string from the source.
        data: Event data dictionary.

    Returns:
        Canonical event type string from constants.py.
    """
    normalized = _normalize_token(raw_event_type)
    stage = _normalize_token(data.get("stage"))
    if normalized in _WAITING_EVENT_TYPES:
        return EVENT_TYPE_LLM_WAITING
    if normalized in _COMPLETED_EVENT_TYPES:
        return EVENT_TYPE_LLM_COMPLETED
    if normalized in _FAILED_EVENT_TYPES:
        return EVENT_TYPE_LLM_FAILED
    if normalized in _TOOL_CALL_EVENT_TYPES:
        return EVENT_TYPE_TOOL_CALL
    if normalized in _TOOL_RESULT_EVENT_TYPES:
        return EVENT_TYPE_TOOL_RESULT
    if normalized == "thinking_preview":
        return "thinking_preview"
    if normalized == "thinking_chunk":
        return EVENT_TYPE_THINKING_CHUNK
    if normalized == "content_preview":
        return "content_preview"
    if normalized == "content_chunk":
        return EVENT_TYPE_CONTENT_CHUNK
    if normalized == "iteration" and stage in {"started", "retrying", "waiting"}:
        return EVENT_TYPE_LLM_WAITING
    if normalized == "iteration" and stage in {"completed", "success"}:
        return EVENT_TYPE_LLM_COMPLETED
    if normalized == "iteration" and stage in {"failed", "error"}:
        return EVENT_TYPE_LLM_FAILED
    return normalized or "llm_event"


def _build_event_kind(projection_event_type: str) -> Literal["state", "action", "observation", "output", "error"]:
    if projection_event_type in {"llm_waiting", "llm_completed"}:
        return "state"
    if projection_event_type == EVENT_TYPE_TOOL_CALL:
        return "action"
    if projection_event_type in {
        EVENT_TYPE_TOOL_RESULT,
        EVENT_TYPE_THINKING_CHUNK,
        "thinking_preview",
        EVENT_TYPE_CONTENT_CHUNK,
        "content_preview",
    }:
        return "output"
    if projection_event_type in {"llm_failed", "error"}:
        return "error"
    return "observation"


def _build_severity(projection_event_type: str) -> Literal["debug", "info", "warn", "error", "critical"]:
    if projection_event_type in {"llm_failed", "error"}:
        return "error"
    return "info"


def _build_message(
    *,
    projection_event_type: str,
    raw_event_type: str,
    role: str,
    data: dict[str, Any],
) -> str:
    model = _coalesce_text(data.get("model"))
    provider = _coalesce_text(data.get("provider"))
    tool_name = _extract_tool_name(data)
    tool_args = _extract_tool_args(data)
    error_text = _extract_error_text(data)
    result_payload = _extract_result_payload(data)

    if projection_event_type == "llm_waiting":
        parts = ["waiting for LLM response"]
        if model:
            parts.append(f"model={model}")
        if provider:
            parts.append(f"provider={provider}")
        return " | ".join(parts)

    if projection_event_type == "llm_completed":
        completion_tokens = data.get("completion_tokens")
        parts = ["llm response completed"]
        if completion_tokens is not None:
            parts.append(f"completion_tokens={completion_tokens}")
        return " | ".join(parts)

    if projection_event_type == "llm_failed":
        return error_text or f"{role} llm failed"

    if projection_event_type == EVENT_TYPE_TOOL_CALL:
        if tool_name and tool_args:
            return f"{EVENT_TYPE_TOOL_CALL}:{tool_name} args={_safe_json_compact(tool_args)}"
        if tool_name:
            return f"{EVENT_TYPE_TOOL_CALL}:{tool_name}"
        return EVENT_TYPE_TOOL_CALL

    if projection_event_type == EVENT_TYPE_TOOL_RESULT:
        success = _extract_success(data)
        status = "ok" if success is True else ("failed" if success is False else "unknown")
        if tool_name and error_text:
            return f"{EVENT_TYPE_TOOL_RESULT}:{tool_name}:{status} error={error_text}"
        if tool_name and result_payload:
            return f"{EVENT_TYPE_TOOL_RESULT}:{tool_name}:{status} result={_safe_json_compact(result_payload)}"
        if tool_name:
            return f"{EVENT_TYPE_TOOL_RESULT}:{tool_name}:{status}"
        return f"{EVENT_TYPE_TOOL_RESULT}:{status}"

    if projection_event_type in {
        EVENT_TYPE_THINKING_CHUNK,
        "thinking_preview",
        EVENT_TYPE_CONTENT_CHUNK,
        "content_preview",
    }:
        return _extract_content_text(data) or raw_event_type

    return _coalesce_text(
        data.get("message"),
        data.get("summary"),
        data.get("stage"),
        raw_event_type,
        "llm_event",
    )


def _build_refs(event: LLMRealtimeEvent) -> dict[str, Any]:
    data = _as_dict(event.data)
    refs: dict[str, Any] = {}
    task_id = _extract_task_id(data)
    if task_id:
        refs["task_id"] = task_id
    iteration = _extract_iteration(event)
    if iteration > 0:
        refs["iteration"] = iteration
    return refs


def _build_tags(raw_event_type: str, projection_event_type: str) -> list[str]:
    tags = [
        "llm_realtime_bridge",
        f"llm_event:{_normalize_token(raw_event_type) or 'unknown'}",
        f"projection_event:{projection_event_type}",
    ]
    return tags


class LogPipelineLLMRealtimeBridge(LLMRealtimeEventBridge):
    """Publish LLM lifecycle events through the canonical log pipeline."""

    def publish(self, event: LLMRealtimeEvent) -> None:
        workspace = _coalesce_text(event.workspace)
        run_id = _coalesce_text(event.run_id)
        role = _coalesce_text(event.role, "unknown").lower()
        raw_event_type = _coalesce_text(event.event_type)
        if not workspace or not run_id or not raw_event_type:
            return

        data = _as_dict(event.data)
        projection_event_type = _build_projection_event_type(raw_event_type, data)
        try:
            writer = get_writer(workspace=workspace, run_id=run_id)
            writer.write_event(
                message=_build_message(
                    projection_event_type=projection_event_type,
                    raw_event_type=raw_event_type,
                    role=role,
                    data=data,
                ),
                channel="llm",
                domain="llm",
                severity=_build_severity(projection_event_type),
                kind=_build_event_kind(projection_event_type),
                actor=role,
                source=_coalesce_text(event.source, "llm_realtime_bridge"),
                run_id=run_id,
                refs=_build_refs(event),
                tags=_build_tags(raw_event_type, projection_event_type),
                raw={
                    "stream_event": projection_event_type,
                    "event_type": raw_event_type,
                    "role": role,
                    "run_id": run_id,
                    "timestamp": _coalesce_text(event.timestamp),
                    "iteration": _extract_iteration(event),
                    "source": _coalesce_text(event.source, "llm_realtime_bridge"),
                    "data": data,
                },
                error=_extract_error_text(data) or None,
            )
        except (RuntimeError, ValueError):
            logger.debug(
                "Failed to publish LLM realtime event via log pipeline",
                exc_info=True,
            )


__all__ = ["LogPipelineLLMRealtimeBridge"]
