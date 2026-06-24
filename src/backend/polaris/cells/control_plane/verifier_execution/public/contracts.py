"""Public contracts for platform verifier execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RunVerifierPolicyCommandV1:
    """Run enabled verifier providers for one workspace/policy snapshot."""

    workspace: str
    policy: dict[str, Any]
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        workspace = str(self.workspace or "").strip()
        if not workspace:
            raise ValueError("workspace must be a non-empty string")
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "policy", dict(self.policy))
        timeout = max(1, min(300, int(self.timeout_seconds or 30)))
        object.__setattr__(self, "timeout_seconds", timeout)


@dataclass(frozen=True)
class VerifierExecutionResultV1:
    """Evidence patch returned by verifier execution."""

    gate_patch: dict[str, Any] = field(default_factory=dict)


class ControlPlaneVerifierExecutionV1Error(Exception):
    """Raised when verifier execution cannot be prepared safely."""


__all__ = [
    "ControlPlaneVerifierExecutionV1Error",
    "RunVerifierPolicyCommandV1",
    "VerifierExecutionResultV1",
]
