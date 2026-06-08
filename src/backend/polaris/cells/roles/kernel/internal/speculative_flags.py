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
    Default: enabled.
    """
    primary = os.environ.get("ENABLE_SPECULATIVE_EXECUTION")
    if primary is not None:
        return _parse_bool(primary, default=True)
    compat = os.environ.get("KERNELONE_ENABLE_SPECULATIVE_EXECUTION")
    return _parse_bool(compat, default=True)


def is_adoption_audit_enabled() -> bool:
    """Return whether speculative-adoption auditing is enabled.

    When enabled, every ADOPT/JOIN of a speculative result is additionally
    re-executed authoritatively and compared; any mismatch is recorded as a
    ``wrong_adoption`` and the authoritative result is used instead (detector +
    safety net). This proves ADR-0077 invariant A (correctness unchanged) on
    real traffic and is the basis of the speculation eval matrix's hard gate.

    Disabled by default — it doubles tool execution cost, so it is only turned
    on by evaluation / verification runs via ``SPECULATION_AUDIT_ADOPTIONS``.
    """
    return _parse_bool(os.environ.get("SPECULATION_AUDIT_ADOPTIONS"), default=False)
