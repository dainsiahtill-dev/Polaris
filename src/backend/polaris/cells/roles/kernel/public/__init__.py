"""Public boundary for `roles.kernel` cell.

This module intentionally uses lazy re-exports so importing a narrow public
submodule such as ``turn_contracts`` does not eagerly import the heavier
``service`` boundary and its internal kernel runtime dependencies.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORT_TO_MODULE: dict[str, str] = {
    "KernelConfig": "polaris.cells.roles.kernel.public.config",
    "get_default_config": "polaris.cells.roles.kernel.public.config",
    "BuildRolePromptCommandV1": "polaris.cells.roles.kernel.public.contracts",
    "CheckRoleQualityCommandV1": "polaris.cells.roles.kernel.public.contracts",
    "ClassifyKernelErrorQueryV1": "polaris.cells.roles.kernel.public.contracts",
    "ExecuteRoleKernelTurnCommandV1": "polaris.cells.roles.kernel.public.contracts",
    "IRoleKernelService": "polaris.cells.roles.kernel.public.contracts",
    "ParseRoleOutputCommandV1": "polaris.cells.roles.kernel.public.contracts",
    "ResolveRetryPolicyQueryV1": "polaris.cells.roles.kernel.public.contracts",
    "RoleKernelError": "polaris.cells.roles.kernel.public.contracts",
    "RoleKernelParsedOutputEventV1": "polaris.cells.roles.kernel.public.contracts",
    "RoleKernelPromptBuiltEventV1": "polaris.cells.roles.kernel.public.contracts",
    "RoleKernelQualityCheckedEventV1": "polaris.cells.roles.kernel.public.contracts",
    "RoleKernelResultV1": "polaris.cells.roles.kernel.public.contracts",
    "CONSTITUTION": "polaris.cells.roles.kernel.public.service",
    "AllocationResult": "polaris.cells.roles.kernel.public.service",
    "AntiPattern": "polaris.cells.roles.kernel.public.service",
    "CacheEntry": "polaris.cells.roles.kernel.public.service",
    "CompressionStrategy": "polaris.cells.roles.kernel.public.service",
    "ConstitutionalRoleContext": "polaris.cells.roles.kernel.public.service",
    "ConstitutionEnforcer": "polaris.cells.roles.kernel.public.service",
    "ConstitutionGuard": "polaris.cells.roles.kernel.public.service",
    "ConstitutionViolationError": "polaris.cells.roles.kernel.public.service",
    "ContextOverride": "polaris.cells.roles.kernel.public.service",
    "ContextRequest": "polaris.cells.roles.kernel.public.service",
    "ContextResult": "polaris.cells.roles.kernel.public.service",
    "ContextStats": "polaris.cells.roles.kernel.public.service",
    "DeliveryMode": "polaris.cells.roles.kernel.internal.transaction.delivery_contract",
    "ConversationHistory": "polaris.cells.roles.kernel.public.service",
    "ErrorCategory": "polaris.cells.roles.kernel.public.service",
    "LLMCache": "polaris.cells.roles.kernel.public.service",
    "LLMCaller": "polaris.cells.roles.kernel.public.service",
    "LLMCallEvent": "polaris.cells.roles.kernel.public.service",
    "LLMEventEmitter": "polaris.cells.roles.kernel.public.service",
    "LLMEventType": "polaris.cells.roles.kernel.public.service",
    "LLMResponse": "polaris.cells.roles.kernel.public.service",
    "MemorySnippet": "polaris.cells.roles.kernel.public.service",
    "OutputParser": "polaris.cells.roles.kernel.public.service",
    "PromptBuilder": "polaris.cells.roles.kernel.public.service",
    "PromptContext": "polaris.cells.roles.kernel.public.service",
    "QualityChecker": "polaris.cells.roles.kernel.public.service",
    "QualityResult": "polaris.cells.roles.kernel.public.service",
    "RetryDecision": "polaris.cells.roles.kernel.public.service",
    "RetryPolicyEngine": "polaris.cells.roles.kernel.public.service",
    "Role": "polaris.cells.roles.kernel.public.service",
    "RoleBoundary": "polaris.cells.roles.kernel.public.service",
    "RoleContextGateway": "polaris.cells.roles.kernel.public.service",
    "RoleExecutionKernel": "polaris.cells.roles.kernel.public.service",
    "RoleToolGateway": "polaris.cells.roles.kernel.public.service",
    "StructuredLLMResponse": "polaris.cells.roles.kernel.public.service",
    "SystemContext": "polaris.cells.roles.kernel.public.service",
    "TaskContext": "polaris.cells.roles.kernel.public.service",
    "ThinkingResult": "polaris.cells.roles.kernel.public.service",
    "TokenBudget": "polaris.cells.roles.kernel.public.service",
    "TokenEstimator": "polaris.cells.roles.kernel.public.service",
    "ToolAuthorizationError": "polaris.cells.roles.kernel.public.service",
    "ToolCallResult": "polaris.cells.roles.kernel.public.service",
    "ToolGatewayManager": "polaris.cells.roles.kernel.public.service",
    "ViolationLevel": "polaris.cells.roles.kernel.public.service",
    "classify_error": "polaris.cells.roles.kernel.public.service",
    "emit_llm_event": "polaris.cells.roles.kernel.public.service",
    "get_global_emitter": "polaris.cells.roles.kernel.public.service",
    "get_global_llm_cache": "polaris.cells.roles.kernel.public.service",
    "get_global_token_budget": "polaris.cells.roles.kernel.public.service",
    "get_max_retries": "polaris.cells.roles.kernel.public.service",
    "is_action_allowed": "polaris.cells.roles.kernel.public.service",
    "is_retryable": "polaris.cells.roles.kernel.public.service",
    "set_global_llm_cache": "polaris.cells.roles.kernel.public.service",
    "AssistantMessage": "polaris.cells.roles.kernel.public.transcript_ir",
    "CanonicalToolCallEntry": "polaris.cells.roles.kernel.public.transcript_ir",
    "ControlEvent": "polaris.cells.roles.kernel.public.transcript_ir",
    "ControlEventType": "polaris.cells.roles.kernel.public.transcript_ir",
    "ParsedToolPlan": "polaris.cells.roles.kernel.public.transcript_ir",
    "ReasoningSummary": "polaris.cells.roles.kernel.public.transcript_ir",
    "SanitizedOutput": "polaris.cells.roles.kernel.public.transcript_ir",
    "SystemInstruction": "polaris.cells.roles.kernel.public.transcript_ir",
    "ToolCall": "polaris.cells.roles.kernel.public.transcript_ir",
    "ToolResult": "polaris.cells.roles.kernel.public.transcript_ir",
    "ToolResultStatus": "polaris.cells.roles.kernel.public.transcript_ir",
    "TranscriptAppendRequest": "polaris.cells.roles.kernel.public.transcript_ir",
    "TranscriptDelta": "polaris.cells.roles.kernel.public.transcript_ir",
    "TranscriptItem": "polaris.cells.roles.kernel.public.transcript_ir",
    "UserMessage": "polaris.cells.roles.kernel.public.transcript_ir",
    "from_assistant_message": "polaris.cells.roles.kernel.public.transcript_ir",
    "from_control_event": "polaris.cells.roles.kernel.public.transcript_ir",
    "from_tool_result": "polaris.cells.roles.kernel.public.transcript_ir",
    "get_dead_loop_metrics": "polaris.cells.roles.kernel.public.metrics_contracts",
}

__all__ = list(_EXPORT_TO_MODULE)


def __getattr__(name: str) -> Any:
    """Resolve public exports lazily to avoid import-time cycles."""
    module_name = _EXPORT_TO_MODULE.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name)
    return getattr(module, name)


def __dir__() -> list[str]:
    """Return the supported public re-export surface."""
    return sorted(__all__)
