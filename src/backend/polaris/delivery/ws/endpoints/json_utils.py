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


def parse_json_line(raw: str) -> dict[str, Any] | None:
    """Parse a JSON line safely.

    Args:
        raw: Raw line string.

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
    """Serialized UTF-8 byte size of a JSON-encodable value (best effort)."""
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return len(str(value).encode("utf-8"))


# Worst-case byte length of the ``…[ws-elided N bytes]`` marker suffix.
_ELIDE_MARKER_OVERHEAD = 40


def _truncate_string(text: str, budget: int) -> str:
    """Truncate ``text`` to fit ``budget`` bytes, appending an elision marker."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max(0, budget):
        return text
    keep = min(len(encoded), _ELIDE_STRING_PREVIEW_BYTES, max(0, budget - _ELIDE_MARKER_OVERHEAD))
    preview = encoded[:keep].decode("utf-8", "ignore")
    return f"{preview}…[ws-elided {len(encoded)} bytes]"


def _elide_mapping(value: Mapping[str, Any], budget: int) -> dict[str, Any]:
    """Shrink a mapping to ``budget`` bytes, keeping small fields, shrinking large ones."""
    small: dict[str, Any] = {}
    large: list[tuple[str, Any]] = []
    for raw_key, item in value.items():
        key = str(raw_key)
        if _frame_byte_size({key: item}) <= _ELIDE_SMALL_FIELD_BYTES:
            small[key] = item
        else:
            large.append((key, item))
    out: dict[str, Any] = dict(small)
    if not large:
        return out
    remaining = max(0, budget - _frame_byte_size(small))
    per_field = max(_ELIDE_SMALL_FIELD_BYTES, remaining // len(large))
    for key, item in large:
        out[key] = _elide_value(item, per_field)
    return out


def _elide_sequence(value: list[Any] | tuple[Any, ...], budget: int) -> list[Any]:
    """Shrink a sequence to ``budget`` bytes, shrinking elements and dropping the tail."""
    seq = list(value)
    out: list[Any] = []
    used = 2  # account for the enclosing brackets
    for index, item in enumerate(seq):
        remaining = budget - used - _ELIDE_MARKER_OVERHEAD
        if remaining <= 0 and out:
            out.append(f"…[ws-elided {len(seq) - index} more items]")
            break
        per_item = min(max(0, remaining), max(_ELIDE_SMALL_FIELD_BYTES, remaining // max(1, len(seq) - index)))
        shrunk = _elide_value(item, per_item)
        item_size = _frame_byte_size(shrunk) + 1  # trailing comma
        if used + item_size > budget - _ELIDE_MARKER_OVERHEAD and out:
            out.append(f"…[ws-elided {len(seq) - index} more items]")
            break
        out.append(shrunk)
        used += item_size
    return out


def _elide_value(value: Any, budget: int) -> Any:
    """Best-effort shrink of ``value`` so its serialized size is roughly ``<= budget``."""
    budget = max(0, budget)
    if _frame_byte_size(value) <= budget:
        return value
    if isinstance(value, str):
        return _truncate_string(value, budget)
    if isinstance(value, Mapping):
        return _elide_mapping(value, budget)
    if isinstance(value, (list, tuple)):
        return _elide_sequence(value, budget)
    # A non-container scalar that is somehow oversized — stringify and truncate.
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
            if (isinstance(val, (str, int, float, bool)) or val is None) and _frame_byte_size(
                {key: val}
            ) <= _ELIDE_SMALL_FIELD_BYTES:
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
    result = _elide_value(payload, budget)
    if _frame_byte_size(result) <= budget:
        return result
    return _hard_floor_frame(payload, budget)


__all__ = [
    "elide_oversized_frame",
    "parse_json_line",
    "resolve_journal_event_channel",
    "sanitize_snapshot_lines",
]
