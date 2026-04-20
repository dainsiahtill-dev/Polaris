"""KernelOne audit public exports."""

from .alerting import (
    Alert,
    AlertCondition,
    AlertingEngine,
    AlertRule,
    AlertSeverity,
    AlertStatus,
)
from .audit_field import (
    AuditFieldError,
    TypeSafeDict,
    TypeSafeList,
    audit_len,
    audit_repr,
    audit_str,
    safe_value,
)
from .contracts import (
    KernelAuditEvent,
    KernelAuditEventType,
    KernelAuditRole,
    KernelAuditWriteResult,
    KernelChainVerificationResult,
)
from .diagnosis import (
    DiagnosisResult,
    ErrorPattern,
    diagnose_error,
    diagnose_from_exception,
)
from .runtime import AuditIndex, KernelAuditRuntime, KernelAuditWriteError
from .validators import SYSTEM_ROLE, require_valid_run_id, validate_run_id

__all__ = [
    "SYSTEM_ROLE",
    # Alerting
    "Alert",
    "AlertCondition",
    "AlertRule",
    "AlertSeverity",
    "AlertStatus",
    "AlertingEngine",
    # Audit field
    "AuditFieldError",
    "AuditIndex",
    "DiagnosisResult",
    "ErrorPattern",
    # Contracts
    "KernelAuditEvent",
    "KernelAuditEventType",
    "KernelAuditRole",
    # Runtime
    "KernelAuditRuntime",
    "KernelAuditWriteError",
    "KernelAuditWriteResult",
    "KernelChainVerificationResult",
    "TypeSafeDict",
    "TypeSafeList",
    "audit_len",
    "audit_repr",
    "audit_str",
    # Diagnosis
    "diagnose_error",
    "diagnose_from_exception",
    "require_valid_run_id",
    # Validators
    "safe_value",
    "validate_run_id",
]
