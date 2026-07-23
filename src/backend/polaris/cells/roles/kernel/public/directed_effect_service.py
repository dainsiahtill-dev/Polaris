"""Public composition factory for an isolated directed-effect dispatch fence."""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.directed_effect_dispatch import (
    create_directed_effect_fence_ports as _create_directed_effect_fence_ports,
)
from polaris.cells.roles.kernel.public.directed_effect_contracts import DirectedEffectFencePortsV1


def create_directed_effect_fence_ports() -> DirectedEffectFencePortsV1:
    """Create a new fence with disjoint kernel-admin and consume-only views.

    Each call owns fresh private state.  The factory cannot reveal or attach to
    another runtime's live fence; the composition root must pass only the
    consume view to an adapter.
    """

    return _create_directed_effect_fence_ports()


__all__ = ["create_directed_effect_fence_ports"]
