"""Arithmetic calculator with memoized division.

The public ``divide`` entry point delegates to a memoizing cache layer so
repeated divisions are cheap. Callers pass an explicit ``precision`` so the
quotient is rounded consistently across the application.

NOTE FOR DEBUGGERS: this file is intentionally a *plausible suspect* but is
NOT the defect. The division math here is correct; it simply trusts whatever
the cache layer hands back.
"""

from __future__ import annotations

from src.cache import cached_divide
from src.config import load_precision


def divide(numerator: float, denominator: float, precision: int | None = None) -> float:
    """Divide ``numerator`` by ``denominator`` rounded to ``precision`` digits.

    When ``precision`` is omitted the value is loaded from configuration. The
    actual quotient is produced by :func:`src.cache.cached_divide`, which
    memoizes results to avoid recomputing hot divisions.
    """
    if denominator == 0:
        raise ZeroDivisionError("denominator must be non-zero")
    effective_precision = precision if precision is not None else load_precision()
    quotient = cached_divide(numerator, denominator, effective_precision)
    return round(quotient, effective_precision)
