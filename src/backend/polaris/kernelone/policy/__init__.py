"""Deprecated: Import from polaris.cells.policy.permission.internal or
polaris.cells.context.engine.internal instead. Do NOT re-export from here —
kernelone must not backward-import from cells."""

from __future__ import annotations

import warnings

__all__ = []

warnings.warn(
    "polaris.kernelone.policy is deprecated. "
    "Import from polaris.cells.policy.permission.internal.non_llm_gates or "
    "polaris.cells.context.engine.internal.precision_mode instead.",
    DeprecationWarning,
    stacklevel=2,
)
