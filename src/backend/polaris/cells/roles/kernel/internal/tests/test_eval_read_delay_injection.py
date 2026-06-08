"""评测专用只读延迟注入开关的单测（默认零注入，不影响生产路径）."""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.internal.stream_shadow_engine import (
    _eval_read_shadow_timeout_ms,
)
from polaris.cells.roles.kernel.internal.tool_batch_runtime import (
    _eval_injected_read_delay_ms,
)


def test_read_delay_default_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPECULATION_EVAL_READ_DELAY_MS", raising=False)
    assert _eval_injected_read_delay_ms() == 0


def test_read_delay_parses_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPECULATION_EVAL_READ_DELAY_MS", "1500")
    assert _eval_injected_read_delay_ms() == 1500


def test_read_delay_invalid_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPECULATION_EVAL_READ_DELAY_MS", "not-a-number")
    assert _eval_injected_read_delay_ms() == 0


def test_read_delay_negative_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPECULATION_EVAL_READ_DELAY_MS", "-50")
    assert _eval_injected_read_delay_ms() == 0


def test_shadow_timeout_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPECULATION_EVAL_READ_TIMEOUT_MS", raising=False)
    assert _eval_read_shadow_timeout_ms() == 1200


def test_shadow_timeout_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPECULATION_EVAL_READ_TIMEOUT_MS", "8000")
    assert _eval_read_shadow_timeout_ms() == 8000


def test_shadow_timeout_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPECULATION_EVAL_READ_TIMEOUT_MS", "bad")
    assert _eval_read_shadow_timeout_ms() == 1200
