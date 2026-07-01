"""Architecture fence for retired audit-store KernelOne contract aliases."""

from __future__ import annotations

from pathlib import Path

import polaris.infrastructure.audit.stores as audit_stores
from polaris.infrastructure.audit.stores import audit_store

BACKEND_ROOT = Path(__file__).resolve().parents[3]
AUDIT_STORE = BACKEND_ROOT / "polaris" / "infrastructure" / "audit" / "stores" / "audit_store.py"
AUDIT_STORES_INIT = BACKEND_ROOT / "polaris" / "infrastructure" / "audit" / "stores" / "__init__.py"


def test_audit_store_kernel_contract_aliases_are_retired() -> None:
    """KernelOne audit contracts own audit event and chain result types."""
    retired_names = {
        "AuditEvent",
        "AuditEventType",
        "ChainVerificationResult",
        "audit_event_to_kernel",
        "kernel_event_to_audit",
    }

    for name in retired_names:
        assert not hasattr(audit_store, name)
        assert not hasattr(audit_stores, name)
        assert name not in audit_stores.__all__


def test_audit_store_source_does_not_reintroduce_kernel_contract_aliases() -> None:
    """Source-level fence blocks the old audit-store duplicate contract surface."""
    forbidden_snippets = (
        "AuditEvent: TypeAlias",
        "AuditEventType: TypeAlias",
        "ChainVerificationResult: TypeAlias",
        "def audit_event_to_kernel",
        "def kernel_event_to_audit",
    )
    combined_source = "\n".join(
        (
            AUDIT_STORE.read_text(encoding="utf-8"),
            AUDIT_STORES_INIT.read_text(encoding="utf-8"),
        )
    )

    for snippet in forbidden_snippets:
        assert snippet not in combined_source
