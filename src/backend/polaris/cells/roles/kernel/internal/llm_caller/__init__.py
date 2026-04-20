"""LLM Caller Package.

This package provides LLM invocation capabilities with the following modules:

- caller.py: Core LLMCaller class (deprecated, use LLMInvoker)
- invoker.py: LLMInvoker service class (recommended)
- response_types.py: LLMResponse, StructuredLLMResponse, NormalizedStreamEvent
- provider_formatter.py: ProviderFormatter protocol and implementations
- stream_handler.py: Stream processing utilities
- error_handling.py: Error classification and handling
- helpers.py: Utility functions

Migration Notice (2026-03-31):
    The standalone call_sync.py, call_structured.py, and call_stream.py modules
    have been removed. Their functionality has been consolidated into the
    LLMInvoker service class in invoker.py.

    LLMCaller is now a facade over LLMInvoker and is maintained for backward
    compatibility. New code should use LLMInvoker directly.
"""

import warnings
from typing import Any

from ..events import emit_llm_event

# Legacy facade (deprecated, maintained for backward compatibility)
from .caller import LLMCaller

# Core service class (recommended for new code)
from .decision_caller import DecisionCaller
from .error_handling import (
    build_native_tool_unavailable_error,
    classify_error,
    is_native_tool_calling_unsupported,
    is_response_format_unsupported,
    is_retryable_error,
)
from .finalization_caller import FinalizationCaller
from .helpers import (
    build_native_tool_schemas,
    extract_native_tool_calls,
    messages_to_input,
    resolve_timeout_seconds,
)
from .invoker import LLMInvoker
from .provider_formatter import (
    AnnotatedProviderFormatter,
    NativeProviderFormatter,
    ProviderFormatter,
    create_formatter,
)
from .response_types import (
    LLMResponse,
    NormalizedStreamEvent,
    PreparedLLMRequest,
    StructuredLLMResponse,
)
from .stream_handler import (
    build_stream_slo_metrics,
    is_stream_cancel_requested,
    normalize_stream_chunk,
    resolve_stream_runtime_config,
)

__all__ = [
    "AnnotatedProviderFormatter",
    "DecisionCaller",
    "FinalizationCaller",
    "LLMCaller",
    "LLMInvoker",
    "LLMResponse",
    "NativeProviderFormatter",
    "NormalizedStreamEvent",
    "PreparedLLMRequest",
    "ProviderFormatter",
    "StructuredLLMResponse",
    "build_native_tool_schemas",
    "build_native_tool_unavailable_error",
    "build_stream_slo_metrics",
    "classify_error",
    "create_formatter",
    "extract_native_tool_calls",
    "is_native_tool_calling_unsupported",
    "is_response_format_unsupported",
    "is_retryable_error",
    "is_stream_cancel_requested",
    "messages_to_input",
    "normalize_stream_chunk",
    "resolve_stream_runtime_config",
    "resolve_timeout_seconds",
]


# Emit deprecation warning for the removed modules if they are imported
def _warn_removed_module(name: str) -> None:
    """Warn about removed modules."""
    removed_modules = {
        "call_sync": "Functionality merged into LLMInvoker.call()",
        "call_structured": "Functionality merged into LLMInvoker.call_structured()",
        "call_stream": "Functionality merged into LLMInvoker.call_stream()",
    }
    if name in removed_modules:
        warnings.warn(
            f"Module '{name}' has been removed. {removed_modules[name]}. Use LLMInvoker instead.",
            DeprecationWarning,
            stacklevel=3,
        )
        raise ModuleNotFoundError(
            f"No module named '{name}' in 'polaris.cells.roles.kernel.internal.llm_caller'. "
            f"{removed_modules[name]}. Use LLMInvoker instead."
        )


def __getattr__(name: str) -> Any:
    """Handle removed module imports."""
    _warn_removed_module(name)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
