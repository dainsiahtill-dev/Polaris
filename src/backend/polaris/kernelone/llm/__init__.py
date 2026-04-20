"""KernelOne LLM runtime exports."""

from . import (
    tools,
    types,  # Re-export for infrastructure compatibility
)
from .exceptions import (
    AuthenticationError,
    BudgetExceededError,
    CircuitBreakerOpenError,
    ConfigMigrationError,
    ConfigurationError,
    ConfigValidationError,
    JSONParseError,
    LLMError,
    LLMTimeoutError,
    NetworkError,
    ProviderError,
    RateLimitError,
    ResponseParseError,
    ToolExecutionError,
    ToolParseError,
    config_loading_context,
    is_retryable,
    json_parsing_context,
    tool_execution_context,
    wrap_tool_result_error,
)
from .provider_contract import KernelLLMRuntimeAdapter, RuntimeProviderInvokeResult
from .runtime import (
    KernelLLM,
    invoke_role_runtime_provider,
    normalize_provider_type,
    resolve_provider_api_key,
)
from .types import InvokeResult, Usage  # Explicit re-export for infrastructure compatibility

__all__ = [
    "AuthenticationError",
    "BudgetExceededError",
    "CircuitBreakerOpenError",
    "ConfigMigrationError",
    "ConfigValidationError",
    "ConfigurationError",
    "JSONParseError",
    # Runtime
    "KernelLLM",
    "KernelLLMRuntimeAdapter",
    # Exceptions
    "LLMError",
    "NetworkError",
    "ProviderError",
    "RateLimitError",
    "ResponseParseError",
    "RuntimeProviderInvokeResult",
    "LLMTimeoutError",
    "ToolExecutionError",
    "ToolParseError",
    "config_loading_context",
    "invoke_role_runtime_provider",
    # Types (for infrastructure compatibility)
    "InvokeResult",
    "Usage",
    # Utilities
    "is_retryable",
    "json_parsing_context",
    "normalize_provider_type",
    "resolve_provider_api_key",
    # Context managers
    "tool_execution_context",
    "tools",
    "wrap_tool_result_error",
]
