"""Verification Guard Cell - Public Contracts.

This module defines the public contracts for the VerificationGuard Cell,
which implements "Verification Before Completion" pattern inspired by
Superpowers design principles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def _require_non_empty(name: str, value: str) -> str:
    """Validate that a string value is non-empty."""
    normalized = str(value).strip()
    if not normalized:
        msg = f"{name} must be a non-empty string"
        raise ValueError(msg)
    return normalized


def _copy_sequence(values: Sequence[str] | None) -> tuple[str, ...]:
    """Copy a sequence of strings into an immutable tuple."""
    return tuple(str(v) for v in (values or []) if str(v).strip())


def _copy_mapping(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy a mapping into a mutable dict."""
    return dict(payload or {})


class VerificationStatus(Enum):
    """Status of a verification attempt."""

    PASS = auto()
    FAIL = auto()
    BLOCKED = auto()
    TIMEOUT = auto()
    ERROR = auto()


@dataclass(frozen=True)
class VerificationClaim:
    """A claim of completion that requires verification before acceptance.

    This represents the "claimed outcome" that an agent asserts, along with
    the methods to verify that claim (commands to run, evidence to collect).

    Attributes:
        claim_id: Unique identifier for this claim
        claimed_outcome: The asserted result (e.g., "tests pass", "code formatted")
        verification_commands: Commands to execute for verification
        evidence_paths: Files/directories to check for evidence
        timeout_seconds: Maximum time allowed for verification
        metadata: Additional context for the claim

    """

    claim_id: str
    claimed_outcome: str
    verification_commands: Sequence[str] = field(default_factory=tuple)
    evidence_paths: Sequence[str] = field(default_factory=tuple)
    timeout_seconds: int = 60
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", _require_non_empty("claim_id", self.claim_id))
        object.__setattr__(self, "claimed_outcome", _require_non_empty("claimed_outcome", self.claimed_outcome))
        object.__setattr__(self, "verification_commands", _copy_sequence(self.verification_commands))
        object.__setattr__(self, "evidence_paths", _copy_sequence(self.evidence_paths))
        if self.timeout_seconds <= 0:
            msg = "timeout_seconds must be > 0"
            raise ValueError(msg)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class ExecutionResult:
    """Result of executing a single verification command.

    Attributes:
        command: The command that was executed
        stdout: Standard output from the command
        stderr: Standard error from the command
        return_code: Exit code (0 = success)
        execution_time_ms: Time taken to execute in milliseconds
        timed_out: Whether the command timed out

    """

    command: str
    stdout: str
    stderr: str
    return_code: int
    execution_time_ms: int
    timed_out: bool = False


@dataclass(frozen=True)
class VerificationReport:
    """Report of the verification process and its outcome.

    This is the canonical result type returned by the VerificationGuard Cell.
    It contains all evidence collected during verification and a final status.

    Attributes:
        claim_id: Reference to the original claim
        status: Final verification status (PASS/FAIL/BLOCKED/TIMEOUT/ERROR)
        command_results: Results of each executed verification command
        evidence_collected: List of evidence files that were found
        evidence_missing: List of evidence files that were not found
        mismatch_details: Specific mismatches between claim and reality
        recommendations: Suggested actions based on verification results
        execution_summary: Human-readable summary of the verification
        metadata: Additional context from the verification process

    """

    claim_id: str
    status: VerificationStatus
    command_results: Sequence[ExecutionResult] = field(default_factory=tuple)
    evidence_collected: Sequence[str] = field(default_factory=tuple)
    evidence_missing: Sequence[str] = field(default_factory=tuple)
    mismatch_details: Sequence[str] = field(default_factory=tuple)
    recommendations: Sequence[str] = field(default_factory=tuple)
    execution_summary: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", _require_non_empty("claim_id", self.claim_id))
        object.__setattr__(self, "command_results", tuple(self.command_results or ()))
        object.__setattr__(self, "evidence_collected", _copy_sequence(self.evidence_collected))
        object.__setattr__(self, "evidence_missing", _copy_sequence(self.evidence_missing))
        object.__setattr__(self, "mismatch_details", _copy_sequence(self.mismatch_details))
        object.__setattr__(self, "recommendations", _copy_sequence(self.recommendations))
        object.__setattr__(self, "execution_summary", str(self.execution_summary or ""))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))


@dataclass(frozen=True)
class VerifyCompletionCommandV1:
    """Command to verify a completion claim.

    This is the primary entry point for the VerificationGuard Cell.
    It encapsulates a claim and the context needed to verify it.

    Attributes:
        workspace: Path to the workspace being verified
        claim: The completion claim to verify
        strict_mode: If True, any mismatch fails verification
        allowed_commands: Optional override for command whitelist

    """

    workspace: str
    claim: VerificationClaim
    strict_mode: bool = True
    allowed_commands: Sequence[str] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if not isinstance(self.claim, VerificationClaim):
            msg = "claim must be a VerificationClaim instance"
            raise TypeError(msg)
        object.__setattr__(
            self, "allowed_commands", _copy_sequence(self.allowed_commands) if self.allowed_commands else None
        )


@dataclass(frozen=True)
class VerifyCompletionResultV1:
    """Result of a verification command.

    This is the canonical result type returned by VerifyCompletionCommandV1.

    Attributes:
        ok: Whether the verification completed successfully
        report: The detailed verification report
        error_code: Error code if verification failed to run
        error_message: Human-readable error description

    """

    ok: bool
    report: VerificationReport | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class VerificationCompletedEventV1:
    """Event emitted when verification completes.

    This event can be used for audit trails and monitoring.

    """

    claim_id: str
    status: VerificationStatus
    workspace: str
    verified_at: str


# ruff: noqa: N818
class VerificationGuardErrorV1(RuntimeError):
    """Error raised by the VerificationGuard Cell."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "verification_guard_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _copy_mapping(details)


@runtime_checkable
class IVerificationGuardService(Protocol):
    """Protocol for the VerificationGuard service."""

    def verify_completion(
        self,
        command: VerifyCompletionCommandV1,
    ) -> VerifyCompletionResultV1:
        """Verify a completion claim and return the result."""
        ...


__all__ = [
    "ExecutionResult",
    "IVerificationGuardService",
    "VerificationClaim",
    "VerificationCompletedEventV1",
    "VerificationGuardErrorV1",
    "VerificationReport",
    "VerificationStatus",
    "VerifyCompletionCommandV1",
    "VerifyCompletionResultV1",
]
