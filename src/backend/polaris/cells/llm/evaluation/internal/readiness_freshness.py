"""Readiness timestamp freshness helpers for LLM evaluation indexes."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_READINESS_MAX_AGE_SECONDS = 24 * 60 * 60
READINESS_MAX_AGE_ENV = "KERNELONE_LLM_READINESS_MAX_AGE_SECONDS"


def readiness_max_age_seconds() -> int:
    """Return the configured readiness max age in seconds.

    A value of ``0`` disables freshness enforcement. Invalid environment
    values fall back to the production default.
    """
    raw = os.environ.get(READINESS_MAX_AGE_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_READINESS_MAX_AGE_SECONDS
    try:
        parsed = int(float(raw.strip()))
    except ValueError:
        return DEFAULT_READINESS_MAX_AGE_SECONDS
    return max(0, parsed)


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


def _utc_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def readiness_freshness_issue(
    timestamp: Any,
    *,
    now: datetime | None = None,
    max_age_seconds: int | None = None,
    missing_is_stale: bool = False,
) -> str:
    """Return a readiness freshness issue code, or ``""`` when acceptable."""
    max_age = readiness_max_age_seconds() if max_age_seconds is None else max(0, int(max_age_seconds))
    if max_age <= 0:
        return ""

    if not isinstance(timestamp, str) or not timestamp.strip():
        return "timestamp_missing" if missing_is_stale else ""

    parsed = parse_readiness_timestamp(timestamp)
    if parsed is None:
        return "timestamp_invalid"

    current = _utc_now(now)
    if current - parsed > timedelta(seconds=max_age):
        return "readiness_stale"
    return ""
