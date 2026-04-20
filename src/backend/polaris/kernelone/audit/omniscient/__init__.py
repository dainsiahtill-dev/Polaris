"""Omniscient Audit - 全知之眼审计核心.

Non-invasive, async-first audit bus with priority queuing and back-pressure.
Builds on KernelAuditRuntime, TypedEvents, and UEP infrastructure.
"""

from polaris.kernelone.audit.omniscient.adapters import (
    JaegerExporter,
    OpenTelemetryExporter,
    StorageTierAdapter,
)
from polaris.kernelone.audit.omniscient.bus import (
    AuditDegradationLevel,
    AuditEventEnvelope,
    AuditPriority,
    OmniscientAuditBus,
)
from polaris.kernelone.audit.omniscient.context import (
    AuditContext,
    ThreadAuditContextManager,
    audit_context,
    audit_context_manager,
    clear_audit_context,
    clear_thread_audit_context,
    get_current_audit_context,
    get_thread_audit_context,
    set_audit_context,
    set_thread_audit_context,
)
from polaris.kernelone.audit.omniscient.interceptors import (
    AuditAlertInterceptor,
    TracingAuditInterceptor,
)
from polaris.kernelone.audit.omniscient.metrics import (
    AuditMetricsCollector,
    get_metrics_collector,
    get_unified_prometheus_metrics,
)
from polaris.kernelone.audit.omniscient.redaction import (
    REDACTED_PLACEHOLDER,
    SensitiveFieldRedactor,
    get_default_redactor,
    redact_sensitive_data,
    reset_default_redactor,
)
from polaris.kernelone.audit.omniscient.storm_detector import (
    AuditStormDetector,
    StormLevel,
)

__all__ = [
    "REDACTED_PLACEHOLDER",
    # Interceptors
    "AuditAlertInterceptor",
    # Context
    "AuditContext",
    "AuditDegradationLevel",
    "AuditEventEnvelope",
    # Metrics
    "AuditMetricsCollector",
    "AuditPriority",
    # Storm detection
    "AuditStormDetector",
    "JaegerExporter",
    # Bus
    "OmniscientAuditBus",
    "OpenTelemetryExporter",
    # Redaction
    "SensitiveFieldRedactor",
    # Storage & Export
    "StorageTierAdapter",
    "StormLevel",
    "ThreadAuditContextManager",
    "TracingAuditInterceptor",
    "audit_context",
    "audit_context_manager",
    "clear_audit_context",
    "clear_thread_audit_context",
    "get_current_audit_context",
    "get_default_redactor",
    "get_metrics_collector",
    "get_thread_audit_context",
    "get_unified_prometheus_metrics",
    "redact_sensitive_data",
    "reset_default_redactor",
    "set_audit_context",
    "set_thread_audit_context",
]
