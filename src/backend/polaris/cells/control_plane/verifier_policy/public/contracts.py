"""Public contracts for platform verifier policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
                str(item or "").strip().lower()
                for item in self.explicit_required_modalities
                if str(item or "").strip()
            ),
        )
        object.__setattr__(
            self,
            "explicit_advisory_modalities",
            tuple(
                str(item or "").strip().lower()
                for item in self.explicit_advisory_modalities
                if str(item or "").strip()
            ),
        )
        object.__setattr__(self, "risk_level", str(self.risk_level or "medium").strip().lower() or "medium")


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
    "OPTIONAL_VERIFIER_MODALITIES",
    "CORE_EVIDENCE_MODALITIES",
    "CompileEvidencePolicyCommandV1",
    "ControlPlaneVerifierPolicyV1Error",
    "EvidencePolicyResultV1",
    "ReadVerifierPolicyQueryV1",
    "SUPPORTED_EVIDENCE_MODALITIES",
    "UpdateVerifierPolicyCommandV1",
    "VerifierPolicyResultV1",
]
