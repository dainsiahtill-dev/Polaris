"""Shared run_id validation utilities.

Security goals:
- Block path traversal or filesystem separators.
- Accept legacy and new run_id shapes used across Polaris.
- Keep one validation source to avoid rule drift.
"""

from __future__ import annotations

import re

_RUN_ID_ALLOWED_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
_RUN_ID_SEPARATOR_RE = re.compile(r"[-_]")


def normalize_run_id(run_id: str | None) -> str:
    """Normalize external run_id input to a safe token candidate."""
    return str(run_id or "").strip()


def validate_run_id(run_id: str | None) -> bool:
    """Validate run_id for safe storage and query operations."""
    token = normalize_run_id(run_id)
    if not token:
        return False

    if ".." in token or "/" in token or "\\" in token:
        return False

    if not _RUN_ID_ALLOWED_RE.fullmatch(token):
        return False

    # Require at least one delimiter to avoid weak free-form identifiers.
    return _RUN_ID_SEPARATOR_RE.search(token) is not None


def ensure_valid_run_id(run_id: str | None) -> str:
    """Return normalized run_id or raise ValueError."""
    token = normalize_run_id(run_id)
    if not validate_run_id(token):
        raise ValueError(f"invalid run_id format: {token}")
    return token
