"""Public contracts for ``runtime.execution_broker``."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _copy_mapping(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _normalize_workspace(value: str | Path) -> str:
    raw = str(value).strip()
    if not raw:
        raise ValueError("workspace must be a non-empty string")
    workspace_path = Path(raw).expanduser().resolve()
    if not workspace_path.exists():
        raise ValueError(f"workspace does not exist: {workspace_path}")
    if not workspace_path.is_dir():
        raise ValueError(f"workspace is not a directory: {workspace_path}")
    return str(workspace_path)


class ExecutionProcessStatusV1(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ExecutionErrorCode(Enum):
    """Execution broker error codes for machine-parseable error classification."""

    # Launch errors
    LAUNCH_FAILED = "execution_broker.launch_failed"
    INVALID_WORKSPACE = "execution_broker.invalid_workspace"
    INVALID_TIMEOUT = "execution_broker.invalid_timeout"
    INVALID_ARGS = "execution_broker.invalid_args"

    # Process state errors
    PROCESS_NOT_FOUND = "execution_broker.process_not_found"
    PROCESS_ALREADY_FINISHED = "execution_broker.process_already_finished"

    # Timeout and cancellation
    TIMEOUT_EXCEEDED = "execution_broker.timeout_exceeded"
    TERMINATION_FAILED = "execution_broker.termination_failed"
    CANCEL_FAILED = "execution_broker.cancel_failed"

    # Metadata errors
    METADATA_ERROR = "execution_broker.metadata_error"

    # Runtime errors
    RUNTIME_ERROR = "execution_broker.runtime_error"
    EXECUTION_NOT_SUBPROCESS = "execution_broker.execution_not_subprocess"

    # Catch-all
    UNKNOWN_ERROR = "execution_broker.unknown_error"


@dataclass(frozen=True)
class LaunchExecutionProcessCommandV1:
    name: str
    args: tuple[str, ...]
    workspace: str
    timeout_seconds: float | None = 300.0
    env: Mapping[str, str] = field(default_factory=dict)
    stdin_input: str | None = None
    log_path: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_non_empty("name", self.name))
        normalized_args = tuple(str(arg) for arg in self.args if str(arg).strip())
        if not normalized_args:
            raise ValueError("args must contain at least one command token")
        object.__setattr__(self, "args", normalized_args)
        object.__setattr__(self, "workspace", _normalize_workspace(self.workspace))
        if self.timeout_seconds is not None and float(self.timeout_seconds) <= 0:
            raise ValueError("timeout_seconds must be > 0 when provided")
        object.__setattr__(self, "timeout_seconds", self.timeout_seconds)
        object.__setattr__(
            self,
            "env",
            {str(k): str(v) for k, v in dict(self.env).items() if str(k).strip()},
        )
        if self.stdin_input is not None:
            object.__setattr__(self, "stdin_input", str(self.stdin_input))
        if self.log_path is not None:
            normalized_log_path = str(Path(str(self.log_path)).expanduser())
            object.__setattr__(self, "log_path", normalized_log_path)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class GetExecutionProcessStatusQueryV1:
    execution_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "execution_id",
            _require_non_empty("execution_id", self.execution_id),
        )


@dataclass(frozen=True)
class ExecutionProcessHandleV1:
    execution_id: str
    pid: int | None
    name: str
    workspace: str
    log_path: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "execution_id",
            _require_non_empty("execution_id", self.execution_id),
        )
        object.__setattr__(self, "name", _require_non_empty("name", self.name))
        object.__setattr__(self, "workspace", _normalize_workspace(self.workspace))
        if self.pid is not None and int(self.pid) < 0:
            raise ValueError("pid must be >= 0 when provided")
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class ExecutionProcessLaunchResultV1:
    success: bool
    handle: ExecutionProcessHandleV1 | None = None
    error_message: str | None = None
    error_code: ExecutionErrorCode | None = None
    launched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class ExecutionProcessWaitResultV1:
    handle: ExecutionProcessHandleV1
    status: ExecutionProcessStatusV1
    success: bool
    exit_code: int | None = None
    timed_out: bool = False
    error_message: str | None = None
    error_code: ExecutionErrorCode | None = None
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ExecutionBrokerError(RuntimeError):
    """Raised when ``runtime.execution_broker`` fails to process a contract."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "execution_broker_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _copy_mapping(details)


__all__ = [
    "ExecutionBrokerError",
    "ExecutionErrorCode",
    "ExecutionProcessHandleV1",
    "ExecutionProcessLaunchResultV1",
    "ExecutionProcessStatusV1",
    "ExecutionProcessWaitResultV1",
    "GetExecutionProcessStatusQueryV1",
    "LaunchExecutionProcessCommandV1",
]
