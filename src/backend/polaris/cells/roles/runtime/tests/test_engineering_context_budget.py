from __future__ import annotations

import time

import pytest
from polaris.cells.roles.runtime.internal.engineering_context_budget import (
    _DEFAULT_FOLD_WINDOW,
    _MIN_FOLD_WINDOW,
    context_fold_window,
)
from polaris.cells.roles.runtime.public.contracts.engineering_run import RunLimits


def test_healthy_run_keeps_default_window() -> None:
    now = time.time()
    limits = RunLimits(now + 3600.0, 1_000_000, 64_000)
    assert (
        context_fold_window(limits=limits, created_at=now, current_tokens=10_000, context_window_tokens=100_000)
        == _DEFAULT_FOLD_WINDOW
    )


def test_tight_deadline_narrows_window_but_keeps_floor() -> None:
    now = time.time()
    limits = RunLimits(now + 60.0, 1_000_000, 64_000)
    assert (
        context_fold_window(limits=limits, created_at=now - 3540.0, current_tokens=10_000, context_window_tokens=100_000)
        == max(_MIN_FOLD_WINDOW, _DEFAULT_FOLD_WINDOW - 2)
    )


def test_large_request_narrows_window() -> None:
    now = time.time()
    limits = RunLimits(now + 3600.0, 1_000_000, 64_000)
    assert (
        context_fold_window(limits=limits, created_at=now, current_tokens=80_000, context_window_tokens=100_000)
        == max(_MIN_FOLD_WINDOW, _DEFAULT_FOLD_WINDOW - 2)
    )


def test_no_deadline_uses_request_pressure_only() -> None:
    now = time.time()
    limits = RunLimits(None, 1_000_000, 64_000)
    assert (
        context_fold_window(limits=limits, created_at=now, current_tokens=95_000, context_window_tokens=100_000)
        == max(_MIN_FOLD_WINDOW, _DEFAULT_FOLD_WINDOW - 2)
    )
    assert (
        context_fold_window(limits=limits, created_at=now, current_tokens=1_000, context_window_tokens=100_000)
        == _DEFAULT_FOLD_WINDOW
    )


def test_fold_window_never_drops_below_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "8")
    now = time.time()
    limits = RunLimits(now + 60.0, 1_000_000, 64_000)
    # Both pressure signals fire: the floor still applies.
    assert (
        context_fold_window(limits=limits, created_at=now - 3540.0, current_tokens=95_000, context_window_tokens=100_000)
        == _MIN_FOLD_WINDOW
    )
