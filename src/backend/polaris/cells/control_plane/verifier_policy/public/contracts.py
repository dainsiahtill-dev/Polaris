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
class VerifierPolicyResultV1:
    """Platform verifier policy read model."""

    policy: dict[str, Any]


class ControlPlaneVerifierPolicyV1Error(Exception):
    """Raised when verifier policy cannot be read or updated."""


__all__ = [
    "OPTIONAL_VERIFIER_MODALITIES",
    "ControlPlaneVerifierPolicyV1Error",
    "ReadVerifierPolicyQueryV1",
    "UpdateVerifierPolicyCommandV1",
    "VerifierPolicyResultV1",
]
