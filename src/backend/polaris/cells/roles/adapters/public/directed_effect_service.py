"""Public consume-only physical mutation factory for DEO-2B."""

from __future__ import annotations

from polaris.cells.director.runtime.public import DirectorEffectPolicySnapshotPortV1
from polaris.cells.roles.kernel.public import (
    DirectedEffectFenceConsumePortV1,
    DirectedEffectMutationPortV1,
)


def create_director_directed_effect_mutation_port(
    *,
    workspace: str,
    policy_snapshot_port: DirectorEffectPolicySnapshotPortV1,
    fence_consume_port: DirectedEffectFenceConsumePortV1,
) -> DirectedEffectMutationPortV1:
    """Create one mutation port without exposing its physical executor type."""

    from polaris.cells.roles.adapters.internal.director.directed_effect_mutation_port import (
        create_director_directed_effect_mutation_port as _create_private_port,
    )

    return _create_private_port(
        workspace=workspace,
        policy_snapshot_port=policy_snapshot_port,
        fence_consume_port=fence_consume_port,
    )
