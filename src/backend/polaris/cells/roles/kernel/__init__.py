"""Entry for `roles.kernel` cell.

This package re-exports the public boundary lazily so importing a narrow
submodule such as ``polaris.cells.roles.kernel.public.turn_contracts`` does not
eagerly import the heavy kernel service/runtime graph.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AllocationResult",
    "BuildRolePromptCommandV1",
    "CacheEntry",
    "CheckRoleQualityCommandV1",
    "ClassifyKernelErrorQueryV1",
    "CompressionStrategy",
    "ContextOverride",
    "ContextRequest",
    "ContextResult",
    "ContextStats",
    "ConversationHistory",
    "ErrorCategory",
    "ExecuteRoleKernelTurnCommandV1",
    "IRoleKernelService",
    "LLMCache",
    "LLMCallEvent",
    "LLMCaller",
    "LLMEventEmitter",
    "LLMEventType",
    "LLMResponse",
    "MemorySnippet",
    "OutputParser",
    "ParseRoleOutputCommandV1",
    "PromptBuilder",
    "PromptContext",
    "QualityChecker",
    "QualityResult",
    "ResolveRetryPolicyQueryV1",
    "RetryDecision",
    "RetryPolicyEngine",
    "RoleContextGateway",
    "RoleExecutionKernel",
    "RoleKernelError",
    "RoleKernelParsedOutputEventV1",
    "RoleKernelPromptBuiltEventV1",
    "RoleKernelQualityCheckedEventV1",
    "RoleKernelResultV1",
    "RoleToolGateway",
    "StructuredLLMResponse",
    "SystemContext",
    "TaskContext",
    "ThinkingResult",
    "TokenBudget",
    "TokenEstimator",
    "ToolAuthorizationError",
    "ToolCallResult",
    "ToolGatewayManager",
    "classify_error",
    "emit_llm_event",
    "get_global_emitter",
    "get_global_llm_cache",
    "get_global_token_budget",
    "get_max_retries",
    "is_retryable",
    "set_global_llm_cache",
]


def __getattr__(name: str) -> Any:
    """Resolve public exports lazily from the canonical public package."""
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from polaris.cells.roles.kernel import public as kernel_public

    return getattr(kernel_public, name)


def __dir__() -> list[str]:
    """Return the supported lazy re-export surface."""
    return sorted(__all__)
