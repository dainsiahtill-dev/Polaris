"""Concrete :class:`CapabilityHandler` implementations, one per capability family.

Each handler module owns its family's payload validation, its function-local lazy
import of its owner cell's public contract, and its result mapping. The legacy
``if/elif`` branch body of ``execute_role_capability_invocation`` moves here
VERBATIM, re-shaped onto the three-method ``validate``/``invoke``/``map_result``
surface — no logic is rewritten.

Phase 2 lands the first family (``director_task_execution``); Phase 3 fans out
the remaining twelve, one module per commit.
"""

from __future__ import annotations

from polaris.cells.roles.runtime.internal.capability.handlers.director_execution_handler import (
    DirectorExecutionHandler,
)

__all__ = [
    "DirectorExecutionHandler",
]
