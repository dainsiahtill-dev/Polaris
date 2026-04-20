"""Public boundary for `audit.evidence` cell."""

from polaris.cells.audit.evidence.public.contracts import (
    AppendEvidenceEventCommandV1,
    EvidenceAppendedEventV1,
    EvidenceAuditError,
    EvidenceQueryResultV1,
    EvidenceVerificationResultV1,
    QueryEvidenceEventsV1,
    VerifyEvidenceChainV1,
)
from polaris.cells.audit.evidence.public.service import (
    AuditLLMBindingConfig,
    EvidenceBundleService,
    EvidenceService,
    RoleSessionAuditService,
    bind_audit_llm_to_task_service,
    build_audit_llm_binding_config,
    build_error_evidence,
    build_file_evidence,
    create_evidence_bundle_service,
    detect_language,
    get_audit_role_descriptor,
)

__all__ = [
    "AppendEvidenceEventCommandV1",
    "AuditLLMBindingConfig",
    "EvidenceAppendedEventV1",
    "EvidenceAuditError",
    "EvidenceBundleService",
    "EvidenceQueryResultV1",
    "EvidenceService",
    "EvidenceVerificationResultV1",
    "QueryEvidenceEventsV1",
    "RoleSessionAuditService",
    "VerifyEvidenceChainV1",
    "bind_audit_llm_to_task_service",
    "build_audit_llm_binding_config",
    "build_error_evidence",
    "build_file_evidence",
    "create_evidence_bundle_service",
    "detect_language",
    "get_audit_role_descriptor",
]
