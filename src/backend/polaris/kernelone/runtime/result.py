"""Result type alias for legacy backward compatibility.

.. deprecated::
    This module is DEPRECATED. It contains the legacy ``Result`` type
    that existed before the ACGA 2.0 contract unification (2026-03-22).

    **Migration path**::

        # Old — this module (deprecated)
        from polaris.kernelone.runtime.result import Result, ErrorCodes
        Result.err("message", code=ErrorCodes.NOT_FOUND)

        # New — canonical (master_types.py)
        from polaris.kernelone.contracts.technical import Result, TaggedError
        Result.err(TaggedError("NOT_FOUND", "message"))

    The canonical ``Result[T, E]`` lives in
    ``polaris.kernelone.contracts.technical.master_types``.
    It is re-exported via ``polaris.kernelone.runtime`` and
    ``polaris.kernelone.contracts.technical``.

    This module is retained ONLY to avoid breaking existing code that
    imports directly from it (e.g. ``tests/test_unified_result_and_error_handling.py``).
    DO NOT add new usages. All production code must use the canonical Result.

    Key differences from canonical Result[T, E]:
    - Single type param ``Result[T]`` (no error type parameter)
    - ``Result.err(message, *, code, details)`` instead of
      ``Result.err(error_tag, message="")`` / ``Result.err(kernel_error)``
    - Extra fields: ``error_code`` (str), ``error_details`` (dict)
    - Extra methods: ``and_then()``, ``log()``, ``ok_value`` / ``err_value`` properties,
      ``from_exception()``, ``from_dict()`` classmethod
    - ``ErrorCodes`` class (deprecated; use ``TaggedError`` / ``KernelError`` instead)

Migration (2026-04-05):
    The legacy ``Result`` class has been replaced with a type alias pointing
    to the canonical ``Result[T, E]`` in ``master_types.py``. The legacy
    factory methods (``Result.ok()``, ``Result.err()``) are no longer available.
    Use ``Result.ok(value)`` and ``Result.err(error, message)`` from the
    canonical module instead.
"""

from __future__ import annotations

import warnings
from typing import Any


def __getattr__(name: str) -> Any:
    """Module-level getattr to emit deprecation warnings for legacy access."""
    if name == "Result":
        warnings.warn(
            "polaris.kernelone.runtime.Result is deprecated. "
            "Use polaris.kernelone.contracts.technical.Result instead. "
            "The canonical Result[T, E] uses TaggedError/KernelError for errors.",
            DeprecationWarning,
            stacklevel=2,
        )
        # Return the canonical Result class
        from polaris.kernelone.contracts.technical.master_types import Result as CanonicalResult

        return CanonicalResult

    if name == "ErrorCodes":
        warnings.warn(
            "polaris.kernelone.runtime.ErrorCodes is deprecated. "
            "Use TaggedError or KernelError from "
            "polaris.kernelone.contracts.technical instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        # Return the ErrorCodes class
        return _ErrorCodes

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class _ErrorCodes:
    """Legacy error codes class.

    .. deprecated::
        ErrorCodes is deprecated. Use ``TaggedError`` or ``KernelError``
        from ``polaris.kernelone.contracts.technical`` instead.
    """

    __slots__ = ()

    # Generic errors
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    NOT_FOUND = "NOT_FOUND"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    FAILED_PRECONDITION = "FAILED_PRECONDITION"
    ABORTED = "ABORTED"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    UNIMPLEMENTED = "UNIMPLEMENTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    UNAVAILABLE = "UNAVAILABLE"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"

    # Domain-specific errors
    AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
    AGENT_ALREADY_REGISTERED = "AGENT_ALREADY_REGISTERED"
    AGENT_INITIALIZATION_FAILED = "AGENT_INITIALIZATION_FAILED"
    AGENT_START_FAILED = "AGENT_START_FAILED"
    AGENT_STOP_FAILED = "AGENT_STOP_FAILED"

    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    TASK_ALREADY_EXISTS = "TASK_ALREADY_EXISTS"
    TASK_INVALID_STATE = "TASK_INVALID_STATE"

    REVIEW_NOT_FOUND = "REVIEW_NOT_FOUND"
    REVIEW_INVALID_STATE = "REVIEW_INVALID_STATE"

    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    MESSAGE_QUEUE_ERROR = "MESSAGE_QUEUE_ERROR"


# Backward compatibility alias - ErrorCodes accessible directly
ErrorCodes = _ErrorCodes


__all__ = ["ErrorCodes"]
