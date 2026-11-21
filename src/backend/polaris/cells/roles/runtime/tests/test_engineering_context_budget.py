from __future__ import annotations

import time

from polaris.cells.roles.runtime.internal.engineering_context_budget import context_fold_window
from polaris.cells.roles.runtime.public.contracts.engineering_run import RunLimits


def test_healthy_run_keeps_default_window() -> None:
    now = time.time()
    limits = RunLimits(now + 3600.0, 1_000_000, 64_000)
    assert (
        context_fold_window(limits=limits, created_at=now, current_tokens=10_000, context_window_tokens=100_000) == 10
    )


def test_tight_deadline_narrows_window_but_keeps_floor() -> None:
    now = time.time()
    limits = RunLimits(now + 60.0, 1_000_000, 64_000)
    assert (
        context_fold_window(limits=limits, created_at=now - 3540.0, current_tokens=10_000, context_window_tokens=100_000)
        == 8
    )


def test_large_request_narrows_window() -> None:
    now = time.time()
    limits = RunLimits(now + 3600.0, 1_000_000, 64_000)
    assert context_fold_window(limits=limits, created_at=now, current_tokens=80_000, context_window_tokens=100_000) == 8


def test_no_deadline_uses_request_pressure_only() -> None:
    now = time.time()
    limits = RunLimits(None, 1_000_000, 64_000)
    assert context_fold_window(limits=limits, created_at=now, current_tokens=95_000, context_window_tokens=100_000) == 8
    assert context_fold_window(limits=limits, created_at=now, current_tokens=1_000, context_window_tokens=100_000) == 10
