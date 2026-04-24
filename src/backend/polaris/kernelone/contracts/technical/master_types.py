"""Technical master types for KernelOne runtime contracts.

This module is the single source of truth for all foundational technical
contracts that cross-cut multiple KernelOne subsystems. It defines:
- Envelope: standard envelope for all cross-layer messages
- Result/Error: unified error handling
- Stream: async streaming primitives
- Effect: side-effect declaration and tracking
- Lock: distributed lock primitives
- Scheduler: task scheduling primitives

These types are platform-independent and contain NO Polaris business
semantics. They are the "system call interface" of the KernelOne runtime.

All types in this module MUST satisfy the KernelOne admission criteria:
1. No Polaris business vocabulary or use-case semantics
2. Meaningful as a standalone technical package
3. Reusable across multiple upper-layer scenarios
4. Testable without importing delivery/application/domain
5. Expose stable technical contracts, not convenience wrappers
"""

from __future__ import annotations

import uuid
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    TypeAlias,
    TypeVar,
)

from polaris.kernelone.errors import (
    ErrorCategory as _CanonicalErrorCategory,
    KernelOneError as _CanonicalKernelOneError,
)
from polaris.kernelone.utils.constants import GENESIS_HASH
from polaris.kernelone.utils.time_utils import utc_now as _utc_now

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# -----------------------------------------------------------------------------
# Identity & Versioning
# -----------------------------------------------------------------------------

KERNELONE_VERSION = "2.0"


def _new_event_id() -> str:
    return uuid.uuid4().hex


def _new_run_id() -> str:
    return uuid.uuid4().hex[:12]


# -----------------------------------------------------------------------------
# Envelope (cross-layer message wrapper)
# -----------------------------------------------------------------------------

T = TypeVar("T")


@dataclass(frozen=True)
class Envelope(Generic[T]):
    """Standard wrapper for all cross-layer messages.

    Every piece of data that crosses a layer boundary or a subsystem
    interface MUST be wrapped in an Envelope. This provides:
    - Traceability: event_id + timestamp + correlation_id
    - Versioning: contract version for compatibility
    - Attribution: source subsystem attribution
    - Typing: generic payload type safety
    """

    event_id: str = field(default_factory=_new_event_id)
    timestamp: datetime = field(default_factory=_utc_now)
    version: str = KERNELONE_VERSION
    correlation_id: str = ""
    source: str = ""  # e.g. "kernelone.llm", "kernelone.fs"
    payload: T | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "version": self.version,
            "correlation_id": self.correlation_id,
            "source": self.source,
            "payload": self.payload,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Envelope[Any]:
        ts_raw = data.get("timestamp")
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")) if isinstance(ts_raw, str) else _utc_now()
        return cls(
            event_id=str(data.get("event_id") or ""),
            timestamp=ts,
            version=str(data.get("version") or KERNELONE_VERSION),
            correlation_id=str(data.get("correlation_id") or ""),
            source=str(data.get("source") or ""),
            payload=data.get("payload"),
            metadata=dict(data.get("metadata") or {}),
        )

    def wrap_response(self, payload: T, *, source: str = "") -> Envelope[T]:
        """Create a response envelope correlated with this request envelope."""
        return Envelope(
            event_id=_new_event_id(),
            timestamp=_utc_now(),
            version=self.version,
            correlation_id=self.event_id,
            source=source or self.source,
            payload=payload,
            metadata=dict(self.metadata),
        )


# -----------------------------------------------------------------------------
# Result (unified error handling)
# -----------------------------------------------------------------------------

E = TypeVar("E")


@dataclass(frozen=True, slots=True)
class Result(Generic[T, E]):
    """Functional Result type for explicit error handling.

    Replaces:
    - Boolean returns (True/False without context)
    - Dict-based error returns ({"ok": False, "error": "..."})
    - Exception-based flow control for expected errors

    The Error type parameter E allows domain-specific error tagging.
    """

    is_ok: bool
    value: T | None = None
    error: E | None = None
    error_message: str = ""

    @property
    def is_err(self) -> bool:
        return not self.is_ok

    def unwrap(self) -> T:
        """Get value, raise if error."""
        if self.is_err:
            msg = self.error_message or str(self.error) if self.error else "Unknown error"
            raise _CanonicalKernelOneError(f"Result.unwrap() called on Err: {msg}")
        if self.value is None:
            raise _CanonicalKernelOneError("Result.unwrap() called on Ok(None)")
        return self.value  # type: ignore[return-value]

    def unwrap_or(self, default: T) -> T:
        """Get value or return default if error."""
        return self.value if self.is_ok else default  # type: ignore[return-value]

    def map(self, fn: Callable[[T], T]) -> Result[T, E]:
        """Transform the value if ok."""
        if self.is_err:
            # Preserve error context: prefer explicit error_message, fall back to
            # TaggedError.message (which is not stored in Result.error_message).
            msg = self.error_message
            if not msg and isinstance(self.error, TaggedError):
                msg = self.error.message
            return Result(is_ok=False, error=self.error, error_message=msg)
        try:
            new_val = fn(self.value) if self.value is not None else None
            return Result(is_ok=True, value=new_val)
        except (RuntimeError, ValueError) as exc:  # pragma: no cover - defensive
            return Result(is_ok=False, error_message=str(exc))

    def to_dict(self) -> dict[str, Any]:
        if self.is_ok:
            return {"ok": True, "value": self.value}
        # Prefer explicit error_message; fall back to TaggedError.message for ergonomics.
        msg = self.error_message
        if not msg and isinstance(self.error, TaggedError):
            msg = self.error.message
        return {
            "ok": False,
            "error": self.error,
            "error_message": msg,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Result[Any, Any]:
        """Reconstruct a Result from a dictionary (inverse of to_dict).

        Note: The error type parameter is lost in serialization;
        callers may need to cast or re-wrap the result.
        """
        is_ok = bool(data.get("ok", False))
        if is_ok:
            return cls.ok(data.get("value"))
        raw_error = data.get("error")
        error_message = str(data.get("error_message", ""))
        if isinstance(raw_error, TaggedError):
            return cls(is_ok=False, error=raw_error, error_message=error_message)  # type: ignore[arg-type]
        if isinstance(raw_error, KernelError):
            return cls(is_ok=False, error=raw_error, error_message=error_message)  # type: ignore[arg-type]
        # Fall back: wrap raw error in TaggedError if it's a string
        if isinstance(raw_error, str):
            return cls(
                is_ok=False,
                error=TaggedError(raw_error, error_message),  # type: ignore[arg-type]
                error_message=error_message,
            )
        return cls(is_ok=False, error_message=error_message)

    @classmethod
    def ok(cls, value: T | None = None) -> Result[T, E]:
        return cls(is_ok=True, value=value)

    @classmethod
    def err(cls, error: E, message: str = "") -> Result[T, E]:
        return cls(is_ok=False, error=error, error_message=str(message))


# -----------------------------------------------------------------------------
# ErrorCategory (deprecated - import from polaris.kernelone.errors instead)
# -----------------------------------------------------------------------------


def __getattr__(name: str) -> Any:
    if name == "ErrorCategory":
        warnings.warn(
            "ErrorCategory has been moved to polaris.kernelone.errors. "
            "Please update imports to use: from polaris.kernelone.errors import ErrorCategory",
            DeprecationWarning,
            stacklevel=2,
        )
        return _CanonicalErrorCategory
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# For backward compatibility during deprecation period
ErrorCategory: TypeAlias = _CanonicalErrorCategory


@dataclass(frozen=True)
class KernelError:
    """Standardized error structure across KernelOne.

    This is the canonical error type for ``Result[T, KernelError]``.
    The ``code`` field provides machine-readable error identification compatible
    with the legacy ``ErrorCodes`` string constants.

    Migration from legacy ``Result.err(message, code="X")``::

        # Old (runtime/result.py)
        Result.err("Review not found", code="REVIEW_NOT_FOUND")

        # New (master_types.py)
        Result.err(
            KernelError(code="REVIEW_NOT_FOUND", message="Review not found"),
        )
    """

    category: ErrorCategory = ErrorCategory.UNKNOWN
    message: str = ""
    code: str = "KERNEL_ERROR"
    context: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False
    source: str = ""  # subsystem that produced the error

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "message": self.message,
            "code": self.code,
            "context": dict(self.context),
            "retryable": self.retryable,
            "source": self.source,
        }


# -----------------------------------------------------------------------------
# Legacy error code -> ErrorCategory mapping (for migration)
# -----------------------------------------------------------------------------

_CODE_TO_CATEGORY: dict[str, ErrorCategory] = {
    "INVALID_ARGUMENT": ErrorCategory.INVALID_INPUT,
    "NOT_FOUND": ErrorCategory.NOT_FOUND,
    "ALREADY_EXISTS": ErrorCategory.ALREADY_EXISTS,
    "PERMISSION_DENIED": ErrorCategory.PERMISSION_DENIED,
    "RESOURCE_EXHAUSTED": ErrorCategory.RESOURCE_EXHAUSTED,
    "FAILED_PRECONDITION": ErrorCategory.FAILED_PRECONDITION,
    "ABORTED": ErrorCategory.ABORTED,
    "OUT_OF_RANGE": ErrorCategory.OUT_OF_RANGE,
    "UNIMPLEMENTED": ErrorCategory.UNIMPLEMENTED,
    "INTERNAL_ERROR": ErrorCategory.INTERNAL_ERROR,
    "UNAVAILABLE": ErrorCategory.UNAVAILABLE,
    "DEADLINE_EXCEEDED": ErrorCategory.DEADLINE_EXCEEDED,
    # Domain-specific codes
    "AGENT_NOT_FOUND": ErrorCategory.NOT_FOUND,
    "AGENT_ALREADY_REGISTERED": ErrorCategory.ALREADY_EXISTS,
    "AGENT_INITIALIZATION_FAILED": ErrorCategory.INTERNAL_ERROR,
    "AGENT_START_FAILED": ErrorCategory.INTERNAL_ERROR,
    "AGENT_STOP_FAILED": ErrorCategory.INTERNAL_ERROR,
    "TASK_NOT_FOUND": ErrorCategory.NOT_FOUND,
    "TASK_ALREADY_EXISTS": ErrorCategory.ALREADY_EXISTS,
    "TASK_INVALID_STATE": ErrorCategory.FAILED_PRECONDITION,
    "REVIEW_NOT_FOUND": ErrorCategory.NOT_FOUND,
    "REVIEW_INVALID_STATE": ErrorCategory.FAILED_PRECONDITION,
    "PROTOCOL_ERROR": ErrorCategory.INTERNAL_ERROR,
    "MESSAGE_QUEUE_ERROR": ErrorCategory.UNAVAILABLE,
}


class TaggedError:
    """Error tag for ``Result[T, TaggedError]`` — provides the legacy
    ``Result.err(code, message)`` call signature for incremental migration.

    Usage::

        Result.err(TaggedError("REVIEW_NOT_FOUND", "Review record not found"))

    The ``code`` is automatically mapped to an ``ErrorCategory`` for structured
    error classification while preserving the machine-readable string code.

    This class exists only to ease migration from ``runtime/result.py::Result``.
    New code should prefer ``KernelError`` directly.
    """

    __slots__ = ("category", "code", "message")

    def __init__(self, code: str, message: str) -> None:
        self.code: str = code
        self.message: str = message
        self.category: ErrorCategory = _CODE_TO_CATEGORY.get(code, ErrorCategory.UNKNOWN)

    def to_kernel_error(self) -> KernelError:
        """Convert to the canonical ``KernelError``."""
        return KernelError(
            category=self.category,
            code=self.code,
            message=self.message,
        )

    def __repr__(self) -> str:
        return f"TaggedError({self.code!r}, {self.message!r})"

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, TaggedError):
            return self.code == other.code and self.message == other.message
        return False

    def __hash__(self) -> int:
        return hash((self.code, self.message))


# -----------------------------------------------------------------------------
# KernelOneError (deprecated - import from polaris.kernelone.errors instead)
# -----------------------------------------------------------------------------


# Create a subclass for backward compatibility that emits deprecation warning
class KernelOneError(_CanonicalKernelOneError):
    """Base exception for KernelOne runtime errors.

    .. deprecated::
        KernelOneError has been moved to polaris.kernelone.errors.
        Please update imports to use:
            from polaris.kernelone.errors import KernelOneError
    """

    def __init__(self, message: str, *, code: str = "KERNEL_ERROR") -> None:
        warnings.warn(
            "KernelOneError has been moved to polaris.kernelone.errors. "
            "Please update imports to use: from polaris.kernelone.errors import KernelOneError",
            DeprecationWarning,
            stacklevel=2,
        )
        self.code = code
        super().__init__(message, code=code)


# -----------------------------------------------------------------------------
# Effect (side-effect declaration)
# -----------------------------------------------------------------------------

# These must be declared before any file/db/ws/etc. operation
EFFECT_TAG_MAX_LENGTH = 64
EFFECT_PAYLOAD_MAX_BYTES = 64 * 1024


class EffectType(str, Enum):
    """Canonical side-effect types that require declaration."""

    FS_READ = "fs.read"
    FS_WRITE = "fs.write"
    FS_DELETE = "fs.delete"
    DB_QUERY = "db.query"
    DB_WRITE = "db.write"
    NETWORK_REQUEST = "network.request"
    SUBPROCESS = "subprocess"
    LLM_CALL = "llm.call"
    TOOL_CALL = "tool_call"  # Unified with UEP EVENT_TYPE_TOOL_CALL (P2-017)
    DESCRIPTOR_UPDATE = "descriptor.update"
    INDEX_UPDATE = "index.update"
    AUDIT_WRITE = "audit.write"
    STREAM_PUBLISH = "stream.publish"


@dataclass(frozen=True, slots=True)
class Effect:
    """Immutable declaration of a side-effect operation.

    Every high-risk side effect (file I/O, DB write, network, LLM call,
    subprocess, index update) MUST be declared as an Effect before execution.
    Effects are the basis for the KernelOne audit chain.
    """

    effect_id: str = field(default_factory=_new_event_id)
    effect_type: EffectType = EffectType.FS_READ
    resource: str = ""  # path, URL, query string, etc.
    principal: str = ""  # who/what initiated this effect
    timestamp: datetime = field(default_factory=_utc_now)
    correlation_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    payload_bytes: int = 0  # approximate size for audit chain validation

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect_id": self.effect_id,
            "effect_type": self.effect_type.value,
            "resource": self.resource,
            "principal": self.principal,
            "timestamp": self.timestamp.isoformat(),
            "correlation_id": self.correlation_id,
            "metadata": dict(self.metadata),
            "payload_bytes": self.payload_bytes,
        }


class EffectTracker(Generic[T]):
    """Tracks declared effects for a single operation context.

    Usage:
        tracker = EffectTracker[None]("op-123")
        tracker.declare(Effect(effect_type=EffectType.FS_READ, resource="/path/to/file"))
        tracker.declare(Effect(effect_type=EffectType.LLM_CALL, resource="model/gpt-4"))
        effects = tracker.finalize()
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
            metadata=dict(metadata or {}),
            payload_bytes=payload_bytes,
        )
        self._effects.append(effect)
        return effect

    def declare_fs_read(self, path: str) -> Effect:
        """Declare a filesystem read effect on the given path.

        Args:
            path: The filesystem path being read.

        Returns:
            The declared Effect instance.
        """
        return self.declare(EffectType.FS_READ, resource=path)

    def declare_fs_write(self, path: str, *, payload_bytes: int = 0) -> Effect:
        return self.declare(EffectType.FS_WRITE, resource=path, payload_bytes=payload_bytes)

    def declare_llm_call(self, model: str, *, prompt_tokens: int = 0, metadata: dict[str, Any] | None = None) -> Effect:
        """Declare an LLM call effect for the given model.

        Args:
            model: The model identifier (e.g., "gpt-4", "claude-3-opus").
            prompt_tokens: Approximate number of prompt tokens used. Stored in
                effect metadata for audit purposes.
            metadata: Optional additional metadata to attach to the effect.

        Returns:
            The declared Effect instance.
        """
        return self.declare(
            EffectType.LLM_CALL,
            resource=model,
            metadata={
                **(dict(metadata) if metadata else {}),
                "prompt_tokens": prompt_tokens,
            },
        )

    @property
    def effects(self) -> list[Effect]:
        return list(self._effects)

    def finalize(self) -> tuple[str, list[Effect]]:
        """Finalize and retrieve all effects tracked for this operation.

        Returns:
            A tuple of (operation_id, effects) where effects is a snapshot
            of all Effect instances declared since tracker creation.
        """
        return (self._operation_id, list(self._effects))

    def clear(self) -> None:
        """Clear all declared effects from this tracker.

        After calling clear(), the tracker can be reused to declare a fresh
        set of effects for a new operation.
        """
        self._effects.clear()


# -----------------------------------------------------------------------------
# Lock (distributed lock primitives)
# -----------------------------------------------------------------------------

LOCK_TIMEOUT_DEFAULT_SECONDS = 30.0
LOCK_TTL_DEFAULT_SECONDS = 60.0


@dataclass(frozen=True)
class LockOptions:
    """Options for acquiring a distributed lock."""

    timeout_seconds: float = LOCK_TIMEOUT_DEFAULT_SECONDS
    ttl_seconds: float = LOCK_TTL_DEFAULT_SECONDS
    retry_interval_seconds: float = 0.1
    non_blocking: bool = False  # if True, acquire returns immediately if held


@dataclass(frozen=True)
class LockAcquireResult:
    """Result of a lock acquisition attempt."""

    acquired: bool
    lock_id: str = ""
    holder_id: str = ""  # who holds this lock
    expires_at: datetime | None = None
    waited_ms: int = 0


@dataclass(frozen=True)
class LockReleaseResult:
    """Result of a lock release operation."""

    released: bool
    lock_id: str = ""
    force_released: bool = False  # True if released by a different holder


class LockPort:
    """Abstract interface for distributed locking.

    Implementations: RedisLockAdapter, SQLiteLockAdapter, FileLockAdapter.
    """

    async def acquire(
        self,
        resource: str,
        holder_id: str,
        options: LockOptions | None = None,
    ) -> LockAcquireResult:
        """Acquire a distributed lock on a resource.

        Args:
            resource: The resource identifier to lock (e.g., file path, key name).
            holder_id: The identifier of the lock holder requesting acquisition.
            options: Optional LockOptions controlling timeout, TTL, and blocking
                behavior. Defaults to LOCK_TIMEOUT_DEFAULT_SECONDS and
                LOCK_TTL_DEFAULT_SECONDS.

        Returns:
            LockAcquireResult indicating whether the lock was acquired, the
            lock_id, holder_id, expiration time, and wait duration.
        """
        raise NotImplementedError

    async def release(self, resource: str, holder_id: str) -> LockReleaseResult:
        """Release a held lock on a resource.

        Args:
            resource: The resource identifier to unlock.
            holder_id: The identifier of the lock holder releasing the lock.
                Must match the holder who acquired the lock.

        Returns:
            LockReleaseResult indicating whether the lock was released and
            whether it was force-released by a different holder.
        """
        raise NotImplementedError

    async def extend(self, resource: str, holder_id: str, additional_seconds: float) -> bool:
        """Extend the TTL of a currently held lock.

        Args:
            resource: The resource identifier of the lock to extend.
            holder_id: The identifier of the lock holder requesting the extension.
                Must match the current holder.
            additional_seconds: The number of seconds to add to the lock's TTL.

        Returns:
            True if the TTL was successfully extended, False otherwise
            (e.g., lock not held or held by a different holder).
        """
        raise NotImplementedError

    async def is_held(self, resource: str) -> bool:
        """Check whether a lock is currently held on a resource.

        Args:
            resource: The resource identifier to check.

        Returns:
            True if the lock is currently held, False otherwise.
        """
        raise NotImplementedError

    async def close(self) -> None:
        """Release all resources held by this lock adapter.

        Implementations should close any open connections (e.g., Redis,
        database, file handles) and release held locks if applicable.
        """
        raise NotImplementedError


# -----------------------------------------------------------------------------
# Scheduler (task scheduling primitives)
# -----------------------------------------------------------------------------


class ScheduleKind(str, Enum):
    """Supported scheduling patterns."""

    ONCE = "once"
    PERIODIC = "periodic"
    CRON = "cron"
    DELAYED = "delayed"


@dataclass(frozen=True)
class ScheduleSpec:
    """Specification for a scheduled task."""

    kind: ScheduleKind = ScheduleKind.ONCE
    interval_seconds: float = 0.0  # for PERIODIC
    cron_expression: str = ""  # for CRON
    delay_seconds: float = 0.0  # for DELAYED
    max_runs: int = 0  # 0 = unlimited
    run_id: str = field(default_factory=_new_run_id)


@dataclass(frozen=True)
class ScheduledTask:
    """A task submitted to the scheduler."""

    task_id: str = field(default_factory=_new_event_id)
    run_id: str = field(default_factory=_new_run_id)
    handler: str = ""  # e.g. "kernelone.audit.cleanup"
    payload: dict[str, Any] = field(default_factory=dict)
    schedule: ScheduleSpec = field(default_factory=ScheduleSpec)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utc_now)


@dataclass(frozen=True)
class ScheduleResult:
    """Result of scheduling a task."""

    scheduled: bool
    task_id: str = ""
    next_run_at: datetime | None = None
    error: str | None = None


class SchedulerPort:
    """Abstract interface for task scheduling.

    Handler execution is delegated to a registered handler registry.
    Subclasses that support dispatch must override ``_execute_handler`` to
    resolve ``task.handler`` (a string key) and invoke it with
    ``task.payload`` (dict arguments).

    The canonical in-process adapter (``SimpleScheduler``) maintains a
    ``Dict[str, Callable[..., Awaitable[Any] | Any]]`` registry accessible
    via ``register_handler`` / ``unregister_handler``. Distributed schedulers
    (Redis, NATS JetStream, etc.) replace dispatch with a remote call.
    """

    async def schedule(self, task: ScheduledTask) -> ScheduleResult:
        raise NotImplementedError

    async def cancel(self, task_id: str) -> bool:
        raise NotImplementedError

    async def get_next_run(self, task_id: str) -> datetime | None:
        raise NotImplementedError

    async def list_tasks(self) -> list[ScheduledTask]:
        raise NotImplementedError

    def register_handler(self, name: str, handler: Callable[..., Awaitable[Any] | Any]) -> None:
        """Register a handler callable for a named task key.

        Args:
            name: Handler key, e.g. ``"kernelone.audit.cleanup"``.
                  Must match ``ScheduledTask.handler`` at dispatch time.
            handler: Sync or async callable invoked as ``handler(**task.payload)``.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support handler registration")

    def unregister_handler(self, name: str) -> None:
        """Remove a registered handler."""
        raise NotImplementedError(f"{type(self).__name__} does not support handler unregistration")


# -----------------------------------------------------------------------------
# Stream (async streaming primitives)
# -----------------------------------------------------------------------------

T_co = TypeVar("T_co", covariant=True)


class StreamEvent(Generic[T_co]):
    """Base type for stream events in KernelOne.

    StreamEvent is the covariant generic base for all streaming event types,
    including data chunks, stream completion markers, and error markers.
    Concrete subclasses include StreamChunk, StreamDone, and StreamError.
    """

    ...


@dataclass(frozen=True)
class StreamChunk(StreamEvent[str]):
    """A single chunk in a stream."""

    data: str
    sequence: int
    is_final: bool = False


@dataclass(frozen=True)
class StreamDone(StreamEvent[Any]):
    """Marker for end of stream."""

    final_value: Any = None


@dataclass(frozen=True)
class StreamError(StreamEvent[KernelError]):
    """Marker for stream error."""

    error: KernelError


# -----------------------------------------------------------------------------
# Health & Lifecycle
# -----------------------------------------------------------------------------


class SubsystemStatus(str, Enum):
    """Health status of a KernelOne subsystem."""

    UNKNOWN = "unknown"
    INITIALIZING = "initializing"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    STOPPED = "stopped"


@dataclass(frozen=True)
class SubsystemHealth:
    """Health report from one subsystem."""

    subsystem: str  # e.g. "kernelone.fs", "kernelone.llm"
    status: SubsystemStatus = SubsystemStatus.UNKNOWN
    latency_ms: int = 0
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subsystem": self.subsystem,
            "status": self.status.value,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class RuntimeHealthReport:
    """Overall runtime health report."""

    version: str = KERNELONE_VERSION
    run_id: str = field(default_factory=_new_run_id)
    timestamp: datetime = field(default_factory=_utc_now)
    healthy: bool = False
    subsystems: list[SubsystemHealth] = field(default_factory=list)
    overall_latency_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "run_id": self.run_id,
            "timestamp": self.timestamp.isoformat(),
            "healthy": self.healthy,
            "subsystems": [s.to_dict() for s in self.subsystems],
            "overall_latency_ms": self.overall_latency_ms,
        }


# -----------------------------------------------------------------------------
# Tracing Context
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class TraceContext:
    """Distributed trace context propagated across subsystems."""

    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    parent_span_id: str = ""
    baggage: dict[str, str] = field(default_factory=dict)
    sampled: bool = True

    def child(self) -> TraceContext:
        """Create a child span context."""
        return TraceContext(
            trace_id=self.trace_id,
            parent_span_id=self.span_id,
            span_id=uuid.uuid4().hex[:8],
            baggage=dict(self.baggage),
            sampled=self.sampled,
        )

    def with_baggage(self, key: str, value: str) -> TraceContext:
        new_baggage = dict(self.baggage)
        new_baggage[key] = value
        return TraceContext(
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            baggage=new_baggage,
            sampled=self.sampled,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "baggage": dict(self.baggage),
            "sampled": self.sampled,
        }


# -----------------------------------------------------------------------------
# Module public API
# -----------------------------------------------------------------------------

__all__ = [
    "EFFECT_PAYLOAD_MAX_BYTES",
    "EFFECT_TAG_MAX_LENGTH",
    "GENESIS_HASH",
    # Versioning
    "KERNELONE_VERSION",
    "LOCK_TIMEOUT_DEFAULT_SECONDS",
    "LOCK_TTL_DEFAULT_SECONDS",
    "Effect",
    "EffectTracker",
    # Effect
    "EffectType",
    # Envelope
    "Envelope",
    "ErrorCategory",
    "KernelError",
    "KernelOneError",
    "LockAcquireResult",
    # Lock
    "LockOptions",
    "LockPort",
    "LockReleaseResult",
    # Result & Error
    "Result",
    "RuntimeHealthReport",
    # Scheduler
    "ScheduleKind",
    "ScheduleResult",
    "ScheduleSpec",
    "ScheduledTask",
    "SchedulerPort",
    "StreamChunk",
    "StreamDone",
    "StreamError",
    # Stream
    "StreamEvent",
    "SubsystemHealth",
    # Health
    "SubsystemStatus",
    "TaggedError",
    # Tracing
    "TraceContext",
    "_new_event_id",
    "_new_run_id",
    # Identity helpers
    "_utc_now",
]
