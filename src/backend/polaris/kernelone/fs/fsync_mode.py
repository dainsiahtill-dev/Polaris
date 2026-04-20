"""Shared fsync mode helpers for file durability controls."""

from __future__ import annotations

from polaris.kernelone._runtime_config import resolve_env_str

IO_FSYNC_ENV = "KERNELONE_IO_FSYNC_MODE"

_DISABLED_TOKENS = {
    "0",
    "false",
    "no",
    "off",
    "relaxed",
    "skip",
    "disabled",
}


def resolve_fsync_mode(raw_value: str | None = None) -> str:
    """Resolve normalized fsync mode token.

    Args:
        raw_value: Optional explicit mode token. When omitted, resolves
            KERNELONE_IO_FSYNC_MODE (with POLARIS_IO_FSYNC_MODE fallback).

    Returns:
        Lower-cased normalized mode token.
    """
    if raw_value is None:
        raw_value = resolve_env_str("io_fsync_mode") or "strict"
    return str(raw_value or "strict").strip().lower()


def is_fsync_enabled(raw_value: str | None = None) -> bool:
    """Return whether fsync should be executed for writes."""
    return resolve_fsync_mode(raw_value) not in _DISABLED_TOKENS


__all__ = ["IO_FSYNC_ENV", "is_fsync_enabled", "resolve_fsync_mode"]
