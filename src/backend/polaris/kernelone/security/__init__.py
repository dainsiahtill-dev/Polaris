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
    CommandAuditResult,
    CommandAuditor,
    SeverityLevel,
)
from polaris.kernelone.security.dangerous_patterns import (
    is_dangerous,
    is_dangerous_command,
    is_path_traversal,
)
from polaris.kernelone.security.guardrails import GuardrailsChain
from polaris.kernelone.security.rate_limiter import RateLimiter
from polaris.kernelone.security.sanitizer import InputSanitizer

__all__ = [
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
]
