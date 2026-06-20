"""Frozen identity-keyed registry of capability handlers.

The dispatcher reconstructs a capability's identity from thirteen hand-written
``is_*`` boolean flags over the triple ``(capability_id, owner_cell,
command_contract)``. ``CapabilityHandlerRegistry`` replaces that reconstruction
with a single immutable lookup table keyed on exactly that triple.

A handler may be registered under more than one identity tuple — e.g. the
``chief_engineer.blueprint`` blueprint-generation branch accepts both the
``generate_diff_specification`` and ``record_arch_memo`` capability ids for the
same owner cell + contract, so its handler is bound to two identity tuples.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.cells.roles.runtime.internal.capability.protocol import CapabilityHandler

CapabilityIdentity = tuple[str, str, str]
"""``(capability_id, owner_cell, command_contract)`` — the dispatcher's branch key."""


@dataclass(frozen=True)
class CapabilityHandlerRegistry:
    """Immutable ``CapabilityIdentity`` -> :class:`CapabilityHandler` table."""

    _handlers: Mapping[CapabilityIdentity, CapabilityHandler]

    @classmethod
    def from_handlers(
        cls,
        handlers: Mapping[CapabilityIdentity, CapabilityHandler],
    ) -> CapabilityHandlerRegistry:
        """Build a registry from a mapping, snapshotting it into an immutable dict."""
        return cls(dict(handlers))

    def lookup(
        self,
        capability_id: str,
        owner_cell: str,
        command_contract: str,
    ) -> CapabilityHandler | None:
        """Return the handler for an identity triple, or ``None`` when unregistered."""
        return self._handlers.get((capability_id, owner_cell, command_contract))

    def identities(self) -> tuple[CapabilityIdentity, ...]:
        """Return every registered identity triple (sorted, for stable iteration)."""
        return tuple(sorted(self._handlers))
