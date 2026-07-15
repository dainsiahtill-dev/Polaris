"""Delivery-neutral workspace bootstrap application service for FactStream."""

from __future__ import annotations

from .contracts import (
    BootstrapFactStreamWorkspaceCommandV1,
    EnrollFactStreamStreamsCommandV1,
    FactStreamMaintenanceReceiptV1,
    ProvisionFactStreamLockAuthorityCommandV1,
)
from .service import enroll_fact_stream_streams, provision_fact_stream_lock_authority


def bootstrap_fact_stream_workspace(
    command: BootstrapFactStreamWorkspaceCommandV1,
) -> FactStreamMaintenanceReceiptV1:
    """Provision one workspace authority and enroll its declared static catalog.

    The service retains no process-local completion cache. The persistent
    authority's exclusive anchor serializes concurrent bootstrap requests, so a
    repeated call proves the current physical binding instead of trusting stale
    in-memory state.
    """

    provision = provision_fact_stream_lock_authority(
        ProvisionFactStreamLockAuthorityCommandV1(
            workspace=command.workspace,
            maintenance_reason=command.maintenance_reason,
            platform_lock_root=command.platform_lock_root,
        )
    )
    enrollment = enroll_fact_stream_streams(
        EnrollFactStreamStreamsCommandV1(
            workspace=command.workspace,
            streams=command.streams,
            maintenance_reason=command.maintenance_reason,
            platform_lock_root=command.platform_lock_root,
        )
    )
    return FactStreamMaintenanceReceiptV1(
        workspace=enrollment.workspace,
        storage_identity_token=enrollment.storage_identity_token,
        maintenance_reason=command.maintenance_reason,
        operation="bootstrap_workspace",
        streams=enrollment.streams,
        proofs=provision.proofs + enrollment.proofs,
    )


__all__ = ["bootstrap_fact_stream_workspace"]
