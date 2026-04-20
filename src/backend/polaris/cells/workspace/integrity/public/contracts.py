"""Public contracts for `workspace.integrity` cell."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping


def _required_text(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _as_dict(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


@dataclass(frozen=True)
class ValidateWorkspaceCommandV1:
    """Validate and normalize workspace path."""

    path: str
    self_upgrade_mode: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _required_text("path", self.path))


@dataclass(frozen=True)
class EnsureDocsReadyCommandV1:
    """Ensure docs root exists, otherwise raise conflict."""

    workspace: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _required_text("workspace", self.workspace))


@dataclass(frozen=True)
class GenerateDocsTemplatesCommandV1:
    """Build docs-init template files."""

    workspace: str
    mode: str
    fields: Mapping[str, str] = field(default_factory=dict)
    qa_commands: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _required_text("workspace", self.workspace))
        object.__setattr__(self, "mode", _required_text("mode", self.mode))
        object.__setattr__(self, "fields", dict(self.fields or {}))
        object.__setattr__(self, "qa_commands", tuple(str(v) for v in self.qa_commands))


@dataclass(frozen=True)
class DocsTemplatesResultV1:
    """Generated docs templates and project profile."""

    workspace: str
    mode: str
    project_profile: Mapping[str, Any]
    files: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _required_text("workspace", self.workspace))
        object.__setattr__(self, "mode", _required_text("mode", self.mode))
        object.__setattr__(self, "project_profile", _as_dict(self.project_profile))
        object.__setattr__(self, "files", dict(self.files or {}))


class WorkspaceIntegrityError(RuntimeError):
    """Structured workspace-integrity contract error."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "workspace_integrity_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        normalized = _required_text("message", message)
        super().__init__(normalized)
        self.code = _required_text("code", code)
        self.details = _as_dict(details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": dict(self.details),
        }


@runtime_checkable
class IWorkspaceIntegrity(Protocol):
    """Public interface for workspace integrity checks and docs bootstrap."""

    def validate_workspace(self, command: ValidateWorkspaceCommandV1) -> str:
        """Validate and normalize workspace path."""

    def ensure_docs_ready(self, command: EnsureDocsReadyCommandV1) -> None:
        """Ensure docs root exists."""

    def generate_docs_templates(
        self,
        command: GenerateDocsTemplatesCommandV1,
    ) -> DocsTemplatesResultV1:
        """Generate docs template files."""


__all__ = [
    "DocsTemplatesResultV1",
    "EnsureDocsReadyCommandV1",
    "GenerateDocsTemplatesCommandV1",
    "IWorkspaceIntegrity",
    "ValidateWorkspaceCommandV1",
    "WorkspaceIntegrityError",
]
