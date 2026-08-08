"""Owner-sealed physical receipts for project verification effects.

This API records facts only.  It never accepts or returns a project-completion
verdict.  Artifact bytes and physical process results are observed by
``runtime.execution_broker`` and content-bound into private-sealed receipts.
"""

from __future__ import annotations

import math
from dataclasses import InitVar, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, runtime_checkable

ProjectVerificationModalityV1 = Literal["environment_prep", "build", "test", "lint", "entrypoint"]

_MODALITIES = frozenset({"environment_prep", "build", "test", "lint", "entrypoint"})
_OWNER_MODULE_ID = "runtime.execution_broker"
_MAX_TIMEOUT_SECONDS = 3600.0


def _require_exact(name: str, value: str, *, max_length: int = 4096) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be an exact non-empty string")
    if len(value) > max_length:
        raise ValueError(f"{name} must contain at most {max_length} characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} must not contain control characters")
    return value


def _require_sha256(name: str, value: str) -> str:
    token = _require_exact(name, value, max_length=64)
    if len(token) != 64 or any(character not in "0123456789abcdef" for character in token):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return token


def _workspace(value: str) -> str:
    path = Path(_require_exact("workspace", value)).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"workspace must be an existing directory: {path}")
    return str(path)


def _absolute_file_path(name: str, value: str) -> str:
    path = Path(_require_exact(name, value)).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"{name} must be an existing absolute file path")
    return str(path.absolute())


def _relative_path(name: str, value: str, *, allow_dot: bool = False) -> str:
    token = _require_exact(name, value)
    if allow_dot and token == ".":
        return token
    pure = PurePosixPath(token)
    if pure.is_absolute() or token != pure.as_posix() or ".." in pure.parts or "." in pure.parts:
        raise ValueError(f"{name} must be a normalized workspace-relative POSIX path")
    return token


def _identity_values(instance: Any) -> None:
    object.__setattr__(instance, "workspace", _workspace(instance.workspace))
    for name in ("project_id", "run_id", "obligation_id", "owner_task_id"):
        object.__setattr__(instance, name, _require_exact(name, getattr(instance, name), max_length=256))
    object.__setattr__(
        instance,
        "completion_contract_hash",
        _require_sha256("completion_contract_hash", instance.completion_contract_hash),
    )


def _timeout_seconds(value: float) -> float:
    if isinstance(value, bool):
        raise TypeError("timeout_seconds must be a finite float")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0 or timeout > _MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be finite and within (0, {_MAX_TIMEOUT_SECONDS}]")
    return timeout


@dataclass(frozen=True, slots=True)
class ProjectVerificationArtifactInputV1:
    """One contract-owned artifact path whose current bytes are command input."""

    obligation_id: str
    path: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "obligation_id", _require_exact("obligation_id", self.obligation_id, max_length=256))
        object.__setattr__(self, "path", _relative_path("path", self.path))


@dataclass(frozen=True, slots=True)
class ProjectVerificationArtifactSnapshotV1:
    """Content hash observed by the execution owner for one input artifact."""

    obligation_id: str
    path: str
    artifact_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "obligation_id", _require_exact("obligation_id", self.obligation_id, max_length=256))
        object.__setattr__(self, "path", _relative_path("path", self.path))
        object.__setattr__(self, "artifact_hash", _require_sha256("artifact_hash", self.artifact_hash))


@dataclass(frozen=True, slots=True)
class RecordProjectArtifactCommandV1:
    """Record one exact artifact obligation from real workspace bytes."""

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    obligation_id: str
    owner_task_id: str
    path: str

    def __post_init__(self) -> None:
        _identity_values(self)
        object.__setattr__(self, "path", _relative_path("path", self.path))


@dataclass(frozen=True, slots=True)
class QueryProjectArtifactReceiptV1:
    """Query a current artifact receipt by its full contract identity."""

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    obligation_id: str
    owner_task_id: str
    path: str

    def __post_init__(self) -> None:
        _identity_values(self)
        object.__setattr__(self, "path", _relative_path("path", self.path))


@dataclass(frozen=True, slots=True)
class ProjectArtifactReceiptV1:
    """Private-sealed content receipt for one real artifact."""

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    obligation_id: str
    owner_task_id: str
    path: str
    artifact_hash: str
    job_token_id: str
    job_token_set_hash: str
    execution_policy_hash: str
    authority_revision: str
    receipt_hash: str
    receipt_ref: str
    owner_module_id: str = field(init=False, default=_OWNER_MODULE_ID)
    _authority_token: InitVar[object | None] = None

    def __post_init__(self, _authority_token: object | None) -> None:
        from polaris.cells.runtime.execution_broker.internal.project_verification_authority import (
            _is_project_verification_receipt_seal,
        )

        if not _is_project_verification_receipt_seal(_authority_token):
            raise ValueError("project artifact receipt must be owner-sealed by runtime.execution_broker")
        _identity_values(self)
        object.__setattr__(self, "path", _relative_path("path", self.path))
        object.__setattr__(self, "artifact_hash", _require_sha256("artifact_hash", self.artifact_hash))
        object.__setattr__(self, "job_token_id", _require_exact("job_token_id", self.job_token_id, max_length=256))
        object.__setattr__(self, "job_token_set_hash", _require_sha256("job_token_set_hash", self.job_token_set_hash))
        object.__setattr__(self, "execution_policy_hash", _require_sha256("execution_policy_hash", self.execution_policy_hash))
        object.__setattr__(self, "authority_revision", _require_sha256("authority_revision", self.authority_revision))
        object.__setattr__(self, "receipt_hash", _require_sha256("receipt_hash", self.receipt_hash))
        object.__setattr__(self, "receipt_ref", _require_exact("receipt_ref", self.receipt_ref))


@dataclass(frozen=True, slots=True)
class ResolveProjectVerificationAuthorityQueryV1:
    """Identity-only request; caller cannot choose executable command fields."""

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    obligation_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _workspace(self.workspace))
        for name in ("project_id", "run_id", "obligation_id"):
            object.__setattr__(self, name, _require_exact(name, getattr(self, name), max_length=256))
        object.__setattr__(
            self,
            "completion_contract_hash",
            _require_sha256("completion_contract_hash", self.completion_contract_hash),
        )


@dataclass(frozen=True, slots=True)
class ResolveProjectArtifactAuthorityQueryV1:
    """Identity-only artifact authority request."""

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    obligation_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _workspace(self.workspace))
        for name in ("project_id", "run_id", "obligation_id"):
            object.__setattr__(self, name, _require_exact(name, getattr(self, name), max_length=256))
        object.__setattr__(self, "completion_contract_hash", _require_sha256("completion_contract_hash", self.completion_contract_hash))


@dataclass(frozen=True, slots=True)
class ProjectArtifactExecutionAuthorityV1:
    """Exact artifact obligation plus current control-plane capability."""

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    obligation_id: str
    owner_task_id: str
    path: str
    job_token_id: str
    job_token_set_hash: str
    execution_policy_hash: str
    authority_revision: str

    def __post_init__(self) -> None:
        _identity_values(self)
        object.__setattr__(self, "path", _relative_path("path", self.path))
        object.__setattr__(self, "job_token_id", _require_exact("job_token_id", self.job_token_id, max_length=256))
        object.__setattr__(self, "job_token_set_hash", _require_sha256("job_token_set_hash", self.job_token_set_hash))
        object.__setattr__(self, "execution_policy_hash", _require_sha256("execution_policy_hash", self.execution_policy_hash))
        object.__setattr__(self, "authority_revision", _require_sha256("authority_revision", self.authority_revision))


@dataclass(frozen=True, slots=True)
class ProjectVerificationExecutionAuthorityV1:
    """Exact CE command plus committed JobToken/policy authority from bootstrap owner."""

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    obligation_id: str
    owner_task_id: str
    modality: ProjectVerificationModalityV1
    argv: tuple[str, ...]
    cwd: str
    command_authority_hash: str
    input_artifacts: tuple[ProjectVerificationArtifactInputV1, ...]
    timeout_seconds: float
    job_token_id: str
    job_token_set_hash: str
    execution_policy_hash: str
    authority_revision: str
    policy_profile_id: str
    policy_decision_hash: str
    executable_path: str
    executable_realpath: str
    executable_hash: str

    def __post_init__(self) -> None:
        _identity_values(self)
        if self.modality not in _MODALITIES:
            raise ValueError(f"modality must be one of {sorted(_MODALITIES)}")
        if type(self.argv) is not tuple or not self.argv:
            raise TypeError("argv must be an exact non-empty tuple")
        object.__setattr__(
            self,
            "argv",
            tuple(_require_exact(f"argv[{index}]", value) for index, value in enumerate(self.argv)),
        )
        object.__setattr__(self, "cwd", _relative_path("cwd", self.cwd, allow_dot=True))
        object.__setattr__(
            self,
            "command_authority_hash",
            _require_sha256("command_authority_hash", self.command_authority_hash),
        )
        if type(self.input_artifacts) is not tuple or not self.input_artifacts:
            raise TypeError("input_artifacts must be an exact non-empty tuple")
        if any(type(item) is not ProjectVerificationArtifactInputV1 for item in self.input_artifacts):
            raise TypeError("input_artifacts must contain exact ProjectVerificationArtifactInputV1 values")
        ordered = tuple(sorted(self.input_artifacts, key=lambda item: (item.obligation_id, item.path)))
        if len({item.obligation_id for item in ordered}) != len(ordered):
            raise ValueError("input_artifacts must contain unique obligation_id values")
        object.__setattr__(self, "input_artifacts", ordered)
        object.__setattr__(self, "timeout_seconds", _timeout_seconds(self.timeout_seconds))
        object.__setattr__(self, "job_token_id", _require_exact("job_token_id", self.job_token_id, max_length=256))
        object.__setattr__(self, "job_token_set_hash", _require_sha256("job_token_set_hash", self.job_token_set_hash))
        object.__setattr__(
            self,
            "execution_policy_hash",
            _require_sha256("execution_policy_hash", self.execution_policy_hash),
        )
        object.__setattr__(self, "authority_revision", _require_sha256("authority_revision", self.authority_revision))
        object.__setattr__(self, "policy_profile_id", _require_exact("policy_profile_id", self.policy_profile_id, max_length=256))
        object.__setattr__(self, "policy_decision_hash", _require_sha256("policy_decision_hash", self.policy_decision_hash))
        object.__setattr__(self, "executable_path", _absolute_file_path("executable_path", self.executable_path))
        object.__setattr__(self, "executable_realpath", _absolute_file_path("executable_realpath", self.executable_realpath))
        object.__setattr__(self, "executable_hash", _require_sha256("executable_hash", self.executable_hash))


@dataclass(frozen=True, slots=True)
class ConsumeProjectVerificationCapabilityCommandV1:
    """Private broker request to atomically fence one physical attempt."""

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    obligation_id: str
    owner_task_id: str
    modality: ProjectVerificationModalityV1
    argv: tuple[str, ...]
    cwd: str
    command_authority_hash: str
    input_artifacts: tuple[ProjectVerificationArtifactInputV1, ...]
    timeout_seconds: float
    job_token_id: str
    job_token_set_hash: str
    execution_policy_hash: str
    authority_revision: str
    policy_profile_id: str
    policy_decision_hash: str
    executable_path: str
    executable_realpath: str
    executable_hash: str
    effect_key: str
    attempt_id: str
    _authority_token: InitVar[object | None] = None

    def __post_init__(self, _authority_token: object | None) -> None:
        from polaris.cells.runtime.execution_broker.internal.project_verification_authority import (
            _is_project_verification_capability_command_seal,
        )

        if not _is_project_verification_capability_command_seal(_authority_token):
            raise ValueError("capability consume command must be owner-sealed by runtime.execution_broker")
        authority = ProjectVerificationExecutionAuthorityV1(
            **{name: getattr(self, name) for name in ProjectVerificationExecutionAuthorityV1.__dataclass_fields__}
        )
        for name in ProjectVerificationExecutionAuthorityV1.__dataclass_fields__:
            object.__setattr__(self, name, getattr(authority, name))
        object.__setattr__(self, "effect_key", _require_exact("effect_key", self.effect_key, max_length=256))
        object.__setattr__(self, "attempt_id", _require_sha256("attempt_id", self.attempt_id))


@dataclass(frozen=True, slots=True)
class ProjectVerificationCapabilityConsumptionV1:
    """One-use capability transfer from bootstrap owner to execution broker."""

    capability_id: str
    effect_key: str
    attempt_id: str
    authority_revision: str
    job_token_id: str
    job_token_set_hash: str
    execution_policy_hash: str
    policy_profile_id: str
    policy_decision_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability_id", _require_sha256("capability_id", self.capability_id))
        object.__setattr__(self, "effect_key", _require_exact("effect_key", self.effect_key, max_length=256))
        object.__setattr__(self, "attempt_id", _require_sha256("attempt_id", self.attempt_id))
        object.__setattr__(self, "authority_revision", _require_sha256("authority_revision", self.authority_revision))
        object.__setattr__(self, "job_token_id", _require_exact("job_token_id", self.job_token_id, max_length=256))
        object.__setattr__(self, "job_token_set_hash", _require_sha256("job_token_set_hash", self.job_token_set_hash))
        object.__setattr__(self, "execution_policy_hash", _require_sha256("execution_policy_hash", self.execution_policy_hash))
        object.__setattr__(self, "policy_profile_id", _require_exact("policy_profile_id", self.policy_profile_id, max_length=256))
        object.__setattr__(self, "policy_decision_hash", _require_sha256("policy_decision_hash", self.policy_decision_hash))


@runtime_checkable
class ProjectVerificationExecutionAuthorityPortV1(Protocol):
    """Bootstrap owner resolves identity to exact CE + JobToken authority."""

    def resolve_project_verification_authority(
        self,
        query: ResolveProjectVerificationAuthorityQueryV1,
    ) -> ProjectVerificationExecutionAuthorityV1:
        """Return exact current authority or fail closed."""
        ...

    def resolve_project_artifact_authority(
        self,
        query: ResolveProjectArtifactAuthorityQueryV1,
    ) -> ProjectArtifactExecutionAuthorityV1:
        """Return exact current artifact authority or fail closed."""
        ...

    def consume_project_verification_execution_capability(
        self,
        command: ConsumeProjectVerificationCapabilityCommandV1,
    ) -> ProjectVerificationCapabilityConsumptionV1:
        """Atomically consume one current attempt capability."""
        ...


@dataclass(frozen=True, slots=True)
class RunProjectVerificationCommandV1:
    """Execute one exact, PM-authorized verifier command.

    No caller evidence, pass/fail flag, or completion verdict is accepted.
    """

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    obligation_id: str
    owner_task_id: str
    modality: ProjectVerificationModalityV1
    argv: tuple[str, ...]
    cwd: str
    command_authority_hash: str
    input_artifacts: tuple[ProjectVerificationArtifactInputV1, ...]
    timeout_seconds: float = 300.0
    job_token_id: str = ""
    job_token_set_hash: str = ""
    execution_policy_hash: str = ""
    authority_revision: str = ""
    policy_profile_id: str = ""
    policy_decision_hash: str = ""
    executable_path: str = ""
    executable_realpath: str = ""
    executable_hash: str = ""
    _authority_token: InitVar[object | None] = None

    def __post_init__(self, _authority_token: object | None) -> None:
        from polaris.cells.runtime.execution_broker.internal.project_verification_authority import (
            _is_project_verification_command_seal,
        )

        if not _is_project_verification_command_seal(_authority_token):
            raise ValueError("project verification command must be owner-authorized by runtime.execution_broker")
        _identity_values(self)
        if self.modality not in _MODALITIES:
            raise ValueError(f"modality must be one of {sorted(_MODALITIES)}")
        if type(self.argv) is not tuple or not self.argv:
            raise TypeError("argv must be an exact non-empty tuple")
        object.__setattr__(
            self,
            "argv",
            tuple(_require_exact(f"argv[{index}]", value) for index, value in enumerate(self.argv)),
        )
        object.__setattr__(self, "authority_revision", _require_sha256("authority_revision", self.authority_revision))
        object.__setattr__(self, "policy_profile_id", _require_exact("policy_profile_id", self.policy_profile_id, max_length=256))
        object.__setattr__(self, "policy_decision_hash", _require_sha256("policy_decision_hash", self.policy_decision_hash))
        object.__setattr__(self, "executable_path", _absolute_file_path("executable_path", self.executable_path))
        object.__setattr__(self, "executable_realpath", _absolute_file_path("executable_realpath", self.executable_realpath))
        object.__setattr__(self, "executable_hash", _require_sha256("executable_hash", self.executable_hash))
        object.__setattr__(self, "cwd", _relative_path("cwd", self.cwd, allow_dot=True))
        object.__setattr__(
            self,
            "command_authority_hash",
            _require_sha256("command_authority_hash", self.command_authority_hash),
        )
        if type(self.input_artifacts) is not tuple or not self.input_artifacts:
            raise TypeError("input_artifacts must be an exact non-empty tuple")
        if any(type(item) is not ProjectVerificationArtifactInputV1 for item in self.input_artifacts):
            raise TypeError("input_artifacts must contain exact ProjectVerificationArtifactInputV1 values")
        ordered = tuple(sorted(self.input_artifacts, key=lambda item: (item.obligation_id, item.path)))
        if len({item.obligation_id for item in ordered}) != len(ordered):
            raise ValueError("input_artifacts must contain unique obligation_id values")
        object.__setattr__(self, "input_artifacts", ordered)
        object.__setattr__(self, "timeout_seconds", _timeout_seconds(self.timeout_seconds))
        object.__setattr__(self, "job_token_id", _require_exact("job_token_id", self.job_token_id, max_length=256))
        object.__setattr__(self, "job_token_set_hash", _require_sha256("job_token_set_hash", self.job_token_set_hash))
        object.__setattr__(
            self,
            "execution_policy_hash",
            _require_sha256("execution_policy_hash", self.execution_policy_hash),
        )


@dataclass(frozen=True, slots=True)
class QueryProjectVerificationReceiptV1:
    """Query a current physical command receipt by exact command identity."""

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    obligation_id: str
    owner_task_id: str
    modality: ProjectVerificationModalityV1
    argv: tuple[str, ...]
    cwd: str
    command_authority_hash: str
    input_artifacts: tuple[ProjectVerificationArtifactInputV1, ...]
    timeout_seconds: float = 300.0
    job_token_id: str = ""
    job_token_set_hash: str = ""
    execution_policy_hash: str = ""
    authority_revision: str = ""
    policy_profile_id: str = ""
    policy_decision_hash: str = ""
    executable_path: str = ""
    executable_realpath: str = ""
    executable_hash: str = ""

    def __post_init__(self) -> None:
        authority = ProjectVerificationExecutionAuthorityV1(
            workspace=self.workspace,
            project_id=self.project_id,
            run_id=self.run_id,
            completion_contract_hash=self.completion_contract_hash,
            obligation_id=self.obligation_id,
            owner_task_id=self.owner_task_id,
            modality=self.modality,
            argv=self.argv,
            cwd=self.cwd,
            command_authority_hash=self.command_authority_hash,
            input_artifacts=self.input_artifacts,
            timeout_seconds=self.timeout_seconds,
            job_token_id=self.job_token_id,
            job_token_set_hash=self.job_token_set_hash,
            execution_policy_hash=self.execution_policy_hash,
            authority_revision=self.authority_revision,
            policy_profile_id=self.policy_profile_id,
            policy_decision_hash=self.policy_decision_hash,
            executable_path=self.executable_path,
            executable_realpath=self.executable_realpath,
            executable_hash=self.executable_hash,
        )
        for name in (
            "workspace",
            "project_id",
            "run_id",
            "completion_contract_hash",
            "obligation_id",
            "owner_task_id",
            "modality",
            "argv",
            "cwd",
            "command_authority_hash",
            "input_artifacts",
            "timeout_seconds",
            "job_token_id",
            "job_token_set_hash",
            "execution_policy_hash",
            "authority_revision",
            "policy_profile_id",
            "policy_decision_hash",
            "executable_path",
            "executable_realpath",
            "executable_hash",
        ):
            object.__setattr__(self, name, getattr(authority, name))


@dataclass(frozen=True, slots=True)
class ProjectVerificationProcessResultV1:
    """Raw physical process outcome returned only by the broker runner port."""

    exit_code: int | None
    timed_out: bool
    output_bytes: bytes
    process_pid: int | None = None
    process_start_token: str | None = None
    readiness_probe_kind: str = "none"
    readiness_satisfied: bool = False
    controlled_termination: bool = False

    def __post_init__(self) -> None:
        if self.exit_code is not None and (isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)):
            raise TypeError("exit_code must be an int or None")
        if type(self.timed_out) is not bool:
            raise TypeError("timed_out must be a bool")
        if not isinstance(self.output_bytes, bytes):
            raise TypeError("output_bytes must be bytes")
        if self.exit_code is None and not self.timed_out and not self.controlled_termination:
            raise ValueError("a non-timeout process result requires an exact exit_code")
        if self.process_pid is not None and (isinstance(self.process_pid, bool) or self.process_pid <= 0):
            raise ValueError("process_pid must be a positive int or None")
        if self.process_pid is not None and not self.process_start_token:
            raise ValueError("process_pid requires process_start_token")
        object.__setattr__(self, "readiness_probe_kind", _require_exact("readiness_probe_kind", self.readiness_probe_kind, max_length=128))
        if type(self.readiness_satisfied) is not bool or type(self.controlled_termination) is not bool:
            raise TypeError("readiness_satisfied and controlled_termination must be bool")
        if self.controlled_termination and not self.readiness_satisfied:
            raise ValueError("controlled termination requires satisfied readiness")


@dataclass(frozen=True, slots=True)
class ProjectVerificationReceiptV1:
    """Private-sealed receipt for one physical verifier execution."""

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    obligation_id: str
    owner_task_id: str
    modality: ProjectVerificationModalityV1
    argv: tuple[str, ...]
    cwd: str
    command_authority_hash: str
    job_token_id: str
    job_token_set_hash: str
    execution_policy_hash: str
    authority_revision: str
    policy_profile_id: str
    policy_decision_hash: str
    executable_path: str
    executable_realpath: str
    executable_hash: str
    capability_id: str
    attempt_id: str
    input_artifacts: tuple[ProjectVerificationArtifactSnapshotV1, ...]
    input_artifact_hash: str
    timeout_seconds: float
    exit_code: int | None
    timed_out: bool
    output_hash: str
    proof_satisfied: bool
    proof_evidence_hash: str
    process_pid: int | None
    process_start_token: str | None
    readiness_probe_kind: str
    readiness_satisfied: bool
    controlled_termination: bool
    receipt_hash: str
    receipt_ref: str
    owner_module_id: str = field(init=False, default=_OWNER_MODULE_ID)
    _authority_token: InitVar[object | None] = None

    def __post_init__(self, _authority_token: object | None) -> None:
        from polaris.cells.runtime.execution_broker.internal.project_verification_authority import (
            _is_project_verification_receipt_seal,
        )

        if not _is_project_verification_receipt_seal(_authority_token):
            raise ValueError("project verification receipt must be owner-sealed by runtime.execution_broker")
        _identity_values(self)
        if self.modality not in _MODALITIES:
            raise ValueError(f"modality must be one of {sorted(_MODALITIES)}")
        if type(self.argv) is not tuple or not self.argv:
            raise TypeError("argv must be an exact non-empty tuple")
        object.__setattr__(
            self,
            "argv",
            tuple(_require_exact(f"argv[{index}]", value) for index, value in enumerate(self.argv)),
        )
        object.__setattr__(self, "authority_revision", _require_sha256("authority_revision", self.authority_revision))
        object.__setattr__(self, "policy_profile_id", _require_exact("policy_profile_id", self.policy_profile_id, max_length=256))
        object.__setattr__(self, "policy_decision_hash", _require_sha256("policy_decision_hash", self.policy_decision_hash))
        object.__setattr__(self, "executable_path", _absolute_file_path("executable_path", self.executable_path))
        object.__setattr__(self, "executable_realpath", _absolute_file_path("executable_realpath", self.executable_realpath))
        object.__setattr__(self, "executable_hash", _require_sha256("executable_hash", self.executable_hash))
        object.__setattr__(self, "capability_id", _require_sha256("capability_id", self.capability_id))
        object.__setattr__(self, "attempt_id", _require_sha256("attempt_id", self.attempt_id))
        object.__setattr__(self, "cwd", _relative_path("cwd", self.cwd, allow_dot=True))
        object.__setattr__(
            self,
            "command_authority_hash",
            _require_sha256("command_authority_hash", self.command_authority_hash),
        )
        object.__setattr__(self, "job_token_id", _require_exact("job_token_id", self.job_token_id, max_length=256))
        object.__setattr__(self, "job_token_set_hash", _require_sha256("job_token_set_hash", self.job_token_set_hash))
        object.__setattr__(
            self,
            "execution_policy_hash",
            _require_sha256("execution_policy_hash", self.execution_policy_hash),
        )
        if (
            type(self.input_artifacts) is not tuple
            or not self.input_artifacts
            or any(type(item) is not ProjectVerificationArtifactSnapshotV1 for item in self.input_artifacts)
        ):
            raise TypeError("input_artifacts must contain exact ProjectVerificationArtifactSnapshotV1 values")
        object.__setattr__(
            self,
            "input_artifacts",
            tuple(sorted(self.input_artifacts, key=lambda item: (item.obligation_id, item.path))),
        )
        object.__setattr__(
            self,
            "input_artifact_hash",
            _require_sha256("input_artifact_hash", self.input_artifact_hash),
        )
        object.__setattr__(self, "timeout_seconds", _timeout_seconds(self.timeout_seconds))
        if self.exit_code is not None and (isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)):
            raise TypeError("exit_code must be an int or None")
        if type(self.timed_out) is not bool:
            raise TypeError("timed_out must be a bool")
        if self.exit_code is None and not self.timed_out and not self.controlled_termination:
            raise ValueError("a non-timeout receipt requires an exact exit_code")
        object.__setattr__(self, "output_hash", _require_sha256("output_hash", self.output_hash))
        if type(self.proof_satisfied) is not bool:
            raise TypeError("proof_satisfied must be a bool")
        object.__setattr__(
            self,
            "proof_evidence_hash",
            _require_sha256("proof_evidence_hash", self.proof_evidence_hash),
        )
        if self.process_pid is not None and (isinstance(self.process_pid, bool) or self.process_pid <= 0):
            raise ValueError("process_pid must be a positive int or None")
        if self.process_pid is not None and not self.process_start_token:
            raise ValueError("process_pid requires process_start_token")
        object.__setattr__(self, "readiness_probe_kind", _require_exact("readiness_probe_kind", self.readiness_probe_kind, max_length=128))
        if type(self.readiness_satisfied) is not bool or type(self.controlled_termination) is not bool:
            raise TypeError("readiness_satisfied and controlled_termination must be bool")
        if self.controlled_termination and not self.readiness_satisfied:
            raise ValueError("controlled termination requires satisfied readiness")
        object.__setattr__(self, "receipt_hash", _require_sha256("receipt_hash", self.receipt_hash))
        object.__setattr__(self, "receipt_ref", _require_exact("receipt_ref", self.receipt_ref))

    @property
    def succeeded(self) -> bool:
        """Derive command success from physical facts; never caller supplied."""

        normal_exit = not self.timed_out and self.exit_code == 0
        ready_entrypoint = (
            self.modality == "entrypoint"
            and self.readiness_satisfied
            and self.controlled_termination
            and not self.timed_out
        )
        return self.proof_satisfied and (normal_exit or ready_entrypoint)


@dataclass(frozen=True, slots=True)
class ProjectVerificationExecutionResultV1:
    """Effect result only; deliberately not a project-completion verdict."""

    code: str
    spawned: bool
    receipt: ProjectVerificationReceiptV1 | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _require_exact("code", self.code, max_length=256))
        if type(self.spawned) is not bool:
            raise TypeError("spawned must be a bool")
        if self.receipt is not None and type(self.receipt) is not ProjectVerificationReceiptV1:
            raise TypeError("receipt must be an exact ProjectVerificationReceiptV1 or None")


def record_project_artifact(command: RecordProjectArtifactCommandV1) -> ProjectArtifactReceiptV1:
    """Record one current artifact from physical bytes."""

    from polaris.cells.runtime.execution_broker.internal.project_verification_authority import (
        record_project_artifact as _record,
    )

    return _record(command)


def query_project_artifact_receipt(query: QueryProjectArtifactReceiptV1) -> ProjectArtifactReceiptV1 | None:
    """Return the exact artifact receipt only while its bytes remain current."""

    from polaris.cells.runtime.execution_broker.internal.project_verification_authority import (
        query_project_artifact_receipt as _query,
    )

    return _query(query)


def authorize_project_verification_command(
    query: ResolveProjectVerificationAuthorityQueryV1,
) -> RunProjectVerificationCommandV1:
    """Resolve identity through bootstrap owner into broker-sealed authority."""

    from polaris.cells.runtime.execution_broker.internal.project_verification_authority import (
        authorize_project_verification_command as _authorize,
    )

    return _authorize(query)


def run_project_verification(
    command: RunProjectVerificationCommandV1,
) -> ProjectVerificationExecutionResultV1:
    """Execute one exact verifier with concurrency-safe idempotency."""

    from polaris.cells.runtime.execution_broker.internal.project_verification_authority import (
        run_project_verification as _run,
    )

    return _run(command)


def query_project_verification_receipt(
    query: QueryProjectVerificationReceiptV1,
) -> ProjectVerificationReceiptV1 | None:
    """Return a receipt only while the exact input-artifact snapshot is current."""

    from polaris.cells.runtime.execution_broker.internal.project_verification_authority import (
        query_project_verification_receipt as _query,
    )

    return _query(query)


__all__ = [
    "ConsumeProjectVerificationCapabilityCommandV1",
    "ProjectArtifactExecutionAuthorityV1",
    "ProjectArtifactReceiptV1",
    "ProjectVerificationArtifactInputV1",
    "ProjectVerificationArtifactSnapshotV1",
    "ProjectVerificationCapabilityConsumptionV1",
    "ProjectVerificationExecutionAuthorityPortV1",
    "ProjectVerificationExecutionAuthorityV1",
    "ProjectVerificationExecutionResultV1",
    "ProjectVerificationProcessResultV1",
    "ProjectVerificationReceiptV1",
    "QueryProjectArtifactReceiptV1",
    "QueryProjectVerificationReceiptV1",
    "RecordProjectArtifactCommandV1",
    "ResolveProjectArtifactAuthorityQueryV1",
    "ResolveProjectVerificationAuthorityQueryV1",
    "RunProjectVerificationCommandV1",
    "authorize_project_verification_command",
    "query_project_artifact_receipt",
    "query_project_verification_receipt",
    "record_project_artifact",
    "run_project_verification",
]
