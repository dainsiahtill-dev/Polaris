"""
KernelOne Time Utilities - Single source of truth for time-related functions.

This module consolidates all duplicated time utility functions across the codebase.
All modules should import from here instead of defining their own versions.

Usage:
    from polaris.kernelone.utils.time_utils import utc_now, utc_now_iso, utc_now_str
"""

from datetime import datetime, timezone
from typing import Final

# Constants for time formatting
ISO_FORMAT_SUFFIX_Z: Final[str] = "Z"
UTC_TZ_SUFFIX: Final[str] = "+00:00"

# Unified timeout constant for process/subprocess operations
# Use this instead of hardcoding 30 or 300 seconds
PROCESS_COMMAND_TIMEOUT_SECONDS: Final[int] = 30


def utc_now() -> datetime:
    """
    Return current UTC datetime.

    This is the canonical implementation - all modules should use this instead
    of defining local _utc_now() functions.

    Returns:
        datetime: Current UTC datetime with timezone info.
    """
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """
    Return current UTC timestamp in ISO-8601 format with timezone.

    Format: 2026-04-04T12:34:56.789012+00:00

    Returns:
        str: ISO-8601 formatted UTC timestamp.
    """
    return datetime.now(timezone.utc).isoformat()


def utc_now_str() -> str:
    """
    Return current UTC timestamp in ISO format with 'Z' suffix.

    Format: 2026-04-04T12:34:56.789012Z

    This is commonly used for filenames and identifiers where the Z suffix
    is preferred over +00:00.

    Returns:
        str: ISO formatted UTC timestamp with Z suffix.
    """
    return datetime.now(timezone.utc).isoformat().replace(UTC_TZ_SUFFIX, ISO_FORMAT_SUFFIX_Z)


def utc_now_iso_compact() -> str:
    """
    Return current UTC timestamp in ISO format with seconds precision.

    Format: 2026-04-04T12:34:56

    Useful for human-readable timestamps where microseconds aren't needed.

    Returns:
        str: ISO formatted UTC timestamp with seconds precision.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now() -> str:
    """
    Return current UTC timestamp as ISO string with zeroed microseconds.

    Format: 2026-04-04T12:34:56

    This is the canonical implementation for workflow/saga timestamps.
    Use this instead of defining local _now() functions that return ISO strings.

    Returns:
        str: ISO formatted UTC timestamp with seconds precision.
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# Backward compatibility aliases - map old names to new canonical names
_utc_now = utc_now
_utc_now_iso = utc_now_iso
_utc_now_str = utc_now_str


__all__ = [
    "ISO_FORMAT_SUFFIX_Z",
    "PROCESS_COMMAND_TIMEOUT_SECONDS",
    "UTC_TZ_SUFFIX",
    "_now",
    # Backward compatibility
    "_utc_now",
    "_utc_now_iso",
    "_utc_now_str",
    "utc_now",
    "utc_now_iso",
    "utc_now_iso_compact",
    "utc_now_str",
]
