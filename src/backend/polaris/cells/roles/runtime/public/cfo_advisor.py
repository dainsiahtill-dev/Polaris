"""Optional CFO role advisor; deterministic budgets remain owned by FinOps.

The historical FinOps CFOAgent import paths moved here so importing the
budget domain never needs the role runtime. The role keeps its lifecycle,
queue and budget tools and is not part of the default delivery role chain.
"""

from __future__ import annotations

from ..internal.cfo_advisor import CFOAgent

__all__ = ["CFOAgent"]
