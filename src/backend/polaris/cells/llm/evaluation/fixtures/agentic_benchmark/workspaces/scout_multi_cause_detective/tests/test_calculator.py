"""Unit tests for the calculator -- these all PASS.

Every assertion uses the SAME precision for a given operand pair, so the stale
cache key in ``src/cache.py`` is never exercised. This is why the bug hides
from the unit suite and only surfaces in ``integration_test.py``.
"""

from __future__ import annotations

from src.cache import clear_cache
from src.calculator import divide


def test_divide_basic() -> None:
    clear_cache()
    assert divide(10.0, 4.0, precision=2) == 2.5


def test_divide_uses_same_precision_repeatedly() -> None:
    clear_cache()
    # Same operands, same precision -> cache hit is harmless, value is correct.
    first = divide(7.0, 3.0, precision=2)
    second = divide(7.0, 3.0, precision=2)
    assert first == second == 2.33


def test_divide_distinct_operands() -> None:
    clear_cache()
    assert divide(9.0, 2.0, precision=1) == 4.5
    assert divide(1.0, 8.0, precision=3) == 0.125
