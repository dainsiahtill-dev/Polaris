"""Public service exports for `roles.kernel` cell."""

from __future__ import annotations

from collections.abc import Callable

from polaris.cells.roles.kernel.internal.constitution_adaptor import (
    ConstitutionalRoleContext,
    ConstitutionGuard,
    ConstitutionViolationError,
    _global_registry,
)
from polaris.cells.roles.kernel.internal.constitution_rules import (
    CONSTITUTION,
    AntiPattern,
    ConstitutionEnforcer,
    Role,
    RoleBoundary,
    ViolationLevel,
    is_action_allowed,
)
from polaris.cells.roles.kernel.internal.context_gateway import (
    ContextGatewayConfig,
    ContextRequest,
    ContextResult,
    RoleContextGateway,
)
from polaris.cells.roles.kernel.internal.context_gateway.role_signals import (
    DEFAULT_SIGNAL_PROVIDERS,
    RoleSignalRegistry,
    SignalBlock,
    SignalBuildContext,
    allocate_role_signals,
)
from polaris.cells.roles.kernel.internal.context_models import (
    ContextOverride,
    ContextStats,
    ConversationHistory,
    MemorySnippet,
    SystemContext,
    TaskContext,
)
from polaris.cells.roles.kernel.internal.error_category import (
    ErrorCategory,
    classify_error,
    get_max_retries,
    is_retryable,
)
from polaris.cells.roles.kernel.internal.events import (
    LLMCallEvent,
    LLMEventEmitter,
    LLMEventType,
    emit_llm_event,
    get_global_emitter,
)
from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.llm_cache import (
    CacheEntry,
    LLMCache,
    get_global_llm_cache,
    set_global_llm_cache,
)
from polaris.cells.roles.kernel.internal.llm_caller import (
    LLMCaller,
    LLMResponse,
    StructuredLLMResponse,
)
from polaris.cells.roles.kernel.internal.metrics import MetricsCollector
from polaris.cells.roles.kernel.internal.output_parser import (
    OutputParser,
    ThinkingResult,
    ToolCallResult,
)
from polaris.cells.roles.kernel.internal.prompt_builder import PromptBuilder, PromptContext
from polaris.cells.roles.kernel.internal.quality_checker import QualityChecker, QualityResult
from polaris.cells.roles.kernel.internal.retry_policy_engine import RetryDecision, RetryPolicyEngine
from polaris.cells.roles.kernel.internal.speculation.events import (
    SpeculationEvent,
    emit as _emit_speculation_event,
    subscribe as _subscribe_speculation_events,
)
from polaris.cells.roles.kernel.internal.token_budget import (
    AllocationResult,
    CompressionStrategy,
    TokenBudget,
    get_global_token_budget,
)
from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway, ToolAuthorizationError, ToolGatewayManager
from polaris.cells.roles.kernel.internal.transaction.write_authority import (
    extract_target_path_from_payload,
    is_authoritative_write_invocation,
    is_authoritative_write_path,
    is_authoritative_write_result,
    is_control_plane_write_path,
    normalize_write_target_path,
)
from polaris.cells.roles.kernel.public.contracts import ToolGatewayPort
from polaris.kernelone.llm.engine.token_estimator import TokenEstimator

__all__ = [
    # Constitution
    "CONSTITUTION",
    "DEFAULT_SIGNAL_PROVIDERS",
    "AllocationResult",
    "AntiPattern",
    "CacheEntry",
    "CompressionStrategy",
    "ConstitutionEnforcer",
    "ConstitutionGuard",
    "ConstitutionViolationError",
    "ConstitutionalRoleContext",
    "ContextGatewayConfig",
    "ContextOverride",
    "ContextRequest",
    "ContextResult",
    "ContextStats",
    "ConversationHistory",
    "ErrorCategory",
    "LLMCache",
    "LLMCallEvent",
    "LLMCaller",
    "LLMEventEmitter",
    "LLMEventType",
    "LLMResponse",
    "MemorySnippet",
    "OutputParser",
    "PromptBuilder",
    "PromptContext",
    "QualityChecker",
    "QualityResult",
    "RetryDecision",
    "RetryPolicyEngine",
    "Role",
    "RoleBoundary",
    "RoleContextGateway",
    "RoleExecutionKernel",
    "RoleSignalRegistry",
    "RoleToolGateway",
    "SignalBlock",
    "SignalBuildContext",
    "SpeculationEvent",
    "StructuredLLMResponse",
    "SystemContext",
    "TaskContext",
    "ThinkingResult",
    "TokenBudget",
    "TokenEstimator",
    "ToolAuthorizationError",
    "ToolCallResult",
    "ToolGatewayManager",
    "ToolGatewayPort",
    "ViolationLevel",
    "allocate_role_signals",
    "classify_error",
    "emit_llm_event",
    "extract_target_path_from_payload",
    "get_global_emitter",
    "get_global_llm_cache",
    "get_global_token_budget",
    "get_kernel_metrics_collector",
    "get_max_retries",
    "is_action_allowed",
    "is_authoritative_write_invocation",
    "is_authoritative_write_path",
    "is_authoritative_write_result",
    "is_control_plane_write_path",
    "is_retryable",
    "normalize_write_target_path",
    "publish_speculation_event",
    "reset_metrics_collector_for_test",
    "reset_role_action_registry_for_test",
    "set_global_llm_cache",
    "subscribe_speculation_events",
]


def get_kernel_metrics_collector() -> MetricsCollector:
    """Return the kernel cell's MetricsCollector singleton instance.

    This is the canonical public entry for delivery / infrastructure layers
    that need to append kernel metrics to Prometheus output.
    """
    from polaris.cells.roles.kernel.internal.metrics import get_metrics_collector

    return get_metrics_collector()


def reset_metrics_collector_for_test() -> None:
    """Reset MetricsCollector singleton and global metric objects for test isolation.

    This function clears the singleton instance and recreates all global metric
    objects to ensure a clean state between tests.
    """
    # Reset global metrics first
    MetricsCollector.reset()
    # Clear the singleton instance
    with MetricsCollector._lock:
        MetricsCollector._instance = None


def reset_role_action_registry_for_test() -> None:
    """Reset the global RoleActionRegistry for test isolation.

    This function clears all registered actions and violations from the
    _global_registry to ensure a clean state between tests.
    """
    _global_registry.reset_for_testing()


def subscribe_speculation_events(sink: Callable[[SpeculationEvent], None]) -> Callable[[], None]:
    """Subscribe to kernel speculation telemetry through the public Cell boundary."""
    return _subscribe_speculation_events(sink)


def publish_speculation_event(event: SpeculationEvent) -> None:
    """Publish kernel speculation telemetry through the public Cell boundary."""
    _emit_speculation_event(event)
