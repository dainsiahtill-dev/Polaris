"""Audit storage implementations.

[P1-AUDIT-001] Converged to canonical types from polaris.kernelone.audit.contracts:
- KernelAuditEvent, KernelAuditEventType, KernelChainVerificationResult
- AuditEvent, AuditEventType, ChainVerificationResult are backward-compatible aliases
"""

from polaris.infrastructure.audit.stores.audit_store import (
    AuditEvent,
    AuditEventResult,
    AuditEventType,
    AuditRole,
    AuditStore,
    ChainVerificationResult,
    ResourceOperation,
    ResourceType,
    audit_event_to_kernel,
    create_audit_event,
    kernel_event_to_audit,
)
from polaris.infrastructure.audit.stores.evidence_store import (
    EvidenceNotFoundError,
    EvidenceStore,
)
from polaris.infrastructure.audit.stores.log_store import LogStore
from polaris.kernelone.audit.contracts import (
    KernelAuditEvent,
    KernelAuditEventType,
    KernelChainVerificationResult,
)

__all__ = [
    # Canonical types (preferred)
    "KernelAuditEvent",
    "KernelAuditEventType",
    "KernelChainVerificationResult",
    # Backward compatibility aliases
    "AuditEvent",
    "AuditEventType",
    "ChainVerificationResult",
    # Legacy enums (for external callers)
    "AuditEventResult",
    "AuditRole",
    "ResourceOperation",
    "ResourceType",
    # Store implementation
    "AuditStore",
    # Supporting
    "EvidenceNotFoundError",
    "EvidenceStore",
    "LogStore",
    # Adapter functions
    "audit_event_to_kernel",
    "create_audit_event",
    "kernel_event_to_audit",
]
