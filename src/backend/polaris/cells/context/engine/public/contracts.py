from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BuildRoleContextCommandV1:
    role_id: str
    objective: str


@dataclass(frozen=True)
class ResolveRoleContextQueryV1:
    role_id: str
    limit: int = 8


@dataclass(frozen=True)
class RoleContextResultV1:
    context_items: tuple[str, ...]
    source_cells: tuple[str, ...]


@dataclass(frozen=True)
class ContextResolvedEventV1:
    role_id: str
    source_cells: tuple[str, ...]


class ContextEngineError(Exception):
    """Raised when graph-constrained context assembly fails."""


__all__ = [
    "BuildRoleContextCommandV1",
    "ContextEngineError",
    "ContextResolvedEventV1",
    "ResolveRoleContextQueryV1",
    "RoleContextResultV1",
]
