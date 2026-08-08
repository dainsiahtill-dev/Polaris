"""Verification Guard Cell - Public Contracts.

This module defines the public contracts for the VerificationGuard Cell,
which implements "Verification Before Completion" pattern inspired by
Superpowers design principles.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import InitVar, dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast, runtime_checkable

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


def _require_exact_token(name: str, value: str, *, max_length: int = 256) -> str:
    """Require a bounded, control-free identity without silent normalization."""

    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be an exact non-empty string")
    if len(value) > max_length:
        raise ValueError(f"{name} must contain at most {max_length} characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} must not contain control characters")
    return value


def _require_sha256(name: str, value: str) -> str:
    token = _require_exact_token(name, value, max_length=64)
    if len(token) != 64 or any(character not in "0123456789abcdef" for character in token):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return token


def _exact_sorted_tuple(name: str, values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise TypeError(f"{name} must be a list or tuple of strings")
    return tuple(sorted({_require_exact_token(f"{name}[{index}]", value) for index, value in enumerate(values)}))


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


CompletionEvidenceStatusV1 = Literal["passed", "failed"]
RepairCoverageStatusV1 = Literal[
    "executable_runtime",
    "metadata_only",
    "uncovered",
    "not_applicable",
    "unknown",
]
CompletionEvidenceStateV1 = Literal["missing", "failed"]
CompletionRetryClassV1 = Literal[
    "owner_rework",
    "deterministic_repair",
    "verification_evidence",
    "control_plane_reconcile",
    "dependency_blocked",
]
CompletionNextActionV1 = Literal[
    "publish_owner_rework",
    "run_deterministic_repair",
    "run_required_verifier",
    "refresh_owner_evidence",
    "wait_for_dependencies",
]


def _canonical_hash(schema_version: str, payload: Mapping[str, Any]) -> str:
    try:
        raw = json.dumps(
            {"schema_version": schema_version, **dict(payload)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"payload is not canonical JSON: {exc}") from exc
    return hashlib.sha256(raw).hexdigest()


ProjectArtifactSemanticRoleObservationV1 = Literal[
    "source",
    "manifest",
    "test",
    "entrypoint",
    "config",
    "docs",
    "assets",
]
ProjectObligationApplicabilityObservationV1 = Literal["required", "optional", "not_applicable"]
ProjectEntrypointKindObservationV1 = Literal["cli", "web", "api", "library"]
ProjectVerificationModalityObservationV1 = Literal["environment_prep", "build", "test", "lint", "entrypoint"]
ProjectKindObservationV1 = Literal["application", "library"]
ProjectCompletionPhysicalEvidenceKindV1 = Literal["artifact", "command"]

_PROJECT_COMPLETION_SCHEMA_V1: Literal["polaris.project_completion_contract.v1"] = (
    "polaris.project_completion_contract.v1"
)
_PROJECT_COMPLETION_ID_PREFIX = "project-completion-"
_ARTIFACT_ROLES = frozenset({"source", "manifest", "test", "entrypoint", "config", "docs", "assets"})
_APPLICABILITIES = frozenset({"required", "optional", "not_applicable"})
_ENTRYPOINT_KINDS = frozenset({"cli", "web", "api", "library"})
_VERIFICATION_MODALITIES = frozenset({"environment_prep", "build", "test", "lint", "entrypoint"})
_PROJECT_KINDS = frozenset({"application", "library"})


def _require_literal(name: str, value: str, allowed: frozenset[str]) -> str:
    token = _require_exact_token(name, value)
    if token not in allowed:
        raise ValueError(f"{name} must be one of {sorted(allowed)}")
    return token


def _optional_exact_token(name: str, value: str | None, *, max_length: int = 4096) -> str | None:
    if value is None:
        return None
    return _require_exact_token(name, value, max_length=max_length)


@dataclass(frozen=True, slots=True)
class ProjectKindAuthorityObservationV1:
    """Exact local observation of Factory-owned project-kind authority."""

    project_kind: ProjectKindObservationV1
    source_ref: str
    source_hash: str
    justification: str
    authority_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_kind", _require_literal("project_kind", self.project_kind, _PROJECT_KINDS))
        object.__setattr__(self, "source_ref", _require_exact_token("source_ref", self.source_ref, max_length=4096))
        object.__setattr__(self, "source_hash", _require_sha256("source_hash", self.source_hash))
        object.__setattr__(
            self,
            "justification",
            _require_exact_token("justification", self.justification, max_length=2048),
        )
        authority_hash = _require_sha256("authority_hash", self.authority_hash)
        expected_hash = hashlib.sha256(
            json.dumps(
                {
                    "domain": "polaris.project_completion_project_kind_authority.v1",
                    "project_kind": self.project_kind,
                    "source_ref": self.source_ref,
                    "source_hash": self.source_hash,
                    "justification": self.justification,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if authority_hash != expected_hash:
            raise ValueError("authority_hash must match exact project-kind authority fields")
        object.__setattr__(self, "authority_hash", authority_hash)

    def to_dict(self) -> dict[str, str]:
        return {
            "project_kind": self.project_kind,
            "source_ref": self.source_ref,
            "source_hash": self.source_hash,
            "justification": self.justification,
            "authority_hash": self.authority_hash,
        }


@dataclass(frozen=True, slots=True)
class ProjectArtifactObligationObservationV1:
    """VerificationGuard-owned immutable view of one CE artifact obligation."""

    obligation_id: str
    path: str
    semantic_role: ProjectArtifactSemanticRoleObservationV1
    applicability: ProjectObligationApplicabilityObservationV1
    owner_task_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "obligation_id", _require_exact_token("obligation_id", self.obligation_id))
        object.__setattr__(self, "path", _require_exact_token("path", self.path, max_length=4096))
        object.__setattr__(
            self, "semantic_role", _require_literal("semantic_role", self.semantic_role, _ARTIFACT_ROLES)
        )
        object.__setattr__(
            self, "applicability", _require_literal("applicability", self.applicability, _APPLICABILITIES)
        )
        object.__setattr__(self, "owner_task_id", _optional_exact_token("owner_task_id", self.owner_task_id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "path": self.path,
            "semantic_role": self.semantic_role,
            "applicability": self.applicability,
            "owner_task_id": self.owner_task_id,
        }


@dataclass(frozen=True, slots=True)
class ProjectEntrypointObligationObservationV1:
    """VerificationGuard-owned immutable view of one CE entrypoint obligation."""

    obligation_id: str
    kind: ProjectEntrypointKindObservationV1
    applicability: ProjectObligationApplicabilityObservationV1
    owner_task_id: str | None = None
    source_path: str | None = None
    runtime_path: str | None = None
    command: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "obligation_id", _require_exact_token("obligation_id", self.obligation_id))
        object.__setattr__(self, "kind", _require_literal("kind", self.kind, _ENTRYPOINT_KINDS))
        object.__setattr__(
            self, "applicability", _require_literal("applicability", self.applicability, _APPLICABILITIES)
        )
        object.__setattr__(self, "owner_task_id", _optional_exact_token("owner_task_id", self.owner_task_id))
        object.__setattr__(self, "source_path", _optional_exact_token("source_path", self.source_path))
        object.__setattr__(self, "runtime_path", _optional_exact_token("runtime_path", self.runtime_path))
        object.__setattr__(self, "command", _optional_exact_token("command", self.command))

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "kind": self.kind,
            "applicability": self.applicability,
            "owner_task_id": self.owner_task_id,
            "source_path": self.source_path,
            "runtime_path": self.runtime_path,
            "command": self.command,
        }


@dataclass(frozen=True, slots=True)
class ProjectVerificationObligationObservationV1:
    """VerificationGuard-owned immutable view of one CE verifier obligation."""

    obligation_id: str
    modality: ProjectVerificationModalityObservationV1
    command: str | None
    applicability: ProjectObligationApplicabilityObservationV1
    covers_obligation_ids: tuple[str, ...] = field(default_factory=tuple)
    owner_task_id: str | None = None
    command_authority_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "obligation_id", _require_exact_token("obligation_id", self.obligation_id))
        object.__setattr__(self, "modality", _require_literal("modality", self.modality, _VERIFICATION_MODALITIES))
        object.__setattr__(self, "command", _optional_exact_token("command", self.command))
        object.__setattr__(
            self, "applicability", _require_literal("applicability", self.applicability, _APPLICABILITIES)
        )
        object.__setattr__(
            self, "covers_obligation_ids", _exact_sorted_tuple("covers_obligation_ids", self.covers_obligation_ids)
        )
        object.__setattr__(self, "owner_task_id", _optional_exact_token("owner_task_id", self.owner_task_id))
        authority_hash = self.command_authority_hash
        if authority_hash is not None:
            authority_hash = _require_sha256("command_authority_hash", authority_hash)
        object.__setattr__(self, "command_authority_hash", authority_hash)

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "modality": self.modality,
            "command": self.command,
            "applicability": self.applicability,
            "covers_obligation_ids": list(self.covers_obligation_ids),
            "owner_task_id": self.owner_task_id,
            "command_authority_hash": self.command_authority_hash,
        }


@dataclass(frozen=True, slots=True)
class ProjectVerificationCommandAuthorityObservationV1:
    """Complete immutable observation of one PM-owned verifier command."""

    task_id: str
    modality: ProjectVerificationModalityObservationV1
    argv: tuple[str, ...]
    cwd: str
    command: str
    authority_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_exact_token("task_id", self.task_id))
        object.__setattr__(self, "modality", _require_literal("modality", self.modality, _VERIFICATION_MODALITIES))
        if type(self.argv) is not tuple or not self.argv:
            raise TypeError("argv must be an exact non-empty tuple")
        object.__setattr__(
            self,
            "argv",
            tuple(_require_exact_token(f"argv[{index}]", item) for index, item in enumerate(self.argv)),
        )
        object.__setattr__(self, "cwd", _require_exact_token("cwd", self.cwd, max_length=4096))
        object.__setattr__(self, "command", _require_exact_token("command", self.command, max_length=4096))
        object.__setattr__(self, "authority_hash", _require_sha256("authority_hash", self.authority_hash))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "modality": self.modality,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "command": self.command,
            "authority_hash": self.authority_hash,
        }


@dataclass(frozen=True, slots=True)
class ProjectCompletionObligationsObservationV1:
    """Complete immutable obligation groups observed from the CE owner."""

    artifacts: tuple[ProjectArtifactObligationObservationV1, ...]
    entrypoints: tuple[ProjectEntrypointObligationObservationV1, ...]
    verification: tuple[ProjectVerificationObligationObservationV1, ...]

    def __post_init__(self) -> None:
        groups = (
            ("artifacts", self.artifacts, ProjectArtifactObligationObservationV1),
            ("entrypoints", self.entrypoints, ProjectEntrypointObligationObservationV1),
            ("verification", self.verification, ProjectVerificationObligationObservationV1),
        )
        for name, values, expected_type in groups:
            if type(values) is not tuple or any(type(item) is not expected_type for item in values):
                raise TypeError(f"{name} must be an exact tuple of {expected_type.__name__}")
            ordered = tuple(sorted(values, key=lambda item: item.obligation_id))
            object.__setattr__(self, name, ordered)
        obligation_ids = [item.obligation_id for item in self.artifacts]
        obligation_ids.extend(item.obligation_id for item in self.entrypoints)
        obligation_ids.extend(item.obligation_id for item in self.verification)
        if len(obligation_ids) != len(set(obligation_ids)):
            raise ValueError("obligation_id must be unique across completion obligations")

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "artifacts": [item.to_dict() for item in self.artifacts],
            "entrypoints": [item.to_dict() for item in self.entrypoints],
            "verification": [item.to_dict() for item in self.verification],
        }


_PROJECT_COMPLETION_CONTRACT_OBSERVATION_TOKEN = object()


@dataclass(frozen=True, slots=True)
class ProjectCompletionContractObservationV1:
    """Sealed, complete, content-hash-verified local observation of the CE contract.

    This is not a second completion-contract authority. Bootstrap maps the CE
    owner contract into this DTO; VerificationGuard independently recomputes
    the owner schema hash before accepting it.
    """

    contract_id: str
    contract_hash: str
    project_id: str
    run_id: str
    project_kind: ProjectKindObservationV1
    project_kind_authority: ProjectKindAuthorityObservationV1
    pm_contract_hash: str
    covered_task_ids: tuple[str, ...]
    obligations: ProjectCompletionObligationsObservationV1
    completion_predicate_version: str
    verifier_policy_hash: str
    verifier_policy_snapshot_hash: str
    verification_command_authority: tuple[ProjectVerificationCommandAuthorityObservationV1, ...]
    schema_version: Literal["polaris.project_completion_contract.v1"] = _PROJECT_COMPLETION_SCHEMA_V1
    _authority_token: InitVar[object | None] = None

    def __post_init__(self, _authority_token: object | None) -> None:
        if _authority_token is not _PROJECT_COMPLETION_CONTRACT_OBSERVATION_TOKEN:
            raise ValueError("project completion contract observation must be sealed by factory.verification_guard")
        if self.schema_version != _PROJECT_COMPLETION_SCHEMA_V1:
            raise ValueError(f"schema_version must equal {_PROJECT_COMPLETION_SCHEMA_V1!r}")
        object.__setattr__(self, "project_id", _require_exact_token("project_id", self.project_id))
        object.__setattr__(self, "run_id", _require_exact_token("run_id", self.run_id))
        object.__setattr__(self, "project_kind", _require_literal("project_kind", self.project_kind, _PROJECT_KINDS))
        if type(self.project_kind_authority) is not ProjectKindAuthorityObservationV1:
            raise TypeError("project_kind_authority must be exact ProjectKindAuthorityObservationV1")
        if self.project_kind_authority.project_kind != self.project_kind:
            raise ValueError("project_kind must match project_kind_authority")
        object.__setattr__(self, "pm_contract_hash", _require_sha256("pm_contract_hash", self.pm_contract_hash))
        object.__setattr__(self, "covered_task_ids", _exact_sorted_tuple("covered_task_ids", self.covered_task_ids))
        if not self.covered_task_ids:
            raise ValueError("covered_task_ids must not be empty")
        if type(self.obligations) is not ProjectCompletionObligationsObservationV1:
            raise TypeError("obligations must be an exact ProjectCompletionObligationsObservationV1")
        object.__setattr__(
            self,
            "completion_predicate_version",
            _require_exact_token("completion_predicate_version", self.completion_predicate_version),
        )
        object.__setattr__(
            self, "verifier_policy_hash", _require_sha256("verifier_policy_hash", self.verifier_policy_hash)
        )
        object.__setattr__(
            self,
            "verifier_policy_snapshot_hash",
            _require_sha256("verifier_policy_snapshot_hash", self.verifier_policy_snapshot_hash),
        )
        if type(self.verification_command_authority) is not tuple or any(
            type(item) is not ProjectVerificationCommandAuthorityObservationV1
            for item in self.verification_command_authority
        ):
            raise TypeError(
                "verification_command_authority must be an exact tuple of "
                "ProjectVerificationCommandAuthorityObservationV1"
            )
        command_authority = tuple(sorted(self.verification_command_authority, key=lambda item: item.authority_hash))
        object.__setattr__(self, "verification_command_authority", command_authority)
        contract_hash = _require_sha256("contract_hash", self.contract_hash)
        expected_hash = hashlib.sha256(
            json.dumps(self.to_seed_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        if contract_hash != expected_hash:
            raise ValueError("contract_hash must match the complete observed CE contract payload")
        object.__setattr__(self, "contract_hash", contract_hash)
        expected_id = f"{_PROJECT_COMPLETION_ID_PREFIX}{contract_hash[:24]}"
        if self.contract_id != expected_id:
            raise ValueError("contract_id must match the observed completion contract hash")
        object.__setattr__(self, "contract_id", expected_id)

    def to_seed_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "project_kind": self.project_kind,
            "project_kind_authority": self.project_kind_authority.to_dict(),
            "pm_contract_hash": self.pm_contract_hash,
            "covered_task_ids": list(self.covered_task_ids),
            "obligations": self.obligations.to_dict(),
            "completion_predicate_version": self.completion_predicate_version,
            "verifier_policy_hash": self.verifier_policy_hash,
            "verifier_policy_snapshot_hash": self.verifier_policy_snapshot_hash,
            "verification_command_authority": [item.to_dict() for item in self.verification_command_authority],
        }


@dataclass(frozen=True, slots=True)
class QueryProjectCompletionDiagnosticsV1:
    """Query owner facts by exact workspace/project/run/contract identity only."""

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_exact_token("workspace", self.workspace, max_length=4096))
        object.__setattr__(self, "project_id", _require_exact_token("project_id", self.project_id))
        object.__setattr__(self, "run_id", _require_exact_token("run_id", self.run_id))
        object.__setattr__(
            self,
            "completion_contract_hash",
            _require_sha256("completion_contract_hash", self.completion_contract_hash),
        )


@dataclass(frozen=True, slots=True)
class RunProjectCompletionEvidenceCommandV1:
    """Request one physical obligation effect by identity only.

    Canonical argv, cwd, artifact paths, evidence and verdicts are deliberately
    absent.  VerificationGuard derives them from the exact CE contract.
    """

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    obligation_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_exact_token("workspace", self.workspace, max_length=4096))
        object.__setattr__(self, "project_id", _require_exact_token("project_id", self.project_id))
        object.__setattr__(self, "run_id", _require_exact_token("run_id", self.run_id))
        object.__setattr__(
            self,
            "completion_contract_hash",
            _require_sha256("completion_contract_hash", self.completion_contract_hash),
        )
        object.__setattr__(self, "obligation_id", _require_exact_token("obligation_id", self.obligation_id))


@dataclass(frozen=True, slots=True)
class ProjectCompletionPhysicalArtifactInputV1:
    """Contract-derived artifact path bound into a physical command receipt."""

    obligation_id: str
    path: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "obligation_id", _require_exact_token("obligation_id", self.obligation_id))
        object.__setattr__(self, "path", _require_exact_token("path", self.path, max_length=4096))


@dataclass(frozen=True, slots=True)
class ProjectCompletionPhysicalEvidenceIntentV1:
    """Private-sealed exact intent delivered to the physical owner port."""

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    obligation_id: str
    owner_task_id: str
    kind: ProjectCompletionPhysicalEvidenceKindV1
    artifact_path: str | None
    modality: ProjectVerificationModalityObservationV1 | None
    argv: tuple[str, ...]
    cwd: str | None
    command_authority_hash: str | None
    input_artifacts: tuple[ProjectCompletionPhysicalArtifactInputV1, ...]
    timeout_seconds: float
    _authority_token: InitVar[object | None] = None

    def __post_init__(self, _authority_token: object | None) -> None:
        from polaris.cells.factory.verification_guard.internal.project_physical_evidence import (
            _is_project_completion_physical_intent_seal,
        )

        if not _is_project_completion_physical_intent_seal(_authority_token):
            raise ProjectCompletionOwnerObservationV1Error(
                "project_completion_physical_intent_seal_required",
                "Project completion physical evidence intent must be sealed by factory.verification_guard",
            )
        for name, limit in (
            ("workspace", 4096),
            ("project_id", 256),
            ("run_id", 256),
            ("obligation_id", 256),
            ("owner_task_id", 256),
        ):
            object.__setattr__(self, name, _require_exact_token(name, getattr(self, name), max_length=limit))
        object.__setattr__(
            self,
            "completion_contract_hash",
            _require_sha256("completion_contract_hash", self.completion_contract_hash),
        )
        if self.kind not in {"artifact", "command"}:
            raise ValueError("kind must be 'artifact' or 'command'")
        artifact_path = _optional_exact_token("artifact_path", self.artifact_path)
        modality = self.modality
        argv = self.argv
        cwd = _optional_exact_token("cwd", self.cwd)
        authority_hash = self.command_authority_hash
        if authority_hash is not None:
            authority_hash = _require_sha256("command_authority_hash", authority_hash)
        if type(argv) is not tuple:
            raise TypeError("argv must be an exact tuple")
        argv = tuple(_require_exact_token(f"argv[{index}]", value) for index, value in enumerate(argv))
        if type(self.input_artifacts) is not tuple or any(
            type(item) is not ProjectCompletionPhysicalArtifactInputV1 for item in self.input_artifacts
        ):
            raise TypeError("input_artifacts must contain exact ProjectCompletionPhysicalArtifactInputV1 values")
        inputs = tuple(sorted(self.input_artifacts, key=lambda item: (item.obligation_id, item.path)))
        if len({item.obligation_id for item in inputs}) != len(inputs):
            raise ValueError("input_artifacts must contain unique obligation_id values")
        if self.kind == "artifact":
            if (
                artifact_path is None
                or modality is not None
                or argv
                or cwd is not None
                or authority_hash is not None
                or inputs
            ):
                raise ValueError("artifact intent must contain only one artifact_path")
        else:
            if artifact_path is not None or modality not in _VERIFICATION_MODALITIES:
                raise ValueError("command intent requires one supported modality and no artifact_path")
            if not argv or cwd is None or authority_hash is None or not inputs:
                raise ValueError("command intent requires argv/cwd/authority hash/input artifacts")
        if isinstance(self.timeout_seconds, bool) or float(self.timeout_seconds) <= 0:
            raise ValueError("timeout_seconds must be > 0")
        object.__setattr__(self, "artifact_path", artifact_path)
        object.__setattr__(self, "argv", argv)
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(self, "command_authority_hash", authority_hash)
        object.__setattr__(self, "input_artifacts", inputs)
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))


@dataclass(frozen=True, slots=True)
class ProjectCompletionPhysicalEvidenceEffectV1:
    """Physical effect locator only; never a project-completion verdict."""

    code: str
    spawned: bool
    receipt_ref: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _require_exact_token("code", self.code))
        if type(self.spawned) is not bool:
            raise TypeError("spawned must be a bool")
        object.__setattr__(
            self,
            "receipt_ref",
            _optional_exact_token("receipt_ref", self.receipt_ref, max_length=2048),
        )


@runtime_checkable
class ProjectCompletionPhysicalEvidencePortV1(Protocol):
    """Bootstrap-bound owner port for physical artifact/command effects."""

    def materialize_project_completion_evidence(
        self,
        intent: ProjectCompletionPhysicalEvidenceIntentV1,
        /,
    ) -> ProjectCompletionPhysicalEvidenceEffectV1:
        """Materialize one sealed intent through the physical owner."""
        ...


@dataclass(frozen=True, slots=True)
class RunProjectCompletionEvidenceResultV1:
    """One obligation-effect result; deliberately not a completion result."""

    code: str
    obligation_id: str
    spawned: bool
    receipt_ref: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _require_exact_token("code", self.code))
        object.__setattr__(self, "obligation_id", _require_exact_token("obligation_id", self.obligation_id))
        if type(self.spawned) is not bool:
            raise TypeError("spawned must be a bool")
        object.__setattr__(
            self,
            "receipt_ref",
            _optional_exact_token("receipt_ref", self.receipt_ref, max_length=2048),
        )


@dataclass(frozen=True, slots=True)
class ProjectCompletionEvidenceV1:
    """Bootstrap-owner evidence bound to one obligation and owner task.

    The hash is derived, never caller supplied.  It binds every identity and
    receipt field that may affect completion classification.
    """

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    obligation_id: str
    owner_task_id: str
    owner_module_id: str
    status: CompletionEvidenceStatusV1
    owner_evidence_refs: tuple[str, ...]
    verifier_receipt_ref: str | None = None
    verifier_exit_code: int | None = None
    artifact_path: str | None = None
    artifact_hash: str | None = None
    verifier_modality: ProjectVerificationModalityObservationV1 | None = None
    verifier_argv: tuple[str, ...] = field(default_factory=tuple)
    verifier_cwd: str | None = None
    verifier_command_authority_hash: str | None = None
    verifier_input_artifact_hash: str | None = None
    verifier_timed_out: bool | None = None
    verifier_output_hash: str | None = None
    owner_evidence_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for name, limit in (
            ("workspace", 4096),
            ("project_id", 256),
            ("run_id", 256),
            ("obligation_id", 256),
            ("owner_task_id", 256),
            ("owner_module_id", 256),
        ):
            object.__setattr__(self, name, _require_exact_token(name, getattr(self, name), max_length=limit))
        object.__setattr__(
            self,
            "completion_contract_hash",
            _require_sha256("completion_contract_hash", self.completion_contract_hash),
        )
        if self.status not in {"passed", "failed"}:
            raise ValueError("status must be 'passed' or 'failed'")
        refs = _exact_sorted_tuple("owner_evidence_refs", self.owner_evidence_refs)
        if not refs:
            raise ValueError("owner_evidence_refs must contain owner-produced evidence")
        object.__setattr__(self, "owner_evidence_refs", refs)
        receipt = self.verifier_receipt_ref
        exit_code = self.verifier_exit_code
        if receipt is not None:
            receipt = _require_exact_token("verifier_receipt_ref", receipt, max_length=2048)
        if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
            raise TypeError("verifier_exit_code must be an int or None")
        artifact_path = _optional_exact_token("artifact_path", self.artifact_path, max_length=4096)
        artifact_hash = self.artifact_hash
        if artifact_hash is not None:
            artifact_hash = _require_sha256("artifact_hash", artifact_hash)
        modality = self.verifier_modality
        if modality is not None:
            modality = cast(
                ProjectVerificationModalityObservationV1,
                _require_literal("verifier_modality", modality, _VERIFICATION_MODALITIES),
            )
        if type(self.verifier_argv) is not tuple:
            raise TypeError("verifier_argv must be an exact tuple")
        verifier_argv = tuple(
            _require_exact_token(f"verifier_argv[{index}]", value) for index, value in enumerate(self.verifier_argv)
        )
        verifier_cwd = _optional_exact_token("verifier_cwd", self.verifier_cwd, max_length=4096)
        command_authority_hash = self.verifier_command_authority_hash
        if command_authority_hash is not None:
            command_authority_hash = _require_sha256("verifier_command_authority_hash", command_authority_hash)
        input_artifact_hash = self.verifier_input_artifact_hash
        if input_artifact_hash is not None:
            input_artifact_hash = _require_sha256("verifier_input_artifact_hash", input_artifact_hash)
        timed_out = self.verifier_timed_out
        if timed_out is not None and type(timed_out) is not bool:
            raise TypeError("verifier_timed_out must be a bool or None")
        output_hash = self.verifier_output_hash
        if output_hash is not None:
            output_hash = _require_sha256("verifier_output_hash", output_hash)
        extended_verifier_fields = (
            modality,
            verifier_argv or None,
            verifier_cwd,
            command_authority_hash,
            input_artifact_hash,
            timed_out,
            output_hash,
        )
        if receipt is None:
            if exit_code is not None or any(item is not None for item in extended_verifier_fields):
                raise ValueError("verifier fields require verifier_receipt_ref")
        elif timed_out is False and exit_code is None:
            raise ValueError("non-timeout verifier receipt requires verifier_exit_code")
        if (artifact_path is None) is not (artifact_hash is None):
            raise ValueError("artifact_path and artifact_hash must be present or absent together")
        if artifact_path is not None and receipt is not None:
            raise ValueError("artifact evidence and command receipt fields are mutually exclusive")
        object.__setattr__(self, "verifier_receipt_ref", receipt)
        object.__setattr__(self, "artifact_path", artifact_path)
        object.__setattr__(self, "artifact_hash", artifact_hash)
        object.__setattr__(self, "verifier_modality", modality)
        object.__setattr__(self, "verifier_argv", verifier_argv)
        object.__setattr__(self, "verifier_cwd", verifier_cwd)
        object.__setattr__(self, "verifier_command_authority_hash", command_authority_hash)
        object.__setattr__(self, "verifier_input_artifact_hash", input_artifact_hash)
        object.__setattr__(self, "verifier_timed_out", timed_out)
        object.__setattr__(self, "verifier_output_hash", output_hash)
        object.__setattr__(
            self,
            "owner_evidence_hash",
            _canonical_hash(
                "factory.verification_guard.owner-evidence.v1",
                {
                    "workspace": self.workspace,
                    "project_id": self.project_id,
                    "run_id": self.run_id,
                    "completion_contract_hash": self.completion_contract_hash,
                    "obligation_id": self.obligation_id,
                    "owner_task_id": self.owner_task_id,
                    "owner_module_id": self.owner_module_id,
                    "status": self.status,
                    "owner_evidence_refs": list(refs),
                    "verifier_receipt_ref": receipt,
                    "verifier_exit_code": exit_code,
                    "artifact_path": artifact_path,
                    "artifact_hash": artifact_hash,
                    "verifier_modality": modality,
                    "verifier_argv": list(verifier_argv),
                    "verifier_cwd": verifier_cwd,
                    "verifier_command_authority_hash": command_authority_hash,
                    "verifier_input_artifact_hash": input_artifact_hash,
                    "verifier_timed_out": timed_out,
                    "verifier_output_hash": output_hash,
                },
            ),
        )


@dataclass(frozen=True, slots=True)
class ProjectRepairCoverageV1:
    """Director-runtime-owned coverage proof; never caller authorization."""

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    obligation_id: str
    owner_task_id: str
    status: RepairCoverageStatusV1
    evidence_ref: str
    source_tool: str | None = None
    evidence_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for name, limit in (
            ("workspace", 4096),
            ("project_id", 256),
            ("run_id", 256),
            ("obligation_id", 256),
            ("owner_task_id", 256),
            ("evidence_ref", 2048),
        ):
            object.__setattr__(self, name, _require_exact_token(name, getattr(self, name), max_length=limit))
        object.__setattr__(
            self,
            "completion_contract_hash",
            _require_sha256("completion_contract_hash", self.completion_contract_hash),
        )
        if self.status not in {
            "executable_runtime",
            "metadata_only",
            "uncovered",
            "not_applicable",
            "unknown",
        }:
            raise ValueError("invalid repair coverage status")
        source_tool = self.source_tool
        if source_tool is not None:
            source_tool = _require_exact_token("source_tool", source_tool)
        if self.status == "executable_runtime" and source_tool is None:
            raise ValueError("executable_runtime repair coverage requires source_tool")
        if self.status != "executable_runtime" and source_tool is not None:
            raise ValueError("only executable_runtime repair coverage may declare source_tool")
        object.__setattr__(self, "source_tool", source_tool)
        object.__setattr__(
            self,
            "evidence_hash",
            _canonical_hash(
                "factory.verification_guard.repair-coverage.v1",
                {
                    "workspace": self.workspace,
                    "project_id": self.project_id,
                    "run_id": self.run_id,
                    "completion_contract_hash": self.completion_contract_hash,
                    "obligation_id": self.obligation_id,
                    "owner_task_id": self.owner_task_id,
                    "status": self.status,
                    "evidence_ref": self.evidence_ref,
                    "source_tool": source_tool,
                },
            ),
        )


@dataclass(frozen=True, slots=True)
class ProjectCompletionOwnerObservationV1:
    """Exact output from the bootstrap owner adapter before same-Cell sealing."""

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    contract: ProjectCompletionContractObservationV1
    evidence: tuple[ProjectCompletionEvidenceV1, ...]
    repair_coverage: tuple[ProjectRepairCoverageV1, ...]

    def __post_init__(self) -> None:
        identity = QueryProjectCompletionDiagnosticsV1(
            workspace=self.workspace,
            project_id=self.project_id,
            run_id=self.run_id,
            completion_contract_hash=self.completion_contract_hash,
        )
        for name in ("workspace", "project_id", "run_id", "completion_contract_hash"):
            object.__setattr__(self, name, getattr(identity, name))
        if type(self.contract) is not ProjectCompletionContractObservationV1:
            raise TypeError("contract must be an exact ProjectCompletionContractObservationV1")
        if (
            self.contract.project_id,
            self.contract.run_id,
            self.contract.contract_hash,
        ) != (self.project_id, self.run_id, self.completion_contract_hash):
            raise ValueError("contract observation identity must match owner observation identity")
        for name, values, expected in (
            ("evidence", self.evidence, ProjectCompletionEvidenceV1),
            ("repair_coverage", self.repair_coverage, ProjectRepairCoverageV1),
        ):
            if type(values) is not tuple or any(type(item) is not expected for item in values):
                raise TypeError(f"{name} must be an exact tuple of {expected.__name__}")
            obligation_ids = [item.obligation_id for item in values]
            if len(obligation_ids) != len(set(obligation_ids)):
                raise ValueError(f"{name} must contain at most one row per obligation")


@runtime_checkable
class ProjectCompletionOwnerObservationPortV1(Protocol):
    """Bootstrap-bound port that alone may collect completion owner facts."""

    def observe_project_completion(
        self,
        *,
        workspace: str,
        project_id: str,
        run_id: str,
        completion_contract_hash: str,
    ) -> ProjectCompletionOwnerObservationV1:
        """Read exact CE, TaskRuntime, TaskBoundary, RunLedger, and repair facts."""
        ...


class ProjectCompletionOwnerObservationV1Error(ValueError):
    """Typed fail-closed owner-binding error."""

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = _require_exact_token("error_code", error_code)
        super().__init__(message)


_PROJECT_COMPLETION_OWNER_BUNDLE_TOKEN = object()


@dataclass(frozen=True, slots=True)
class ProjectCompletionOwnerEvidenceBundleV1:
    """Private-sealed evidence set accepted by the deterministic evaluator."""

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    contract: ProjectCompletionContractObservationV1
    evidence: tuple[ProjectCompletionEvidenceV1, ...]
    repair_coverage: tuple[ProjectRepairCoverageV1, ...]
    bundle_hash: str
    _authority_token: InitVar[object | None] = None

    def __post_init__(self, _authority_token: object | None) -> None:
        if _authority_token is not _PROJECT_COMPLETION_OWNER_BUNDLE_TOKEN:
            raise ProjectCompletionOwnerObservationV1Error(
                "project_completion_owner_bundle_seal_required",
                "Owner evidence bundle must be sealed by factory.verification_guard",
            )
        _require_sha256("bundle_hash", self.bundle_hash)


@dataclass(frozen=True)
class ProjectCompletionDiagnosticV1:
    """One deterministic residual against a required completion obligation."""

    diagnostic_id: str
    archetype: str
    evidence_state: CompletionEvidenceStateV1
    primary_module_id: str
    obligation_id: str
    owner_task_id: str
    affected_target: str
    owner_evidence_refs: tuple[str, ...]
    retry_class: CompletionRetryClassV1
    allowed_next_action: CompletionNextActionV1
    dependency_ids: tuple[str, ...]
    repair_coverage: RepairCoverageStatusV1
    repair_source_tool: str | None
    repair_coverage_evidence_ref: str | None
    repair_coverage_evidence_hash: str | None
    required_verifier_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "diagnostic_id",
            "archetype",
            "primary_module_id",
            "obligation_id",
            "owner_task_id",
            "affected_target",
        ):
            object.__setattr__(self, name, _require_exact_token(name, getattr(self, name), max_length=2048))
        if self.evidence_state not in {"missing", "failed"}:
            raise ValueError("evidence_state must be 'missing' or 'failed'")
        retry_actions = {
            "owner_rework": "publish_owner_rework",
            "deterministic_repair": "run_deterministic_repair",
            "verification_evidence": "run_required_verifier",
            "control_plane_reconcile": "refresh_owner_evidence",
            "dependency_blocked": "wait_for_dependencies",
        }
        if retry_actions.get(self.retry_class) != self.allowed_next_action:
            raise ValueError("retry_class and allowed_next_action must form a supported pair")
        if self.repair_coverage not in {
            "executable_runtime",
            "metadata_only",
            "uncovered",
            "not_applicable",
            "unknown",
        }:
            raise ValueError("invalid repair_coverage")
        object.__setattr__(
            self,
            "owner_evidence_refs",
            _exact_sorted_tuple("owner_evidence_refs", self.owner_evidence_refs),
        )
        object.__setattr__(self, "dependency_ids", _exact_sorted_tuple("dependency_ids", self.dependency_ids))
        object.__setattr__(
            self,
            "required_verifier_ids",
            _exact_sorted_tuple("required_verifier_ids", self.required_verifier_ids),
        )
        if self.repair_source_tool is not None:
            object.__setattr__(
                self,
                "repair_source_tool",
                _require_exact_token("repair_source_tool", self.repair_source_tool),
            )
        if self.repair_coverage_evidence_ref is not None:
            object.__setattr__(
                self,
                "repair_coverage_evidence_ref",
                _require_exact_token(
                    "repair_coverage_evidence_ref",
                    self.repair_coverage_evidence_ref,
                    max_length=2048,
                ),
            )
        if self.repair_coverage_evidence_hash is not None:
            object.__setattr__(
                self,
                "repair_coverage_evidence_hash",
                _require_sha256("repair_coverage_evidence_hash", self.repair_coverage_evidence_hash),
            )
        if (self.repair_coverage_evidence_ref is None) is not (self.repair_coverage_evidence_hash is None):
            raise ValueError("repair coverage evidence ref and hash must be present or absent together")
        if self.repair_coverage != "unknown" and self.repair_coverage_evidence_ref is None:
            raise ValueError("known repair coverage requires owner evidence ref and hash")
        if self.repair_coverage == "executable_runtime" and self.repair_source_tool is None:
            raise ValueError("executable_runtime repair coverage requires repair_source_tool")
        if self.retry_class == "deterministic_repair" and self.repair_coverage != "executable_runtime":
            raise ValueError("deterministic_repair retry requires executable_runtime coverage")
        if self.dependency_ids and self.retry_class != "dependency_blocked":
            raise ValueError("diagnostics with dependencies must use dependency_blocked retry_class")
        if self.retry_class == "dependency_blocked" and not self.dependency_ids:
            raise ValueError("dependency_blocked diagnostics require dependency_ids")


_PROJECT_COMPLETION_DIAGNOSTICS_AUTHORITY_TOKEN = object()


@dataclass(frozen=True, slots=True)
class ProjectCompletionDiagnosticsV1:
    """Owner-sealed, stable residual set; it is not a completion verdict."""

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    owner_bundle_hash: str
    diagnostics: tuple[ProjectCompletionDiagnosticV1, ...]
    passed_obligation_ids: tuple[str, ...]
    missing_obligation_ids: tuple[str, ...]
    failed_obligation_ids: tuple[str, ...]
    non_blocking_obligation_ids: tuple[str, ...]
    evaluated_obligation_ids: tuple[str, ...] = field(init=False)
    authority_bound: bool = field(init=False)
    _authority_token: InitVar[object | None] = None

    def __post_init__(self, _authority_token: object | None) -> None:
        if _authority_token is not _PROJECT_COMPLETION_DIAGNOSTICS_AUTHORITY_TOKEN:
            raise ProjectCompletionOwnerObservationV1Error(
                "project_completion_diagnostics_seal_required",
                "Project completion diagnostics must be created from a sealed owner bundle",
            )
        object.__setattr__(self, "workspace", _require_exact_token("workspace", self.workspace, max_length=4096))
        object.__setattr__(self, "project_id", _require_exact_token("project_id", self.project_id))
        object.__setattr__(self, "run_id", _require_exact_token("run_id", self.run_id))
        object.__setattr__(
            self,
            "completion_contract_hash",
            _require_sha256("completion_contract_hash", self.completion_contract_hash),
        )
        object.__setattr__(self, "owner_bundle_hash", _require_sha256("owner_bundle_hash", self.owner_bundle_hash))
        if not isinstance(self.diagnostics, (list, tuple)) or any(
            not isinstance(item, ProjectCompletionDiagnosticV1) for item in self.diagnostics
        ):
            raise TypeError("diagnostics must contain ProjectCompletionDiagnosticV1 values")
        diagnostics = tuple(sorted(self.diagnostics, key=lambda item: (item.obligation_id, item.diagnostic_id)))
        if len({item.diagnostic_id for item in diagnostics}) != len(diagnostics):
            raise ValueError("diagnostic_id values must be unique")
        if len({item.obligation_id for item in diagnostics}) != len(diagnostics):
            raise ValueError("diagnostics must contain at most one row per obligation_id")
        diagnostic_ids = {item.diagnostic_id for item in diagnostics}
        adjacency = {item.diagnostic_id: item.dependency_ids for item in diagnostics}
        for item in diagnostics:
            unknown_dependencies = set(item.dependency_ids) - diagnostic_ids
            if unknown_dependencies or item.diagnostic_id in item.dependency_ids:
                raise ValueError("dependency_ids must reference other diagnostics in this result")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(diagnostic_id: str) -> None:
            if diagnostic_id in visiting:
                raise ValueError("diagnostic dependency graph must be acyclic")
            if diagnostic_id in visited:
                return
            visiting.add(diagnostic_id)
            for dependency_id in adjacency[diagnostic_id]:
                visit(dependency_id)
            visiting.remove(diagnostic_id)
            visited.add(diagnostic_id)

        for diagnostic_id in sorted(diagnostic_ids):
            visit(diagnostic_id)
        object.__setattr__(self, "diagnostics", diagnostics)
        passed = _exact_sorted_tuple("passed_obligation_ids", self.passed_obligation_ids)
        missing = _exact_sorted_tuple("missing_obligation_ids", self.missing_obligation_ids)
        failed = _exact_sorted_tuple("failed_obligation_ids", self.failed_obligation_ids)
        non_blocking = _exact_sorted_tuple(
            "non_blocking_obligation_ids",
            self.non_blocking_obligation_ids,
        )
        if set(missing).intersection(failed):
            raise ValueError("missing and failed obligation ids must be disjoint")
        if set(passed).intersection(set(missing) | set(failed)):
            raise ValueError("passed obligation ids must be disjoint from residual ids")
        if set(non_blocking).intersection(set(passed) | set(missing) | set(failed)):
            raise ValueError("non-blocking obligation ids must be disjoint from required evidence ids")
        expected_missing = {item.obligation_id for item in diagnostics if item.evidence_state == "missing"}
        expected_failed = {item.obligation_id for item in diagnostics if item.evidence_state == "failed"}
        if set(missing) != expected_missing or set(failed) != expected_failed:
            raise ValueError("missing and failed ids must match diagnostic evidence states")
        object.__setattr__(self, "passed_obligation_ids", passed)
        object.__setattr__(self, "missing_obligation_ids", missing)
        object.__setattr__(self, "failed_obligation_ids", failed)
        object.__setattr__(self, "non_blocking_obligation_ids", non_blocking)
        object.__setattr__(
            self,
            "evaluated_obligation_ids",
            tuple(sorted((*passed, *missing, *failed, *non_blocking))),
        )
        object.__setattr__(self, "authority_bound", True)


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


@runtime_checkable
class IProjectCompletionDiagnosticsService(Protocol):
    """Protocol for exact, owner-bound completion diagnostics queries."""

    def query_project_completion_diagnostics(
        self,
        query: QueryProjectCompletionDiagnosticsV1,
    ) -> ProjectCompletionDiagnosticsV1:
        """Read and evaluate every required obligation from owner facts."""
        ...


__all__ = [
    "CompletionEvidenceStateV1",
    "CompletionEvidenceStatusV1",
    "CompletionNextActionV1",
    "CompletionRetryClassV1",
    "ExecutionResult",
    "IProjectCompletionDiagnosticsService",
    "IVerificationGuardService",
    "ProjectArtifactObligationObservationV1",
    "ProjectCompletionContractObservationV1",
    "ProjectCompletionDiagnosticV1",
    "ProjectCompletionDiagnosticsV1",
    "ProjectCompletionEvidenceV1",
    "ProjectCompletionObligationsObservationV1",
    "ProjectCompletionOwnerEvidenceBundleV1",
    "ProjectCompletionOwnerObservationPortV1",
    "ProjectCompletionOwnerObservationV1",
    "ProjectCompletionOwnerObservationV1Error",
    "ProjectEntrypointObligationObservationV1",
    "ProjectKindObservationV1",
    "ProjectRepairCoverageV1",
    "ProjectVerificationCommandAuthorityObservationV1",
    "ProjectVerificationModalityObservationV1",
    "ProjectVerificationObligationObservationV1",
    "QueryProjectCompletionDiagnosticsV1",
    "RepairCoverageStatusV1",
    "VerificationClaim",
    "VerificationCompletedEventV1",
    "VerificationGuardErrorV1",
    "VerificationReport",
    "VerificationStatus",
    "VerifyCompletionCommandV1",
    "VerifyCompletionResultV1",
]
