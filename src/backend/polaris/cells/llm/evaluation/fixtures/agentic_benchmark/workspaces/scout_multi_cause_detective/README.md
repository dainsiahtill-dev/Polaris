# scout_multi_cause_detective fixture

A division calculator with a memoization layer. There is a single reported bug:
`integration_test.py` fails with an output mismatch while
`tests/test_calculator.py` passes.

Multiple files look like plausible causes:

- `src/calculator.py` -- the division entry point (does correct math; trusts the cache).
- `src/config.py` -- loads float precision from `CALC_PRECISION`, defaults LOW (2),
  and carries a misleading `TODO: precision may be wrong`. **Red herring.**
- `src/cache.py` -- memoizes division results. Its memo key omits `precision`,
  so same operands at a different precision return a stale value. **True root cause.**

The unit suite never varies precision for the same operands, so it stays green.
The integration test divides the same operands at two precisions, triggering the
stale cache key.
