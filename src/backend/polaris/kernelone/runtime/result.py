# Result type for legacy backward compatibility.
#
# DEPRECATED: This module is DEPRECATED.
# Use polaris.kernelone.contracts.technical.Result instead.

from __future__ import annotations

import logging
from typing import Any, Generic, TypeVar

T = TypeVar("T")

class Result(Generic[T]):
    # Legacy Result type with backward-compatible API.
    # DEPRECATED: Use polaris.kernelone.contracts.technical.Result instead.

    def __init__(
        self,
        is_ok: bool,
        value: T | None = None,
        error_message: str = "",
        error_code: str = "UNKNOWN_ERROR",
        error_details: dict[str, Any] | None = None,
    ) -> None:
        self.is_ok = is_ok
        self.value = value
        self.error_message = error_message
        self.error_code = error_code
        self.error_details = error_details or {}

    @property
    def is_err(self) -> bool:
        return not self.is_ok

    @property
    def ok_value(self) -> T:
        if self.is_err:
            raise RuntimeError("Cannot get ok_value from Err")
        return self.value  # type: ignore[return-value]

    @property
    def err_value(self) -> tuple[str, str]:
        if self.is_ok:
            raise RuntimeError("Cannot get err_value from Ok")
        return (self.error_code, self.error_message)

    def unwrap_or(self, default: T) -> T:
        return self.value if self.is_ok else default  # type: ignore[return-value]

    def unwrap_or_else(self, fn: Any) -> T:
        return self.value if self.is_ok else fn()

    def map(self, fn: Any) -> Result[Any]:
        if self.is_err:
            return Result(
                is_ok=False,
                error_message=self.error_message,
                error_code=self.error_code,
                error_details=dict(self.error_details),
            )
        try:
            new_val = fn(self.value)
            return Result(is_ok=True, value=new_val)
        except Exception as exc:  # pragma: no cover - defensive
            return Result(is_ok=False, error_message=str(exc))

    def and_then(self, fn: Any) -> Result[Any]:
        if self.is_err:
            return Result(
                is_ok=False,
                error_message=self.error_message,
                error_code=self.error_code,
                error_details=dict(self.error_details),
            )
        try:
            return fn(self.value)
        except Exception as exc:  # pragma: no cover - defensive
            return Result(is_ok=False, error_message=str(exc))

    def to_dict(self) -> dict[str, Any]:
        if self.is_ok:
            return {"ok": True, "value": self.value}
        result: dict[str, Any] = {
            "ok": False,
            "error": self.error_message,
            "error_code": self.error_code,
        }
        if self.error_details:
            result["error_details"] = dict(self.error_details)
        return result

    @classmethod
    def ok(cls, value: T | None = None) -> Result[T]:
        return cls(is_ok=True, value=value)

    @classmethod
    def err(
        cls,
        message: str,
        code: str = "UNKNOWN_ERROR",
        details: dict[str, Any] | None = None,
    ) -> Result[Any]:
        return cls(
            is_ok=False,
            error_message=message,
            error_code=code,
            error_details=details,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Result[Any]:
        is_ok = bool(data.get("ok", False))
        if is_ok:
            return cls.ok(data.get("value"))
        return cls.err(
            message=str(data.get("error", "")),
            code=str(data.get("error_code", "UNKNOWN_ERROR")),
            details=dict(data.get("error_details", {})),
        )

    @classmethod
    def from_exception(
        cls,
        exc: BaseException,
        code: str = "EXCEPTION",
        context: dict[str, Any] | None = None,
    ) -> Result[Any]:
        details: dict[str, Any] = {"exception_type": type(exc).__name__}
        if context:
            details.update(context)
        return cls.err(message=str(exc), code=code, details=details)

    def log(self, level: int = logging.DEBUG, include_details: bool = True) -> Result[T]:
        logger = logging.getLogger("polaris.kernelone.runtime.result")
        if self.is_err:
            msg = self.error_message
            if include_details and self.error_details:
                msg += f" | details={self.error_details}"
            logger.warning(msg)
        else:
            logger.log(level, "Result.ok: %s", self.value)
        return self

class _ErrorCodes:
    # Legacy error codes class.
    # DEPRECATED: Use TaggedError or KernelError instead.

    __slots__ = ()

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

ErrorCodes = _ErrorCodes

__all__ = ["Result", "ErrorCodes"]

