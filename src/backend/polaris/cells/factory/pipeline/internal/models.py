"""Internal models for the factory projection lab.

These objects formalize the minimal Cell IR and projection metadata needed to
compile a target project into a traditional code workspace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

_VALID_FIELD_KINDS = {"str", "tags", "bool", "int"}


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _normalize_settings(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


def _normalize_verification_commands(
    commands: tuple[tuple[str, ...], ...] | tuple[list[str], ...] | list[tuple[str, ...]] | list[list[str]] | None,
) -> tuple[tuple[str, ...], ...]:
    normalized: list[tuple[str, ...]] = []
    for command in commands or ():
        parts = tuple(str(part).strip() for part in command if str(part).strip())
        if parts:
            normalized.append(parts)
    return tuple(normalized)


@dataclass(frozen=True)
class FieldSpec:
    """Describe one generated entity field."""

    name: str
    kind: str
    description: str
    required: bool = True
    searchable: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_non_empty("name", self.name))
        object.__setattr__(self, "kind", _require_non_empty("kind", self.kind))
        object.__setattr__(self, "description", _require_non_empty("description", self.description))
        if self.kind not in _VALID_FIELD_KINDS:
            raise ValueError(f"Unsupported field kind: {self.kind}")
        if self.kind == "bool" and self.required:
            object.__setattr__(self, "required", False)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "required": self.required,
            "searchable": self.searchable,
        }


@dataclass(frozen=True)
class CommandSpec:
    """Describe one user-visible capability command."""

    name: str
    description: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_non_empty("name", self.name))
        object.__setattr__(self, "description", _require_non_empty("description", self.description))

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "description": self.description}


@dataclass(frozen=True)
class EntitySpec:
    """Describe the primary entity generated for a target project."""

    singular: str
    plural: str
    class_name: str
    fields: tuple[FieldSpec, ...]
    archive_field: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "singular", _require_non_empty("singular", self.singular))
        object.__setattr__(self, "plural", _require_non_empty("plural", self.plural))
        object.__setattr__(self, "class_name", _require_non_empty("class_name", self.class_name))
        object.__setattr__(self, "fields", tuple(self.fields))
        if not self.fields:
            raise ValueError("fields must not be empty")
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("field names must be unique")
        if self.archive_field and self.archive_field not in names:
            raise ValueError("archive_field must reference an existing field")

    @property
    def record_id_field(self) -> str:
        return f"{self.singular}_id"

    @property
    def searchable_fields(self) -> tuple[FieldSpec, ...]:
        return tuple(field for field in self.fields if field.searchable)

    def to_dict(self) -> dict[str, object]:
        return {
            "singular": self.singular,
            "plural": self.plural,
            "class_name": self.class_name,
            "record_id_field": self.record_id_field,
            "archive_field": self.archive_field,
            "fields": [field.to_dict() for field in self.fields],
        }


@dataclass(frozen=True)
class TargetProjectManifest:
    """Describe a generated target project in Cell IR form."""

    scenario_id: str
    requirement: str
    project_slug: str
    project_title: str
    package_name: str
    summary: str
    entity: EntitySpec
    commands: tuple[CommandSpec, ...]
    project_style: str = "json_cli_app"
    settings: Mapping[str, Any] = field(default_factory=dict)
    verification_commands: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario_id", _require_non_empty("scenario_id", self.scenario_id))
        object.__setattr__(self, "requirement", _require_non_empty("requirement", self.requirement))
        object.__setattr__(self, "project_slug", _require_non_empty("project_slug", self.project_slug))
        object.__setattr__(self, "project_title", _require_non_empty("project_title", self.project_title))
        object.__setattr__(self, "package_name", _require_non_empty("package_name", self.package_name))
        object.__setattr__(self, "summary", _require_non_empty("summary", self.summary))
        object.__setattr__(self, "commands", tuple(self.commands))
        object.__setattr__(self, "project_style", _require_non_empty("project_style", self.project_style))
        object.__setattr__(self, "settings", _normalize_settings(self.settings))
        object.__setattr__(self, "verification_commands", _normalize_verification_commands(self.verification_commands))
        if not self.commands:
            raise ValueError("commands must not be empty")

    @property
    def project_root(self) -> str:
        return f"experiments/{self.project_slug}"

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "requirement": self.requirement,
            "project_slug": self.project_slug,
            "project_title": self.project_title,
            "package_name": self.package_name,
            "summary": self.summary,
            "project_root": self.project_root,
            "project_style": self.project_style,
            "settings": dict(self.settings),
            "verification_commands": [list(command) for command in self.verification_commands],
            "entity": self.entity.to_dict(),
            "commands": [command.to_dict() for command in self.commands],
        }


@dataclass(frozen=True)
class TargetCellSpec:
    """Describe one target-side Cell produced for the experiment."""

    cell_id: str
    purpose: str
    depends_on: tuple[str, ...] = ()
    state_owners: tuple[str, ...] = ()
    effects_allowed: tuple[str, ...] = ()
    projection_targets: tuple[str, ...] = ()
    verification_targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "cell_id", _require_non_empty("cell_id", self.cell_id))
        object.__setattr__(self, "purpose", _require_non_empty("purpose", self.purpose))
        object.__setattr__(
            self, "depends_on", tuple(str(value).strip() for value in self.depends_on if str(value).strip())
        )
        object.__setattr__(
            self, "state_owners", tuple(str(value).strip() for value in self.state_owners if str(value).strip())
        )
        object.__setattr__(
            self, "effects_allowed", tuple(str(value).strip() for value in self.effects_allowed if str(value).strip())
        )
        object.__setattr__(
            self,
            "projection_targets",
            tuple(str(value).strip() for value in self.projection_targets if str(value).strip()),
        )
        object.__setattr__(
            self,
            "verification_targets",
            tuple(str(value).strip() for value in self.verification_targets if str(value).strip()),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "cell_id": self.cell_id,
            "purpose": self.purpose,
            "depends_on": list(self.depends_on),
            "state_owners": list(self.state_owners),
            "effects_allowed": list(self.effects_allowed),
            "projection_targets": list(self.projection_targets),
            "verification_targets": list(self.verification_targets),
        }


@dataclass(frozen=True)
class ProjectionEntry:
    """Map one projected file back to one or more target cells."""

    path: str
    cell_ids: tuple[str, ...]
    description: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _require_non_empty("path", self.path))
        object.__setattr__(self, "cell_ids", tuple(str(value).strip() for value in self.cell_ids if str(value).strip()))
        object.__setattr__(self, "description", _require_non_empty("description", self.description))
        if not self.cell_ids:
            raise ValueError("cell_ids must not be empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "cell_ids": list(self.cell_ids),
            "description": self.description,
        }


__all__ = [
    "CommandSpec",
    "EntitySpec",
    "FieldSpec",
    "ProjectionEntry",
    "TargetCellSpec",
    "TargetProjectManifest",
]
