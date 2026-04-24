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
    "AuditEvent",
    "AuditEventResult",
    "AuditEventType",
    "AuditRole",
    "AuditStore",
    "ChainVerificationResult",
    "EvidenceNotFoundError",
    "EvidenceStore",
    "KernelAuditEvent",
    "KernelAuditEventType",
    "KernelChainVerificationResult",
    "LogStore",
    "ResourceOperation",
    "ResourceType",
    "audit_event_to_kernel",
    "create_audit_event",
    "kernel_event_to_audit",
]
