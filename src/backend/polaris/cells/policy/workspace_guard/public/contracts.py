from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceWriteGuardQueryV1:
    path: str
    operation: str


@dataclass(frozen=True)
class WorkspaceArchiveWriteGuardQueryV1:
    path: str
    operation: str


@dataclass(frozen=True)
class WorkspaceGuardDecisionV1:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class WorkspaceGuardViolationEventV1:
    path: str
    operation: str
    reason: str


class WorkspaceGuardError(Exception):
    """Raised when a workspace guard check cannot be completed."""


__all__ = [
    "WorkspaceArchiveWriteGuardQueryV1",
    "WorkspaceGuardDecisionV1",
    "WorkspaceGuardError",
    "WorkspaceGuardViolationEventV1",
    "WorkspaceWriteGuardQueryV1",
]
