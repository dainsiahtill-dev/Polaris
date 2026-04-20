"""Public service exports for `audit.evidence` cell."""

from __future__ import annotations

from polaris.cells.audit.evidence.bundle_service import EvidenceBundleService, create_evidence_bundle_service
from polaris.cells.audit.evidence.internal.role_session_audit_service import (
    RoleSessionAuditService,
)
from polaris.cells.audit.evidence.internal.task_audit_llm_binding import (
    AuditLLMBindingConfig,
    bind_audit_llm_to_task_service,
    build_audit_llm_binding_config,
    get_audit_role_descriptor,
)
from polaris.cells.audit.evidence.task_service import (
    EvidenceService,
    build_error_evidence,
    build_file_evidence,
    detect_language,
)

__all__ = [
    "AuditLLMBindingConfig",
    "EvidenceBundleService",
    "EvidenceService",
    "RoleSessionAuditService",
    "bind_audit_llm_to_task_service",
    "build_audit_llm_binding_config",
    "build_error_evidence",
    "build_file_evidence",
    "create_evidence_bundle_service",
    "detect_language",
    "get_audit_role_descriptor",
]
