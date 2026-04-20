"""Effect tracking subsystem.

Provides EffectTrackerImpl (in-memory) and TimeoutManager for tracking
declared side effects and enforcing operation timeouts.

Design constraints:
- KernelOne-only: no Polaris business semantics
- No bare except: all errors caught with specific exception types
- Explicit UTF-8: all file I/O uses encoding="utf-8"
- Effects are declarations, not authorizations: this module tracks what
  was declared, it does not grant permissions.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from polaris.kernelone.contracts.technical import (
    Effect,
    EffectTracker,
    EffectType,
)
from polaris.kernelone.utils.time_utils import utc_now as _utc_now

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class EffectReceiptStatus(str, Enum):
    """Outcome of an effect execution."""

    DECLARED = "declared"
    SUCCESS = "success"
    FAILURE = "failure"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class EffectReceipt:
    """Immutable receipt for a completed effect.

    Records that an effect was declared, executed, and its outcome.
    This is the audit trail for the KernelOne effect chain.
    """

    effect: Effect
    status: EffectReceiptStatus = EffectReceiptStatus.DECLARED
    completed_at: datetime | None = None
    duration_ms: int = 0
    output: Any | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect": self.effect.to_dict(),
            "status": self.status.value,
            "completed_at": (self.completed_at.isoformat() if self.completed_at else None),
            "duration_ms": self.duration_ms,
            "output": self.output,
            "error": self.error,
        }


class EffectTrackerImpl(EffectTracker[None]):
    """In-memory effect tracker.

    Records declared effects and their outcomes. Provides an audit chain
    for all high-risk side effects within an operation context.

    Usage::

        tracker = EffectTrackerImpl("op-abc", principal="director.agent")
        with tracker:
            tracker.declare(EffectType.FS_WRITE, "/tmp/output.txt")
            tracker.declare(EffectType.LLM_CALL, "gpt-4")
            # ... perform operations ...
            receipts = tracker.finalize()   # get all receipts
    """

    def __init__(
        self,
        operation_id: str,
        *,
        principal: str = "kernel",
        correlation_id: str = "",
    ) -> None:
        self._operation_id = operation_id
        self._principal = principal
        self._correlation_id = correlation_id
        self._effects: list[Effect] = []
        self._receipts: list[EffectReceipt] = []
        self._active = False

    def __enter__(self) -> EffectTrackerImpl:
        self._active = True
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._active = False

    def declare(
        self,
        effect_type: EffectType,
        resource: str,
        *,
        metadata: dict[str, Any] | None = None,
        payload_bytes: int = 0,
    ) -> Effect:
        effect = Effect(
            effect_type=effect_type,
            resource=resource,
            principal=self._principal,
            correlation_id=self._correlation_id,
            metadata=dict(metadata) if metadata else {},
            payload_bytes=payload_bytes,
        )
        self._effects.append(effect)
        self._receipts.append(EffectReceipt(effect=effect, status=EffectReceiptStatus.DECLARED))
        return effect

    def declare_fs_read(self, path: str) -> Effect:
        return self.declare(EffectType.FS_READ, resource=path)

    def declare_fs_write(self, path: str, *, payload_bytes: int = 0) -> Effect:
        return self.declare(EffectType.FS_WRITE, resource=path, payload_bytes=payload_bytes)

    def declare_llm_call(
        self,
        model: str,
        *,
        prompt_tokens: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> Effect:
        return self.declare(
            EffectType.LLM_CALL,
            resource=model,
            metadata={
                **(dict(metadata) if metadata else {}),
                "prompt_tokens": prompt_tokens,
            },
        )

    def record_success(
        self,
        effect_id: str,
        output: Any,
        *,
        duration_ms: int = 0,
    ) -> None:
        self._update_receipt(
            effect_id,
            EffectReceiptStatus.SUCCESS,
            output=output,
            duration_ms=duration_ms,
        )

    def record_failure(
        self,
        effect_id: str,
        error: str,
        *,
        duration_ms: int = 0,
    ) -> None:
        self._update_receipt(
            effect_id,
            EffectReceiptStatus.FAILURE,
            error=error,
            duration_ms=duration_ms,
        )

    def _update_receipt(
        self,
        effect_id: str,
        status: EffectReceiptStatus,
        output: Any = None,
        error: str | None = None,
        duration_ms: int = 0,
    ) -> None:
        for i, receipt in enumerate(self._receipts):
            if receipt.effect.effect_id == effect_id:
                new_effect = receipt.effect
                new_receipt = EffectReceipt(
                    effect=new_effect,
                    status=status,
                    completed_at=_utc_now(),
                    duration_ms=duration_ms,
                    output=output,
                    error=error,
                )
                self._receipts[i] = new_receipt
                return

    @property
    def effects(self) -> list[Effect]:
        return list(self._effects)

    @property
    def receipts(self) -> list[EffectReceipt]:
        return list(self._receipts)

    def finalize(self) -> tuple[str, list[Effect]]:
        return (self._operation_id, list(self._effects))


# -----------------------------------------------------------------------------
# TimeoutManager
# -----------------------------------------------------------------------------


@dataclass
class TimeoutGuard:
    """Guard object for a timeout operation.

    Usage::

        guard = TimeoutGuard(timeout_seconds=5.0)
        try:
            async with guard:
                await long_operation()
        except TimeoutError:
            logger.warning("Operation timed out after %s", guard.timeout_seconds)
    """

    timeout_seconds: float
    elapsed_seconds: float = field(default=0.0, init=False)
    _start: float = field(default=0.0, init=False)
    _cancelled: bool = field(default=False, init=False)

    def __enter__(self) -> TimeoutGuard:
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.elapsed_seconds = time.monotonic() - self._start

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def timed_out(self) -> bool:
        return self.elapsed_seconds >= self.timeout_seconds


class TimeoutManager:
    """Manages operation timeouts with structured timeout guards.

    Provides a central place to configure and enforce operation timeouts
    across KernelOne subsystems. Uses monotonic clock to avoid clock drift issues.

    Design notes:
    - Uses time.monotonic() for stable elapsed-time measurement
    - All operations are in-process; no distributed coordination
    - Raises TimeoutError (standard library) on timeout, not a custom type
    """

    DEFAULT_TIMEOUTS: dict[str, float] = {
        "fs_read": 30.0,
        "fs_write": 60.0,
        "db_query": 15.0,
        "db_write": 30.0,
        "llm_call": 120.0,
        "subprocess": 300.0,
        "network_request": 30.0,
    }

    def __init__(self, *, overrides: dict[str, float] | None = None) -> None:
        self._timeouts: dict[str, float] = {
            **self.DEFAULT_TIMEOUTS,
            **(dict(overrides) if overrides else {}),
        }

    def get_timeout(self, operation: str) -> float:
        return self._timeouts.get(operation, 30.0)

    def set_timeout(self, operation: str, seconds: float) -> None:
        if seconds <= 0:
            raise ValueError(f"timeout must be positive, got {seconds}")
        self._timeouts[operation] = float(seconds)

    def timeout_for(self, effect_type: EffectType) -> str:
        mapping = {
            EffectType.FS_READ: "fs_read",
            EffectType.FS_WRITE: "fs_write",
            EffectType.FS_DELETE: "fs_write",
            EffectType.DB_QUERY: "db_query",
            EffectType.DB_WRITE: "db_write",
            EffectType.LLM_CALL: "llm_call",
            EffectType.SUBPROCESS: "subprocess",
            EffectType.NETWORK_REQUEST: "network_request",
        }
        return mapping.get(effect_type, "fs_read")

    async def run_with_timeout(
        self,
        coro: Callable,
        operation: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Run a coroutine with the configured timeout for the operation.

        Args:
            coro: Callable returning an awaitable
            operation: Operation key for timeout lookup
            *args, **kwargs: Forwarded to coro

        Returns:
            Result of the coroutine

        Raises:
            TimeoutError: If the operation exceeds its timeout
        """
        timeout = self.get_timeout(operation)
        try:
            return await asyncio.wait_for(coro(*args, **kwargs), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"Operation '{operation}' timed out after {timeout}s") from exc


__all__ = [
    "EffectReceipt",
    "EffectReceiptStatus",
    "EffectTrackerImpl",
    "TimeoutGuard",
    "TimeoutManager",
]
