"""Context and turn-decision errors.

Internal submodule of :mod:`polaris.kernelone.errors`.
Public symbols are re-exported from the package ``__init__``.
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.errors._base import KernelOneError


class ContextError(KernelOneError):
    """Context-related errors."""

    def __init__(
        self: Any,
        message: str,
        *,
        code: str = "CONTEXT_ERROR",
        **kwargs: Any,
    ) -> None:
        super().__init__(message, code=code, **kwargs)


class ContextOverflowError(ContextError):
    """Context overflow error."""

    def __init__(
        self: Any,
        message: str,
        *,
        max_tokens: int = 0,
        current_tokens: int = 0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            message,
            code="CONTEXT_OVERFLOW_ERROR",
            **kwargs,
        )
        if max_tokens:
            self.details["max_tokens"] = max_tokens
        if current_tokens:
            self.details["current_tokens"] = current_tokens


class ContextCompilationError(ContextError):
    """Context compilation error."""

    def __init__(
        self: Any,
        message: str,
        *,
        compilation_step: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            message,
            code="CONTEXT_COMPILATION_ERROR",
            **kwargs,
        )
        if compilation_step:
            self.details["compilation_step"] = compilation_step


class TurnDecisionError(KernelOneError):
    """Turn decision error."""

    def __init__(
        self: Any,
        message: str,
        *,
        code: str = "TURN_DECISION_ERROR",
        turn_id: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(message, code=code, **kwargs)
        if turn_id:
            self.details["turn_id"] = turn_id


class TurnDecisionDecodeError(TurnDecisionError):
    """Turn decision decode error."""

    def __init__(
        self: Any,
        message: str,
        *,
        raw_response: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            message,
            code="TURN_DECISION_DECODE_ERROR",
            **kwargs,
        )
        if raw_response:
            self.details["raw_response"] = raw_response[:500]  # Limit size
