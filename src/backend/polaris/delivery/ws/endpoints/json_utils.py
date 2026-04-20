"""JSON parsing utility functions for runtime WebSocket endpoint.

This module contains:
- JSON line parsing helpers
- Journal event channel resolution
- Snapshot line sanitization
"""

from __future__ import annotations

import json
import logging
from typing import Any

from polaris.delivery.ws.endpoints.channel_utils import is_llm_channel
from polaris.delivery.ws.endpoints.models import JOURNAL_CHANNELS

logger = logging.getLogger(__name__)


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


__all__ = [
    "parse_json_line",
    "resolve_journal_event_channel",
    "sanitize_snapshot_lines",
]
