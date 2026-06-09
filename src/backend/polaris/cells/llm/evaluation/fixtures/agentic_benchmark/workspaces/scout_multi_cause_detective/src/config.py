"""Configuration loader for the calculator.

Reads the float precision used when rounding quotients. The default is
intentionally LOW (2 digits) which makes this module look suspicious during a
mismatch investigation.

RED HERRING: the TODO below is a deliberate decoy. The precision value is read
and applied correctly; a low default merely rounds more aggressively, it does
NOT cause the integration mismatch. Do not name this file as the root cause.
"""

from __future__ import annotations

import os

# Low default precision -- looks like it could be the culprit, but isn't.
DEFAULT_PRECISION = 2

# TODO: precision may be wrong -- revisit the default someday.
# (This TODO is a known decoy left from an old refactor; it is unrelated to the
#  integration_test.py failure.)


def load_precision() -> int:
    """Return the configured rounding precision.

    Reads ``CALC_PRECISION`` from the environment, falling back to
    :data:`DEFAULT_PRECISION` when it is unset or non-numeric.
    """
    raw = os.environ.get("CALC_PRECISION")
    if raw is None:
        return DEFAULT_PRECISION
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_PRECISION
