"""Adaptive context budget governor for long-running engineering runs."""

from __future__ import annotations

import os
import time

from polaris.cells.roles.runtime.public.contracts.engineering_run import RunLimits

_MIN_FOLD_WINDOW = 6
_DEFAULT_FOLD_WINDOW = 24


def _default_window() -> int:
    return max(
        _MIN_FOLD_WINDOW,
        int(os.environ.get("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", str(_DEFAULT_FOLD_WINDOW))),
    )


def context_fold_window(
    *,
    limits: RunLimits,
    created_at: float,
    current_tokens: int,
    context_window_tokens: int,
) -> int:
    """Keep the recent window wide when the run is healthy, narrow it under pressure.

    Pressure comes from remaining wall-clock budget and from the current request
    already occupying most of the model window. The window never drops below the
    safe floor, so continuity for the current reasoning is preserved.
    """

    base = _default_window()
    pressure = 0
    if limits.deadline is not None:
        total = max(0.0, float(limits.deadline) - float(created_at))
        if total > 0:
            remaining_fraction = max(0.0, min(1.0, (float(limits.deadline) - time.time()) / total))
            if remaining_fraction < 0.2:
                pressure += 1
    if context_window_tokens > 0 and current_tokens / float(context_window_tokens) >= 0.7:
        pressure += 1
    if pressure == 0:
        return base
    return max(_MIN_FOLD_WINDOW, base - (2 * pressure))
