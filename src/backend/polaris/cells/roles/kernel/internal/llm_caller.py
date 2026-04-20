"""LLM Caller (Facade).

This file preserves backward compatibility. The actual implementation
has been migrated to the llm_caller/ subpackage.

Migration completed: 2026-03-31
New structure:
  - llm_caller/invoker.py: LLMInvoker service class (recommended)
  - llm_caller/caller.py: LLMCaller facade (deprecated)
  - llm_caller/response_types.py: Response types
  - llm_caller/provider_formatter.py: Provider formatters
  - llm_caller/stream_handler.py: Stream processing
  - llm_caller/error_handling.py: Error handling
  - llm_caller/helpers.py: Helper functions

Deprecation Notice:
    LLMCaller is deprecated. Use LLMInvoker directly for new code.
    The following standalone modules have been removed:
    - call_sync.py -> Use LLMInvoker.call()
    - call_structured.py -> Use LLMInvoker.call_structured()
    - call_stream.py -> Use LLMInvoker.call_stream()
"""

import warnings

# Re-export all public classes and functions for backward compatibility
from .llm_caller import (
    AnnotatedProviderFormatter,
    # Core facade (deprecated, maintained for backward compatibility)
    LLMCaller,
    # Core service (recommended for new code)
    LLMInvoker,
    # Response types
    LLMResponse,
    NativeProviderFormatter,
    NormalizedStreamEvent,
    PreparedLLMRequest,
    # Provider formatter
    ProviderFormatter,
    StructuredLLMResponse,
    build_native_tool_schemas,
    build_native_tool_unavailable_error,
    build_stream_slo_metrics,
    classify_error,
    # Functions
    create_formatter,
    extract_native_tool_calls,
    is_native_tool_calling_unsupported,
    is_response_format_unsupported,
    is_retryable_error,
    is_stream_cancel_requested,
    messages_to_input,
    normalize_stream_chunk,
    resolve_stream_runtime_config,
    resolve_timeout_seconds,
)

# Emit deprecation warning for direct imports from this module
warnings.warn(
    "Importing from polaris.cells.roles.kernel.internal.llm_caller is deprecated. "
    "Import from polaris.cells.roles.kernel.internal.llm_caller directly instead. "
    "Use LLMInvoker for new code.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "AnnotatedProviderFormatter",
    # Legacy facade (deprecated)
    "LLMCaller",
    # Core service (recommended)
    "LLMInvoker",
    # Response types
    "LLMResponse",
    "NativeProviderFormatter",
    "NormalizedStreamEvent",
    "PreparedLLMRequest",
    # Provider formatter
    "ProviderFormatter",
    "StructuredLLMResponse",
    "build_native_tool_schemas",
    "build_native_tool_unavailable_error",
    "build_stream_slo_metrics",
    # Error handling
    "classify_error",
    "create_formatter",
    "extract_native_tool_calls",
    "is_native_tool_calling_unsupported",
    "is_response_format_unsupported",
    "is_retryable_error",
    "is_stream_cancel_requested",
    # Helpers
    "messages_to_input",
    # Stream handler
    "normalize_stream_chunk",
    "resolve_stream_runtime_config",
    "resolve_timeout_seconds",
]
