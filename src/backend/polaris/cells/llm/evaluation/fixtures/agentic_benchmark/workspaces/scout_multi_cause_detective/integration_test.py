"""Integration test that FAILS with an output mismatch.

This is the reproduction of the reported bug. It divides the SAME operands
twice but requests two DIFFERENT precisions. Because ``src/cache.py`` builds
its memo key from the operands only (dropping precision), the second call
returns the STALE quotient cached from the first call, so the high-precision
expectation fails.

Observed failure (illustrative):

    AssertionError: output mismatch: expected 2.333 but got 2.33
    (the second call reused the precision=2 result cached under the same operands)

Note: this fails REGARDLESS of the config precision default. Forcing
CALC_PRECISION higher does not help, because the first cached value -- whatever
its precision -- is what every later same-operand call receives.
"""

from __future__ import annotations

from src.cache import clear_cache
from src.calculator import divide


def test_same_operands_different_precision_should_not_collide() -> None:
    clear_cache()
    low = divide(7.0, 3.0, precision=2)  # caches 2.33 under (7.0, 3.0)
    high = divide(7.0, 3.0, precision=3)  # EXPECTED 2.333, but stale cache returns 2.33

    assert low == 2.33
    # This assertion fails: high is 2.33 (stale), not 2.333.
    assert high == 2.333, f"output mismatch: expected 2.333 but got {high}"


if __name__ == "__main__":
    test_same_operands_different_precision_should_not_collide()
