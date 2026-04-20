"""KernelAuditRuntime adapters for OmniscientAuditBus.

This module provides adapters that bridge the OmniscientAuditBus to the
existing KernelAuditRuntime infrastructure with:
- Async batched writes for high-throughput scenarios
- Sanitization hooks for PII protection
- Circuit breaker pattern for fault tolerance
- File partitioning for query efficiency
- Cold/hot storage tiering
- OpenTelemetry/Jaeger trace export
"""

from __future__ import annotations

from polaris.kernelone.audit.omniscient.adapters.kernel_runtime_adapter import (
    KernelRuntimeAdapter,
    get_default_adapter,
    reset_default_adapter,
)
from polaris.kernelone.audit.omniscient.adapters.otel_exporter import (
    DEFAULT_JAEGER_ENDPOINT,
    DEFAULT_OTEL_ENDPOINT,
    DEFAULT_SERVICE_NAME,
    JaegerExporter,
    OpenTelemetryExporter,
)
from polaris.kernelone.audit.omniscient.adapters.sanitization_hook import (
    SanitizationConfig,
    SanitizationHook,
    get_default_sanitizer,
)
from polaris.kernelone.audit.omniscient.adapters.storage_tier_adapter import (
    DEFAULT_COLD_TTL_DAYS,
    DEFAULT_HOT_TTL_DAYS,
    StorageTierAdapter,
)

__all__ = [
    "DEFAULT_COLD_TTL_DAYS",
    "DEFAULT_HOT_TTL_DAYS",
    "DEFAULT_JAEGER_ENDPOINT",
    "DEFAULT_OTEL_ENDPOINT",
    "DEFAULT_SERVICE_NAME",
    "JaegerExporter",
    # Runtime adapter
    "KernelRuntimeAdapter",
    # OTEL / Jaeger
    "OpenTelemetryExporter",
    "SanitizationConfig",
    # Sanitization
    "SanitizationHook",
    # Storage tier
    "StorageTierAdapter",
    "get_default_adapter",
    "get_default_sanitizer",
    "reset_default_adapter",
]
