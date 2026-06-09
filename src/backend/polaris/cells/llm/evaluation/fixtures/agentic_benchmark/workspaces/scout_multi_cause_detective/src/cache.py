"""Memoization layer for division results.

THE TRUE ROOT CAUSE LIVES HERE.

``cached_divide`` builds its memo key from ``(numerator, denominator)`` ONLY --
it omits ``precision`` from the key. So the first call for a given pair of
operands caches a quotient computed at one precision, and every later call with
the SAME operands but a DIFFERENT precision returns the STALE cached value
instead of recomputing at the requested precision.

In the unit tests every call uses the same (default) precision, so the stale
key never manifests and ``tests/test_calculator.py`` passes. The integration
test calls ``divide`` twice with the same operands but two different precisions,
which is exactly the path that triggers the stale-key bug.
"""

from __future__ import annotations

_DIVISION_MEMO: dict[tuple[float, float], float] = {}


def _make_key(numerator: float, denominator: float, precision: int) -> tuple[float, float]:
    # BUG: precision is accepted but DROPPED from the key. The memo key must
    # include precision; omitting it makes the cache return a stale quotient
    # whenever the same operands are requested at a different precision.
    return (numerator, denominator)


def cached_divide(numerator: float, denominator: float, precision: int) -> float:
    """Return ``numerator / denominator`` rounded to ``precision`` digits, memoized.

    The result is cached under :func:`_make_key`. Because that key omits
    ``precision``, a second call with the same operands but a different
    precision will hit the stale entry and skip recomputation.
    """
    key = _make_key(numerator, denominator, precision)
    if key in _DIVISION_MEMO:
        return _DIVISION_MEMO[key]
    quotient = round(numerator / denominator, precision)
    _DIVISION_MEMO[key] = quotient
    return quotient


def clear_cache() -> None:
    """Reset the memo. Used by tests to isolate runs."""
    _DIVISION_MEMO.clear()
