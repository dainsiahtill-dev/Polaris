"""LLM Stream Handler Module.

Provides stream processing, chunk normalization, and SLO metrics.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .response_types import NormalizedStreamEvent

logger = logging.getLogger(__name__)

# Event type aliases for normalization
EVENT_TYPE_ALIASES = {
    "reasoning": "reasoning_chunk",
    "thinking": "reasoning_chunk",
    "thinking_chunk": "reasoning_chunk",
    "content_chunk": "chunk",
    "token": "chunk",
    "text": "chunk",
    "delta": "chunk",
    "content_delta": "chunk",
    "tool_use": "tool_call",
    "tool_calls": "tool_call",
    "tool_call_delta": "tool_call",
    "stream_error": "error",
    "done": "complete",
    "completion": "complete",
    "message_stop": "complete",
    "content_block_stop": "complete",
}


def normalize_stream_chunk(
    chunk: Any,
    *,
    native_tool_mode: str,
    tool_protocol: str,
) -> NormalizedStreamEvent:
    """Normalize heterogeneous streaming chunk to canonical event.

    Args:
        chunk: Raw provider chunk (dict or object)
        native_tool_mode: Native tool calling mode
        tool_protocol: Tool protocol identifier

    Returns:
        NormalizedStreamEvent with standardized fields
    """
    event_type = ""
    content = ""
    reasoning = ""
    error_message = ""
    metadata: dict[str, Any] = {}
    tool_call_payload: dict[str, Any] = {}
    tool_result_payload: dict[str, Any] = {}
    done = False

    # Extract from dict-based chunk
    if isinstance(chunk, dict):
        raw_event_type = chunk.get("event_type")
        if raw_event_type in (None, ""):
            raw_event_type = chunk.get("type")
        if raw_event_type in (None, ""):
            raw_event_type = chunk.get("kind")
        event_type = str(getattr(raw_event_type, "value", raw_event_type) or "").strip().lower()

        raw_meta = chunk.get("metadata")
        if isinstance(raw_meta, dict):
            metadata = dict(raw_meta)

        content = str(chunk.get("content") or chunk.get("chunk") or chunk.get("text") or chunk.get("delta") or "")
        reasoning = str(chunk.get("reasoning") or "")
        error_message = str(chunk.get("error") or chunk.get("message") or "").strip()

        raw_tool_call = chunk.get("tool_call")
        if isinstance(raw_tool_call, dict):
            tool_call_payload = dict(raw_tool_call)

        raw_tool_result = chunk.get("tool_result")
        if isinstance(raw_tool_result, dict):
            tool_result_payload = dict(raw_tool_result)

        done = bool(chunk.get("done", False))

        for key in ("provider_id", "provider", "model"):
            value = chunk.get(key)
            if value not in (None, "") and key not in metadata:
                metadata[key] = value

    # Extract from object-based chunk
    else:
        raw_event_type = getattr(chunk, "event_type", None)
        if raw_event_type in (None, ""):
            raw_event_type = getattr(chunk, "type", "")
        event_type = str(getattr(raw_event_type, "value", raw_event_type) or "").strip().lower()

        raw_meta = getattr(chunk, "metadata", None)
        if not isinstance(raw_meta, dict):
            raw_meta = getattr(chunk, "meta", None)
        if isinstance(raw_meta, dict):
            metadata = dict(raw_meta)

        content = str(
            getattr(chunk, "content", None) or getattr(chunk, "chunk", None) or getattr(chunk, "text", None) or ""
        )
        reasoning = str(getattr(chunk, "reasoning", "") or "")
        error_message = str(getattr(chunk, "error", "") or "").strip()

        raw_tool_call = getattr(chunk, "tool_call", None)
        if isinstance(raw_tool_call, dict):
            tool_call_payload = dict(raw_tool_call)

        raw_tool_result = getattr(chunk, "tool_result", None)
        if isinstance(raw_tool_result, dict):
            tool_result_payload = dict(raw_tool_result)

        done = bool(getattr(chunk, "done", False))

        for key in ("provider_id", "provider", "model"):
            value = getattr(chunk, key, None)
            if value not in (None, "") and key not in metadata:
                metadata[key] = value

    # Check metadata for tool payloads
    if not tool_call_payload:
        meta_tool_call = metadata.get("tool_call")
        if isinstance(meta_tool_call, dict):
            tool_call_payload = dict(meta_tool_call)

    if not tool_result_payload:
        meta_tool_result = metadata.get("tool_result")
        if isinstance(meta_tool_result, dict):
            tool_result_payload = dict(meta_tool_result)

    if not error_message:
        error_message = str(metadata.get("error") or "").strip()

    # Normalize event type
    event_type = EVENT_TYPE_ALIASES.get(event_type, event_type)

    # Infer event type if missing
    if not event_type:
        if error_message:
            event_type = "error"
        elif tool_call_payload:
            event_type = "tool_call"
        elif tool_result_payload:
            event_type = "tool_result"
        elif reasoning:
            event_type = "reasoning_chunk"
        elif content:
            event_type = "chunk"
        elif done:
            event_type = "complete"
        else:
            event_type = "unknown"

    # Merge reasoning into content for reasoning_chunk
    if event_type == "reasoning_chunk" and not content:
        content = reasoning

    # Normalize tool call payload
    tool_name = ""
    tool_args: dict[str, Any] = {}
    tool_call_id = ""

    if event_type == "tool_call":
        tool_name, tool_args, tool_call_id = _normalize_stream_tool_call_payload(tool_call_payload)

    metadata.setdefault("native_tool_mode", native_tool_mode)
    metadata.setdefault("tool_protocol", tool_protocol)
    metadata.setdefault("native_tool_calling_fallback", False)

    return NormalizedStreamEvent(
        event_type=event_type,
        content=content,
        metadata=metadata,
        error=error_message,
        tool_name=tool_name,
        tool_args=tool_args,
        tool_call_id=tool_call_id,
        tool_result=tool_result_payload,
    )


def _normalize_stream_tool_call_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    """Normalize provider-specific tool_call payload into stable fields.

    Args:
        payload: Raw tool call payload

    Returns:
        Tuple of (tool_name, tool_args, call_id)
    """
    if not isinstance(payload, dict):
        return "", {}, ""

    tool_name = str(payload.get("tool") or payload.get("name") or payload.get("tool_name") or "").strip()
    call_id = str(payload.get("call_id") or payload.get("id") or "").strip()

    raw_args: Any = payload.get("arguments")
    if raw_args in (None, ""):
        raw_args = payload.get("args")
    if raw_args in (None, ""):
        raw_args = payload.get("input")

    function_payload = payload.get("function")
    if isinstance(function_payload, dict):
        if not tool_name:
            tool_name = str(function_payload.get("name") or function_payload.get("tool") or "").strip()
        if raw_args in (None, "", {}):
            raw_args = function_payload.get("arguments")

    normalized_args: dict[str, Any] = {}
    if isinstance(raw_args, dict):
        normalized_args = dict(raw_args)
    elif isinstance(raw_args, str):
        token = raw_args.strip()
        if token:
            try:
                parsed = json.loads(token)
                normalized_args = parsed if isinstance(parsed, dict) else {"value": parsed}
            except (RuntimeError, ValueError):
                normalized_args = {"raw": raw_args}

    return tool_name, normalized_args, call_id


def tool_call_signature_from_normalized(event: NormalizedStreamEvent) -> str:
    """Generate deduplication signature for tool call event.

    Args:
        event: Normalized tool call event

    Returns:
        JSON signature string
    """
    payload = {
        "tool": str(event.tool_name or "").strip(),
        "call_id": str(event.tool_call_id or "").strip(),
        "args": dict(event.tool_args or {}),
    }
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (RuntimeError, ValueError):
        return str(payload)


def consume_reconnect_prefix(prefix: str, chunk_text: str) -> tuple[str, str]:
    """Drop replayed chunk prefixes after reconnect to avoid duplicate output.

    Args:
        prefix: Previously emitted content prefix
        chunk_text: New chunk text

    Returns:
        Tuple of (visible_content, remaining_prefix)
    """
    remaining_prefix = str(prefix or "")
    token = str(chunk_text or "")

    if not remaining_prefix or not token:
        return token, remaining_prefix

    if remaining_prefix.startswith(token):
        return "", remaining_prefix[len(token) :]

    if token.startswith(remaining_prefix):
        return token[len(remaining_prefix) :], ""

    # Mismatch: stop consuming to avoid dropping new content
    return token, ""


def build_stream_slo_metrics(
    *,
    elapsed_ms: float,
    event_count: int,
    reconnect_count: int,
    deduped_chunks: int,
    deduped_tool_calls: int,
    raw_tool_calls: int,
    first_event_latency_ms: float | None,
    backpressure_wait_ms: float,
) -> dict[str, Any]:
    """Build stream SLO metrics dictionary.

    Args:
        elapsed_ms: Total elapsed time in milliseconds
        event_count: Total event count
        reconnect_count: Reconnect attempt count
        deduped_chunks: Deduplicated chunk count
        deduped_tool_calls: Deduplicated tool call count
        raw_tool_calls: Raw tool call count (including duplicates)
        first_event_latency_ms: First event latency
        backpressure_wait_ms: Backpressure wait time

    Returns:
        Metrics dictionary
    """
    throughput = 0.0
    safe_elapsed_ms = max(0.0, float(elapsed_ms))

    if safe_elapsed_ms > 0:
        throughput = round((max(0, int(event_count)) * 1000.0) / safe_elapsed_ms, 3)

    metrics: dict[str, Any] = {
        "elapsed_ms": round(safe_elapsed_ms, 2),
        "stream_elapsed_ms": round(safe_elapsed_ms, 2),
        "stream_event_count": max(0, int(event_count)),
        "stream_reconnect_count": max(0, int(reconnect_count)),
        "stream_deduped_chunk_count": max(0, int(deduped_chunks)),
        "stream_deduped_tool_call_count": max(0, int(deduped_tool_calls)),
        "stream_raw_tool_call_count": max(0, int(raw_tool_calls)),
        "stream_backpressure_wait_ms": round(max(0.0, float(backpressure_wait_ms)), 2),
        "stream_events_per_second": throughput,
    }

    if first_event_latency_ms is not None:
        metrics["stream_first_event_latency_ms"] = round(max(0.0, float(first_event_latency_ms)), 2)

    return metrics


def resolve_stream_runtime_config(context: Any) -> dict[str, Any]:
    """Resolve stream runtime configuration from context override.

    Args:
        context: ContextRequest with context_override

    Returns:
        Runtime configuration dictionary
    """
    override = getattr(context, "context_override", None)
    options = dict(override) if isinstance(override, dict) else {}

    def _to_int(name: str, default: int, *, minimum: int = 0, maximum: int = 10) -> int:
        raw = options.get(name, default)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))

    def _to_float(name: str, default: float, *, minimum: float = 0.0, maximum: float = 30.0) -> float:
        raw = options.get(name, default)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(value, maximum))

    def _to_bool(name: str, default: bool = False) -> bool:
        raw = options.get(name, default)
        if isinstance(raw, bool):
            return raw
        token = str(raw or "").strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
        return bool(default)

    return {
        "max_reconnects": _to_int("stream_max_reconnects", 1, maximum=5),
        "retry_backoff_seconds": _to_float("stream_retry_backoff_seconds", 0.35, maximum=10.0),
        "cancel_requested": _to_bool("stream_cancelled", False) or _to_bool("cancel_requested", False),
        "emit_unknown_events": _to_bool("stream_emit_unknown_events", False),
        "dedupe_reconnect_replay": _to_bool("stream_dedupe_reconnect_replay", True),
        "max_backpressure_wait_ms": _to_int("stream_max_backpressure_wait_ms", 0, maximum=120000),
        "max_events": _to_int("stream_max_events", 0, maximum=200000),
    }


def is_stream_cancel_requested(context: Any) -> bool:
    """Check if stream cancellation was requested via context override.

    Args:
        context: ContextRequest with context_override

    Returns:
        True if cancellation requested
    """
    override = getattr(context, "context_override", None)
    if not isinstance(override, dict):
        return False

    for key in ("stream_cancelled", "cancel_requested", "cancelled"):
        raw = override.get(key, False)
        if isinstance(raw, bool):
            if raw:
                return True
            continue
        token = str(raw or "").strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True

    return False


__all__ = [
    "EVENT_TYPE_ALIASES",
    "build_stream_slo_metrics",
    "consume_reconnect_prefix",
    "is_stream_cancel_requested",
    "normalize_stream_chunk",
    "resolve_stream_runtime_config",
    "tool_call_signature_from_normalized",
]
