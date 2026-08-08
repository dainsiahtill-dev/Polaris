"""Public contracts for platform verifier policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

OPTIONAL_VERIFIER_MODALITIES = (
    "browser",
    "visual",
    "llm_judge",
    "custom_script",
)
CORE_EVIDENCE_MODALITIES = (
    "qa",
    "code",
    "command",
    "tool_receipt",
    "repair",
    "environment_prep",
    "verifier",
    "domain",
    "api_contract",
    "integration",
    "performance",
    "security",
    "device",
    "plugin_compat",
    "accessibility",
)
SUPPORTED_EVIDENCE_MODALITIES = tuple(dict.fromkeys((*CORE_EVIDENCE_MODALITIES, *OPTIONAL_VERIFIER_MODALITIES)))
VERIFIER_COMMAND_MODALITIES = (
    "environment_prep",
    "build",
    "test",
    "lint",
    "entrypoint",
)


def _exact_token(name: str, value: object, *, max_bytes: int = 512) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value or value != value.strip() or any(ord(character) < 32 for character in value):
        raise ValueError(f"{name} must be an exact non-empty string without control characters")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{name} exceeds {max_bytes} UTF-8 bytes")
    return value


def _sha256(name: str, value: object) -> str:
    token = _exact_token(name, value, max_bytes=64).lower()
    if len(token) != 64 or any(character not in "0123456789abcdef" for character in token):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return token


def _relative_cwd(value: object) -> str:
    token = _exact_token("cwd", value, max_bytes=1024).replace("\\", "/")
    if token == ".":
        return token
    if token.startswith("/") or token == ".." or token.startswith("../") or "/../" in token:
        raise ValueError("cwd must be workspace-relative")
    return token.lstrip("./")


def _clean_workspace(value: str) -> str:
    workspace = str(value or "").strip()
    if not workspace:
        raise ValueError("workspace must be a non-empty string")
    return workspace


@dataclass(frozen=True)
class ReadVerifierPolicyQueryV1:
    """Read the platform verifier policy for one workspace."""

    workspace: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _clean_workspace(self.workspace))


@dataclass(frozen=True)
class UpdateVerifierPolicyCommandV1:
    """Update optional verifier policy for one workspace.

    The policy only controls whether optional verifier modalities are enabled or
    required. It does not execute browser automation, multimodal judgement, or
    user scripts.
    """

    workspace: str
    browser_enabled: bool | None = None
    visual_enabled: bool | None = None
    llm_judge_enabled: bool | None = None
    custom_script_enabled: bool | None = None
    required_modalities: tuple[str, ...] = field(default_factory=tuple)
    custom_scripts: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _clean_workspace(self.workspace))
        object.__setattr__(
            self,
            "required_modalities",
            tuple(str(item or "").strip() for item in self.required_modalities if str(item or "").strip()),
        )
        object.__setattr__(self, "custom_scripts", tuple(dict(item) for item in self.custom_scripts))


@dataclass(frozen=True)
class CompileEvidencePolicyCommandV1:
    """Compile required/advisory QA evidence from task and project context."""

    workspace: str
    task_id: str = ""
    run_id: str = ""
    project_type: str = ""
    language: str = ""
    target_files: tuple[str, ...] = field(default_factory=tuple)
    acceptance_criteria: tuple[str, ...] = field(default_factory=tuple)
    explicit_required_modalities: tuple[str, ...] = field(default_factory=tuple)
    explicit_advisory_modalities: tuple[str, ...] = field(default_factory=tuple)
    risk_level: str = "medium"

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _clean_workspace(self.workspace))
        object.__setattr__(self, "task_id", str(self.task_id or "").strip())
        object.__setattr__(self, "run_id", str(self.run_id or "").strip())
        object.__setattr__(self, "project_type", str(self.project_type or "").strip().lower())
        object.__setattr__(self, "language", str(self.language or "").strip().lower())
        object.__setattr__(
            self,
            "target_files",
            tuple(str(item or "").strip().replace("\\", "/") for item in self.target_files if str(item or "").strip()),
        )
        object.__setattr__(
            self,
            "acceptance_criteria",
            tuple(str(item or "").strip() for item in self.acceptance_criteria if str(item or "").strip()),
        )
        object.__setattr__(
            self,
            "explicit_required_modalities",
            tuple(
                str(item or "").strip().lower() for item in self.explicit_required_modalities if str(item or "").strip()
            ),
        )
        object.__setattr__(
            self,
            "explicit_advisory_modalities",
            tuple(
                str(item or "").strip().lower() for item in self.explicit_advisory_modalities if str(item or "").strip()
            ),
        )
        object.__setattr__(self, "risk_level", str(self.risk_level or "medium").strip().lower() or "medium")


VerifierCommandModalityV1 = Literal["environment_prep", "build", "test", "lint", "entrypoint"]


@dataclass(frozen=True)
class EvaluateVerifierCommandPolicyQueryV1:
    """Evaluate one committed verifier command against platform-owned profiles.

    This query is evidence, not a spawn capability. The execution owner must
    resolve it again while atomically consuming its own fenced launch lease.
    """

    workspace: str
    project_id: str
    run_id: str
    task_id: str
    completion_contract_hash: str
    verifier_obligation_id: str
    command_authority_hash: str
    modality: VerifierCommandModalityV1
    argv: tuple[str, ...]
    cwd: str
    input_obligation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _clean_workspace(self.workspace))
        for name in ("project_id", "run_id", "task_id", "verifier_obligation_id"):
            object.__setattr__(self, name, _exact_token(name, getattr(self, name)))
        object.__setattr__(
            self,
            "completion_contract_hash",
            _sha256("completion_contract_hash", self.completion_contract_hash),
        )
        object.__setattr__(
            self,
            "command_authority_hash",
            _sha256("command_authority_hash", self.command_authority_hash),
        )
        modality = _exact_token("modality", self.modality, max_bytes=64)
        if modality not in VERIFIER_COMMAND_MODALITIES:
            raise ValueError(f"unsupported verifier modality: {modality}")
        object.__setattr__(self, "modality", modality)
        if not isinstance(self.argv, (list, tuple)) or not self.argv:
            raise ValueError("argv must be a non-empty list or tuple")
        argv = tuple(_exact_token(f"argv[{index}]", item, max_bytes=2048) for index, item in enumerate(self.argv))
        if len(argv) > 128:
            raise ValueError("argv must contain at most 128 items")
        object.__setattr__(self, "argv", argv)
        object.__setattr__(self, "cwd", _relative_cwd(self.cwd))
        if not isinstance(self.input_obligation_ids, (list, tuple)) or not self.input_obligation_ids:
            raise ValueError("input_obligation_ids must be a non-empty list or tuple")
        input_ids = tuple(sorted({_exact_token("input_obligation_id", item) for item in self.input_obligation_ids}))
        object.__setattr__(self, "input_obligation_ids", input_ids)


@dataclass(frozen=True)
class VerifierCommandPolicyDecisionV1:
    """Owner decision for one exact command proposal; never a capability."""

    authorized: bool
    error_code: str
    detail: str
    profile_id: str
    normalized_argv: tuple[str, ...]
    normalized_cwd: str
    input_obligation_ids: tuple[str, ...]
    executable_path: str
    executable_realpath: str
    executable_hash: str
    policy_decision_hash: str


@dataclass(frozen=True)
class VerifierPolicyResultV1:
    """Platform verifier policy read model."""

    policy: dict[str, Any]


@dataclass(frozen=True)
class EvidencePolicyResultV1:
    """Compiled evidence policy read model."""

    policy: dict[str, Any]


class ControlPlaneVerifierPolicyV1Error(Exception):
    """Raised when verifier policy cannot be read or updated."""


__all__ = [
    "CORE_EVIDENCE_MODALITIES",
    "OPTIONAL_VERIFIER_MODALITIES",
    "SUPPORTED_EVIDENCE_MODALITIES",
    "VERIFIER_COMMAND_MODALITIES",
    "CompileEvidencePolicyCommandV1",
    "ControlPlaneVerifierPolicyV1Error",
    "EvaluateVerifierCommandPolicyQueryV1",
    "EvidencePolicyResultV1",
    "ReadVerifierPolicyQueryV1",
    "UpdateVerifierPolicyCommandV1",
    "VerifierCommandModalityV1",
    "VerifierCommandPolicyDecisionV1",
    "VerifierPolicyResultV1",
]
