"""Readiness timestamp audit helpers for LLM evaluation indexes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

DEFAULT_READINESS_MAX_AGE_SECONDS = 0


def readiness_max_age_seconds() -> int:
    """Return the readiness max age in seconds.

    Readiness is tied to the tested provider/model identity and result, not
    wall-clock age. The legacy public function remains for compatibility.
    """
    return DEFAULT_READINESS_MAX_AGE_SECONDS


def parse_readiness_timestamp(value: Any) -> datetime | None:
    """Parse an evaluation timestamp as UTC."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def readiness_freshness_issue(
    timestamp: Any,
    *,
    now: datetime | None = None,
    max_age_seconds: int | None = None,
    missing_is_stale: bool = False,
) -> str:
    """Return a timestamp audit issue code, or ``""`` when acceptable."""
    _ = (now, max_age_seconds)
    if not isinstance(timestamp, str) or not timestamp.strip():
        return "timestamp_missing" if missing_is_stale else ""

    parsed = parse_readiness_timestamp(timestamp)
    if parsed is None:
        return "timestamp_invalid"

    return ""
