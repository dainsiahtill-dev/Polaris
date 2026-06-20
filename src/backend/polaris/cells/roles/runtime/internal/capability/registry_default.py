"""The default, process-wide :class:`CapabilityHandlerRegistry`.

``default_capability_registry`` assembles every migrated capability handler into
one immutable, identity-keyed registry and caches it for the process. The
dispatcher falls back to this registry when no explicit ``handlers`` registry is
injected, so each migrated family short-circuits the legacy ``if/elif`` ladder.

As Phase 3 fans out, every new handler registers its identity tuple(s) here in
:data:`_HANDLER_BINDINGS` — one entry per ``(capability_id, owner_cell,
command_contract)`` triple the handler answers (a single handler may answer more
than one triple, e.g. the dual-id blueprint family).
"""

from __future__ import annotations

from functools import lru_cache

from polaris.cells.roles.runtime.internal.capability.handlers import DirectorExecutionHandler
from polaris.cells.roles.runtime.internal.capability.protocol import CapabilityHandler
from polaris.cells.roles.runtime.internal.capability.registry import (
    CapabilityHandlerRegistry,
    CapabilityIdentity,
)


def _handler_bindings() -> dict[CapabilityIdentity, CapabilityHandler]:
    """Return the identity-tuple -> handler bindings for all migrated families.

    Each migrated handler is instantiated once and bound to every identity triple
    it answers. Phase 3 appends additional families here.
    """
    director_execution_handler: CapabilityHandler = DirectorExecutionHandler()
    bindings: dict[CapabilityIdentity, CapabilityHandler] = {
        # director_task_execution
        ("execute_director_task", "director.execution", "ExecuteDirectorTaskCommandV1"): (director_execution_handler),
    }
    return bindings


@lru_cache(maxsize=1)
def default_capability_registry() -> CapabilityHandlerRegistry:
    """Return the cached, process-wide registry of all migrated handlers."""
    return CapabilityHandlerRegistry.from_handlers(_handler_bindings())
