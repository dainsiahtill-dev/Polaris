"""Compose audit.evidence receipt ownership into Run Ledger projection."""

from __future__ import annotations

from polaris.cells.audit.evidence.public.contracts import ReadManagedProcessReceiptQueryV1
from polaris.cells.audit.evidence.public.service import read_managed_process_receipt
from polaris.cells.control_plane.run_ledger.public.managed_process_lifecycle import (
    ManagedProcessReceiptOwnerRecordV1,
)
from polaris.cells.control_plane.run_ledger.public.managed_process_lifecycle_bootstrap import (
    bind_managed_process_receipt_owner_port,
)


class _AuditEvidenceManagedProcessReceiptOwnerV1:
    def read_managed_process_receipt(
        self,
        *,
        workspace: str,
        receipt_hash: str,
    ) -> ManagedProcessReceiptOwnerRecordV1 | None:
        record = read_managed_process_receipt(
            ReadManagedProcessReceiptQueryV1(
                workspace=workspace,
                receipt_hash=receipt_hash,
            )
        )
        if record is None:
            return None
        return ManagedProcessReceiptOwnerRecordV1(
            receipt_ref=record.receipt_ref,
            receipt_hash=record.receipt_hash,
            receipt=record.receipt,
        )


_OWNER = _AuditEvidenceManagedProcessReceiptOwnerV1()


def configure_managed_process_receipt_owner() -> None:
    """Bind one stateless owner adapter for process lifetime."""

    bind_managed_process_receipt_owner_port(_OWNER)


__all__ = ["configure_managed_process_receipt_owner"]
