"""JSON parsing utility functions for runtime WebSocket endpoint.

This module contains:
- JSON line parsing helpers
- Journal event channel resolution
- Snapshot line sanitization
- Oversized-frame elision (keeps every runtime.v2 frame under the WS limit)
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from polaris.delivery.ws.endpoints.channel_utils import is_llm_channel
from polaris.delivery.ws.endpoints.models import JOURNAL_CHANNELS

logger = logging.getLogger(__name__)

# Bytes of a single oversized string we keep as a readable preview before the marker.
_ELIDE_STRING_PREVIEW_BYTES = 1024
# A value whose serialized size is at/under this is always preserved verbatim. Control-plane
# fields (``type`` / ``status`` / ``stage`` / ``cursor`` ...) are tiny scalars and therefore
# always survive elision, which is what realtime consumers (bench status extraction, the
# frontend factory panel) depend on.
_ELIDE_SMALL_FIELD_BYTES = 1024


# =============================================================================
# JSON Parsing Helpers
# =============================================================================


def parse_json_line(raw: str | None) -> dict[str, Any] | None:
    """Parse a JSON line safely.

    Args:
        raw: Raw line string (``None`` is tolerated and parsed as empty).

    Returns:
        Parsed dictionary, or None if invalid.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except (RuntimeError, ValueError) as exc:
        logger.debug("json.loads line failed: %s", exc)
        return None
    if isinstance(payload, dict):
        return payload
    return None


def sanitize_snapshot_lines(channel: str, lines: list[str]) -> list[str]:
    """Sanitize snapshot lines for LLM channel.

    Args:
        channel: Channel name.
        lines: Raw lines from snapshot.

    Returns:
        Sanitized lines.
    """
    if not is_llm_channel(channel) or not lines:
        return lines
    first = str(lines[0] or "").lstrip()
    if first and not first.startswith("{"):
        return lines[1:]
    return lines


def resolve_journal_event_channel(raw_line: str) -> str:
    """Resolve target channel from journal event line.

    Args:
        raw_line: Raw JSON line from journal.

    Returns:
        Target channel name.
    """
    payload = parse_json_line(raw_line)
    if payload is None:
        return "system"

    channel = str(payload.get("channel") or "").strip().lower()
    if channel in JOURNAL_CHANNELS:
        return channel

    domain = str(payload.get("domain") or "").strip().lower()
    if domain in {"llm", "process", "system"}:
        return "llm" if domain == "llm" else ("process" if domain == "process" else "system")

    return "system"


# =============================================================================
# Oversized-frame elision
# =============================================================================
#
# The runtime.v2 WebSocket emits one event per frame. Some payloads (notably the
# factory ``stage_completed`` event, which embeds the full ``StageResult.output``)
# can exceed the WebSocket frame limit (the ``websockets`` client defaults to
# 1,048,576 bytes and drops the connection with close code 1009 "message too
# big"). A dropped connection means every subscriber — the factory-bench chain
# waiter AND the live frontend factory panel — loses realtime delivery.
#
# ``elide_oversized_frame`` bounds any payload to a byte budget by preserving
# small control-plane fields verbatim and replacing oversized strings/containers
# with truncation markers. The durable on-disk event store keeps full fidelity;
# only the realtime frame is bounded.


def _frame_byte_size(value: Any) -> int:
    """Serialized UTF-8 byte size of one complete frame (best effort).

    This deliberately remains a top-level operation. Recursive elision uses
    structural byte accounting below; serializing every subtree turns a deep,
    multi-megabyte runtime event into quadratic CPU and allocation work.
    """
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return len(str(value).encode("utf-8"))


def _json_string_byte_size(text: str) -> int:
    """Exact ``ensure_ascii=False`` JSON byte size without serializing ``text``."""
    size = 2  # quotes
    for char in text:
        codepoint = ord(char)
        if char in {'"', "\\"} or char in {"\b", "\t", "\n", "\f", "\r"}:
            size += 2
        elif codepoint < 0x20:
            size += 6  # ``\u00XX``
        else:
            size += len(char.encode("utf-8"))
    return size


def _scalar_byte_size(value: Any) -> int | None:
    """Exact JSON size for native scalar values; ``None`` means container/unknown."""
    if value is None:
        return 4
    if value is True:
        return 4
    if value is False:
        return 5
    if isinstance(value, str):
        return _json_string_byte_size(value)
    if isinstance(value, int):
        return len(str(value).encode("utf-8"))
    if isinstance(value, float):
        if value != value:
            return 3  # NaN
        if value == float("inf") or value == float("-inf"):
            return 9 if value < 0 else 8  # -Infinity / Infinity
        return len(str(value).encode("utf-8"))
    return None


def _truncate_string(text: str, budget: int) -> tuple[str, int]:
    """Truncate ``text`` within serialized ``budget`` and return exact output size."""
    budget = max(0, budget)
    original_size = _json_string_byte_size(text)
    if original_size <= budget:
        return text, original_size

    raw_size = len(text.encode("utf-8"))
    marker = f"…[ws-elided {raw_size} bytes]"
    marker_size = _json_string_byte_size(marker)
    if marker_size > budget:
        marker = "…" if _json_string_byte_size("…") <= budget else ""

    preview_chars: list[str] = []
    preview_bytes = 0
    for char in text:
        char_bytes = len(char.encode("utf-8"))
        if preview_bytes + char_bytes > _ELIDE_STRING_PREVIEW_BYTES:
            break
        candidate = "".join((*preview_chars, char, marker))
        if _json_string_byte_size(candidate) > budget:
            break
        preview_chars.append(char)
        preview_bytes += char_bytes
    result = "".join((*preview_chars, marker))
    return result, _json_string_byte_size(result)


def _elide_mapping(value: Mapping[Any, Any], budget: int) -> tuple[dict[str, Any], int]:
    """Copy a mapping once, assigning remaining bytes across sibling fields."""
    items = [(str(raw_key), item) for raw_key, item in value.items()]
    fixed_size = 2  # braces
    if items:
        fixed_size += 2 * (len(items) - 1)  # default JSON ``, `` separator
        fixed_size += sum(_json_string_byte_size(key) + 2 for key, _ in items)  # ``: ``

    remaining = max(0, budget - fixed_size)
    out: dict[str, Any] = {}
    child_sizes = 0
    for index, (key, item) in enumerate(items):
        fields_left = len(items) - index
        field_budget = remaining // max(1, fields_left)
        shrunk, item_size = _elide_value(item, field_budget)
        out[key] = shrunk
        child_sizes += item_size
        remaining = max(0, remaining - item_size)
    return out, fixed_size + child_sizes


def _elide_sequence(value: list[Any] | tuple[Any, ...], budget: int) -> tuple[list[Any], int]:
    """Copy a sequence once, assigning remaining bytes across its elements."""
    fixed_size = 2 + max(0, len(value) - 1) * 2  # brackets + default ``, `` separators
    remaining = max(0, budget - fixed_size)
    out: list[Any] = []
    child_sizes = 0
    for index, item in enumerate(value):
        items_left = len(value) - index
        item_budget = remaining // max(1, items_left)
        shrunk, item_size = _elide_value(item, item_budget)
        out.append(shrunk)
        child_sizes += item_size
        remaining = max(0, remaining - item_size)
    return out, fixed_size + child_sizes


def _elide_value(value: Any, budget: int) -> tuple[Any, int]:
    """Build one bounded structural copy without serializing recursive subtrees."""
    budget = max(0, budget)
    scalar_size = _scalar_byte_size(value)
    if scalar_size is not None:
        if scalar_size <= budget:
            return value, scalar_size
        return _truncate_string(str(value), budget)
    if isinstance(value, Mapping):
        return _elide_mapping(value, budget)
    if isinstance(value, (list, tuple)):
        return _elide_sequence(value, budget)
    return _truncate_string(str(value), budget)


# Top-level control-plane keys kept by the hard-floor net (all small scalars).
_FRAME_CONTROL_KEYS = (
    "type",
    "channel",
    "snapshot",
    "cursor",
    "schema_version",
    "run_id",
    "kind",
    "event_id",
    "protocol",
    "reason",
)


def _hard_floor_frame(payload: Any, budget: int) -> dict[str, Any]:
    """Last-resort bound: keep only top-level small control scalars plus a marker.

    Reached only when per-field minimums summed past ``budget`` (pathological:
    very many sibling fields). Guarantees a strictly bounded, still-parseable frame.
    """
    keep: dict[str, Any] = {}
    if isinstance(payload, Mapping):
        for key in _FRAME_CONTROL_KEYS:
            if key not in payload:
                continue
            val = payload[key]
            scalar_size = _scalar_byte_size(val)
            if scalar_size is not None and scalar_size <= _ELIDE_SMALL_FIELD_BYTES:
                keep[str(key)] = val
    keep["__ws_frame_elided__"] = True
    return keep


def elide_oversized_frame(payload: Any, max_bytes: int) -> Any:
    """Return a JSON-serializable copy of ``payload`` bounded to ``max_bytes`` bytes.

    Small control-plane fields are preserved verbatim; oversized strings and
    containers are replaced with truncation markers. Idempotent for payloads that
    already fit (returns them unchanged). The result is guaranteed to serialize to
    at most ``max_bytes`` bytes — a hard-floor fallback applies when best-effort
    elision cannot fit (e.g. thousands of sibling fields).
    """
    budget = max(0, max_bytes)
    if _frame_byte_size(payload) <= budget:
        return payload
    pathological_siblings = isinstance(payload, Mapping) and (len(payload) * _ELIDE_SMALL_FIELD_BYTES > budget)
    if not pathological_siblings:
        result, _estimated_size = _elide_value(payload, budget)
        if _frame_byte_size(result) <= budget:
            return result
    hard_floor = _hard_floor_frame(payload, budget)
    if _frame_byte_size(hard_floor) <= budget:
        return hard_floor
    # JSON has no representation smaller than one byte. Tiny synthetic budgets
    # cannot retain envelope semantics; return the smallest valid values possible.
    return {} if budget >= 2 else 0


__all__ = [
    "elide_oversized_frame",
    "parse_json_line",
    "resolve_journal_event_channel",
    "sanitize_snapshot_lines",
]
