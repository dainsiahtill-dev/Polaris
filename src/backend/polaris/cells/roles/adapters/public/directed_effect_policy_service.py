"""Public factory for the adapter-owned DEO-2B policy snapshot port."""

from __future__ import annotations

from polaris.cells.director.runtime.public import DirectorEffectPolicySnapshotPortV1


def create_director_effect_policy_snapshot_port(workspace: str) -> DirectorEffectPolicySnapshotPortV1:
    """Create the evidence-only policy port for one canonical workspace."""
    from polaris.cells.roles.adapters.internal.director.directed_effect_policy_snapshot import (
        _DirectorEffectPolicySnapshotPort,
    )

    return _DirectorEffectPolicySnapshotPort(workspace)
