from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceWriteGuardQueryV1:
    path: str
    operation: str


@dataclass(frozen=True)
class WorkspaceWriteGuardBatchQueryV1:
    paths: tuple[str, ...]
    operation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "paths", tuple(str(path) for path in self.paths))


@dataclass(frozen=True)
class WorkspaceArchiveWriteGuardQueryV1:
    path: str
    operation: str


@dataclass(frozen=True)
class WorkspaceGuardDecisionV1:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class WorkspaceGuardPathDecisionV1:
    path: str
    operation: str
    allowed: bool
    reason: str


@dataclass(frozen=True)
class WorkspaceGuardBatchDecisionV1:
    allowed: bool
    reason: str
    checked_paths: tuple[str, ...] = ()
    denied_path: str = ""
    path_decisions: tuple[WorkspaceGuardPathDecisionV1, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "checked_paths", tuple(str(path) for path in self.checked_paths))


@dataclass(frozen=True)
class WorkspaceGuardViolationEventV1:
    path: str
    operation: str
    reason: str


class WorkspaceGuardError(Exception):
    """Raised when a workspace guard check cannot be completed."""


__all__ = [
    "WorkspaceArchiveWriteGuardQueryV1",
    "WorkspaceGuardBatchDecisionV1",
    "WorkspaceGuardDecisionV1",
    "WorkspaceGuardError",
    "WorkspaceGuardPathDecisionV1",
    "WorkspaceGuardViolationEventV1",
    "WorkspaceWriteGuardBatchQueryV1",
    "WorkspaceWriteGuardQueryV1",
]
