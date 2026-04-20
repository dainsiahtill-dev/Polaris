from __future__ import annotations

import os

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in _TRUE_VALUES


def is_speculative_execution_enabled() -> bool:
    """Return whether speculative execution is enabled.

    Priority:
    1. ENABLE_SPECULATIVE_EXECUTION
    2. KERNELONE_ENABLE_SPECULATIVE_EXECUTION (compat)
    Default: disabled.
    """
    primary = os.environ.get("ENABLE_SPECULATIVE_EXECUTION")
    if primary is not None:
        return _parse_bool(primary, default=False)
    compat = os.environ.get("KERNELONE_ENABLE_SPECULATIVE_EXECUTION")
    return _parse_bool(compat, default=False)
