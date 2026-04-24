"""
KernelOne Utils Package - Unified utilities for KernelOne.

This package consolidates all duplicated utility functions across the codebase.
All modules should import from this package instead of defining local versions.

Usage:
    from polaris.kernelone.utils import utc_now, GENESIS_HASH, safe_json_loads
    from polaris.kernelone.utils.time_utils import utc_now_iso
    from polaris.kernelone.utils.constants import GENESIS_HASH
    from polaris.kernelone.utils.json_utils import parse_json_payload

Module Overview:
- time_utils.py: Time-related functions (utc_now, utc_now_iso, utc_now_str)
- constants.py: Shared constants (GENESIS_HASH, retention periods)
- json_utils.py: JSON parsing utilities (safe_json_loads, parse_json_payload)

Migration Guide:
Replace local definitions with imports:
    # Before (local definition)
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    # After (import from utils)
    from polaris.kernelone.utils.time_utils import utc_now as _utc_now
"""

from polaris.kernelone.utils.constants import (
    DEFAULT_AUDIT_RETENTION_DAYS,
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    DEFAULT_SESSION_TIMEOUT_SECONDS,
    EMPTY_SHA256,
    GENESIS_HASH,
)
from polaris.kernelone.utils.json_utils import (
    format_json,
    parse_json_payload,
    safe_json_loads,
)
from polaris.kernelone.utils.time_utils import (
    ISO_FORMAT_SUFFIX_Z,
    PROCESS_COMMAND_TIMEOUT_SECONDS,
    UTC_TZ_SUFFIX,
    _now,
    utc_now,
    utc_now_iso,
    utc_now_iso_compact,
    utc_now_str,
)

# Backward compatibility - expose old names at package level
_utc_now = utc_now
_utc_now_iso = utc_now_iso
_utc_now_str = utc_now_str
_safe_json_loads = safe_json_loads
_parse_json_payload = parse_json_payload


__all__ = [
    "DEFAULT_AUDIT_RETENTION_DAYS",
    "DEFAULT_LOCK_TIMEOUT_SECONDS",
    "DEFAULT_SESSION_TIMEOUT_SECONDS",
    "EMPTY_SHA256",
    # Constants
    "GENESIS_HASH",
    "ISO_FORMAT_SUFFIX_Z",
    "PROCESS_COMMAND_TIMEOUT_SECONDS",
    "UTC_TZ_SUFFIX",
    "_now",
    "_parse_json_payload",
    "_safe_json_loads",
    # Backward compatibility
    "_utc_now",
    "_utc_now_iso",
    "_utc_now_str",
    "format_json",
    "parse_json_payload",
    # JSON utilities
    "safe_json_loads",
    # Time utilities
    "utc_now",
    "utc_now_iso",
    "utc_now_iso_compact",
    "utc_now_str",
]
