"""Canonical storage record-id guard.

Every persisted ledger (risk / adr / incident / tech-debt / tech-radar /
decision / milestone / raid) stores records under
``<ledger_dir>/<record_id>.json`` where ``record_id`` may originate from an
untrusted HTTP path parameter. A bare safe token is therefore mandatory:
alphanumerics plus ``_ . -`` — no path separators, no ``..``, no shell
metacharacters.

This is the single source of truth. It replaces eight byte-identical copies
that previously lived one-per-ledger and differed only in the error-message
label (which the ``label`` parameter now carries for attribution).

Fail-closed: anything that is not a provably-safe token raises ``ValueError``.
The HTTP layer maps that to 400.
"""

from __future__ import annotations

import re

__all__ = [
    "SAFE_RECORD_ID_PATTERN",
    "is_safe_record_id",
    "validate_storage_record_id",
]

# Alphanumerics plus ``_ . -`` only. Full-match anchored on both ends so a
# partial match (e.g. a NUL or trailing path separator) cannot slip through.
SAFE_RECORD_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def is_safe_record_id(value: object) -> bool:
    """Return ``True`` iff ``value`` is a safe bare storage record-id token.

    Bool predicate companion to :func:`validate_storage_record_id` for call
    sites that need a non-raising check. Mirrors the validator exactly: strips
    surrounding whitespace, rejects empty/None, rejects any ``..`` substring
    (defence-in-depth even though the pattern already forbids ``/``), and
    requires a full pattern match.
    """

    token = str(value or "").strip()
    if not token or ".." in token:
        return False
    return SAFE_RECORD_ID_PATTERN.match(token) is not None


def validate_storage_record_id(value: object, *, label: str = "record id") -> str:
    """Validate ``value`` as a safe storage record-id, returning the stripped token.

    Args:
        value: The raw record id (typically from an HTTP path parameter).
        label: Human-readable field name included in the ``ValueError`` message
            so the failure attributes to the correct ledger field
            (e.g. ``"risk_id"``, ``"adr_id"``).

    Returns:
        The stripped, validated token.

    Raises:
        ValueError: If the value is empty, contains ``..``, or is not a bare
            safe token (alphanumerics + ``_ . -``).
    """

    token = str(value or "").strip()
    if not token or ".." in token or not SAFE_RECORD_ID_PATTERN.match(token):
        raise ValueError(f"unsafe {label}: {value!r}")
    return token
