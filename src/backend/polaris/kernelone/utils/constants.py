"""
KernelOne Constants - Single source of truth for shared constants.

This module consolidates all duplicated constant definitions across the codebase.
All modules should import from here instead of defining their own versions.

Usage:
    from polaris.kernelone.utils.constants import GENESIS_HASH, EMPTY_SHA256
"""

from typing import Final

# Audit chain constants
GENESIS_HASH: Final[str] = "0" * 64
"""Genesis hash for audit chains - 64 zeros representing SHA-256 empty."""

EMPTY_SHA256: Final[str] = GENESIS_HASH
"""Alias for GENESIS_HASH - empty SHA-256 hash representation."""

# Default retention periods
DEFAULT_AUDIT_RETENTION_DAYS: Final[int] = 90
"""Default retention period for audit records."""

# Common timeout values
DEFAULT_LOCK_TIMEOUT_SECONDS: Final[int] = 30
"""Default timeout for distributed locks."""

DEFAULT_SESSION_TIMEOUT_SECONDS: Final[int] = 3600
"""Default timeout for sessions (1 hour)."""


__all__ = [
    "DEFAULT_AUDIT_RETENTION_DAYS",
    "DEFAULT_LOCK_TIMEOUT_SECONDS",
    "DEFAULT_SESSION_TIMEOUT_SECONDS",
    "EMPTY_SHA256",
    "GENESIS_HASH",
]
