"""Single source of truth for context-snapshot hash validation.

Both the producer (``AIExecutor._store_context_messages_sync``) and the
consumer (``GET /v2/context/{hash}``) must agree on the exact hash format,
or one side can write a key the other side refuses to read.  This module
is the only place that defines and validates the contract — the rest of
the codebase should call :func:`validate_context_hash` instead of rolling
its own character-class check.
"""

from __future__ import annotations

import re

# 24 lowercase hex chars = 12 bytes / 96 bits.  This is the truncated
# SHA-256 prefix written by ``_store_context_messages_sync``.
CONTEXT_HASH_PATTERN: re.Pattern[str] = re.compile(r"^[0-9a-f]{24}$")


def validate_context_hash(value: str) -> str:
    """Return ``value`` if and only if it matches ``CONTEXT_HASH_PATTERN``.

    Lenient whitespace handling: leading and trailing whitespace (spaces,
    tabs, newlines, carriage returns) is stripped from ``value`` before the
    pattern check.  After stripping, the candidate must strictly
    ``re.fullmatch`` ``CONTEXT_HASH_PATTERN`` — no embedded whitespace, no
    embedded nulls, no uppercase, no percent-encoded separators, no Unicode
    escapes.  On mismatch a :class:`ValueError` is raised that callers can
    map to whatever transport they prefer (HTTP 400, log rejection, etc).

    The returned canonical value is the *stripped* (and already lowercase)
    candidate — ``value`` itself is never echoed back.  This guarantees the
    producer and consumer always share a single canonical form on disk,
    regardless of incidental transport-level whitespace.

    Args:
        value: The candidate hash string.

    Returns:
        The stripped, lowercase canonical hash (24 hex chars).

    Raises:
        ValueError: If ``value`` is not a string, or if the stripped
            candidate does not match the 24-char hex pattern.
    """
    if not isinstance(value, str):
        raise ValueError("context hash must be a string")
    candidate = value.strip()
    if not CONTEXT_HASH_PATTERN.fullmatch(candidate):
        raise ValueError("context hash must be a 24-character lowercase hexadecimal string")
    return candidate


__all__ = [
    "CONTEXT_HASH_PATTERN",
    "validate_context_hash",
]
