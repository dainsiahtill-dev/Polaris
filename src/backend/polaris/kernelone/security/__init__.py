"""KernelOne Security Module.

Unified security utilities for dangerous command detection.
"""

from polaris.kernelone.security.aegis_restore import PIIReversibleMasker
from polaris.kernelone.security.audit import (
    SecurityAuditor,
    SecurityAuditResult,
    Vulnerability,
    VulnerabilityCategory,
    VulnerabilitySeverity,
)
from polaris.kernelone.security.command_auditor import (
    AuditConfig,
    CommandAuditEvent,
    CommandAuditor,
    CommandAuditResult,
    SeverityLevel,
)
from polaris.kernelone.security.dangerous_patterns import (
    is_dangerous,
    is_dangerous_command,
    is_path_traversal,
)
from polaris.kernelone.security.guardrails import GuardrailsChain
from polaris.kernelone.security.rate_limiter import RateLimiter
from polaris.kernelone.security.record_id_guard import (
    SAFE_RECORD_ID_PATTERN,
    is_safe_record_id,
    validate_storage_record_id,
)
from polaris.kernelone.security.sanitizer import InputSanitizer

__all__ = [
    "SAFE_RECORD_ID_PATTERN",
    "AuditConfig",
    "CommandAuditEvent",
    "CommandAuditResult",
    "CommandAuditor",
    "GuardrailsChain",
    "InputSanitizer",
    "PIIReversibleMasker",
    "RateLimiter",
    "SecurityAuditResult",
    "SecurityAuditor",
    "SeverityLevel",
    "Vulnerability",
    "VulnerabilityCategory",
    "VulnerabilitySeverity",
    "is_dangerous",
    "is_dangerous_command",
    "is_path_traversal",
    "is_safe_record_id",
    "validate_storage_record_id",
]
