"""KernelOne audit public exports.

Audit helpers are imported by low-level ContextOS and LLM paths, so the package
entrypoint must not eagerly load the full audit runtime dependency graph.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "Alert": "alerting",
    "AlertCondition": "alerting",
    "AlertingEngine": "alerting",
    "AlertRule": "alerting",
    "AlertSeverity": "alerting",
    "AlertStatus": "alerting",
    "AuditFieldError": "audit_field",
    "AuditIndex": "runtime",
    "DiagnosisResult": "diagnosis",
    "ErrorPattern": "diagnosis",
    "KernelAuditEvent": "contracts",
    "KernelAuditEventType": "contracts",
    "KernelAuditRole": "contracts",
    "KernelAuditRuntime": "runtime",
    "KernelAuditWriteError": "runtime",
    "KernelAuditWriteResult": "contracts",
    "KernelChainVerificationResult": "contracts",
    "SYSTEM_ROLE": "validators",
    "TypeSafeDict": "audit_field",
    "TypeSafeList": "audit_field",
    "audit_len": "audit_field",
    "audit_repr": "audit_field",
    "audit_str": "audit_field",
    "diagnose_error": "diagnosis",
    "diagnose_from_exception": "diagnosis",
    "require_valid_run_id": "validators",
    "safe_value": "audit_field",
    "validate_run_id": "validators",
}

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


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value
