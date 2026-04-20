"""Polaris AI Platform - Unified AI Infrastructure

统一 AI 基础设施平台，提供：
- 统一调用契约 (contracts)
- 统一执行器 (executor)
- 统一流式引擎 (stream_executor)
- 统一解析器 (normalizer)
- 统一弹性策略 (resilience)
- 统一观测 (telemetry)
"""

from __future__ import annotations

# Contracts
from .contracts import (
    AIRequest,
    AIResponse,
    AIStreamEvent,
    AIStreamGenerator,
    CompressionResult,
    ErrorCategory,
    EvaluationCase,
    EvaluationReport,
    EvaluationRequest,
    EvaluationResult,
    EvaluationSuiteResult,
    ModelSpec,
    StreamEventType,
    TaskType,
    TokenBudgetDecision,
    Usage,
)

# Error Mapping
from .error_mapping import (
    KernelRepairCategory,
    NoRetryCategory,
    PlatformRetryCategory,
    get_retry_hint,
    is_kernel_repairable,
    is_platform_retryable,
    is_retryable,
    map_error_to_category,
    serialize_error,
)

# Executor
from .executor import (
    AIExecutor,
    WorkspaceExecutorManager,
    get_executor,
    get_executor_async,
    set_executor,
)

# Model Catalog and Token Budget
from .model_catalog import ModelCatalog

# Normalizer
from .normalizer import ResponseNormalizer, normalize_list, split_lines, truncate_text
from .prompt_budget import CompressionRouter, TokenBudgetManager

# Resilience
from .resilience import (
    ResilienceManager,
    RetryConfig,
    TimeoutConfig,
    TruncationConfig,
    with_resilience,
)

# Stream Executor
from .stream_executor import StreamExecutor, stream_to_response

# Telemetry
from .telemetry import (
    MetricsAggregator,
    TelemetryCollector,
    TelemetryEvent,
    create_telemetry_collector,
)
from .token_estimator import TokenEstimator, estimate_tokens

__all__ = [
    # Executor
    "AIExecutor",
    # Contracts
    "AIRequest",
    "AIResponse",
    "AIStreamEvent",
    "AIStreamGenerator",
    "CompressionResult",
    "CompressionRouter",
    "ErrorCategory",
    "EvaluationCase",
    "EvaluationReport",
    "EvaluationRequest",
    "EvaluationResult",
    "EvaluationSuiteResult",
    "KernelRepairCategory",
    # Telemetry
    "MetricsAggregator",
    # Model Catalog and Token Budget
    "ModelCatalog",
    "ModelSpec",
    "NoRetryCategory",
    # Error Mapping
    "PlatformRetryCategory",
    # Resilience
    "ResilienceManager",
    # Normalizer
    "ResponseNormalizer",
    "RetryConfig",
    "StreamEventType",
    # Stream Executor
    "StreamExecutor",
    "TaskType",
    "TelemetryCollector",
    "TelemetryEvent",
    "TimeoutConfig",
    "TokenBudgetDecision",
    "TokenBudgetManager",
    "TokenEstimator",
    "TruncationConfig",
    "Usage",
    "WorkspaceExecutorManager",
    "create_telemetry_collector",
    "estimate_tokens",
    "get_executor",
    "get_executor_async",
    "get_retry_hint",
    "is_kernel_repairable",
    "is_platform_retryable",
    "is_retryable",
    "map_error_to_category",
    "normalize_list",
    "serialize_error",
    "set_executor",
    "split_lines",
    "stream_to_response",
    "truncate_text",
    "with_resilience",
]
