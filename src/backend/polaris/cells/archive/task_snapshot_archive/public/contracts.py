from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArchiveTaskSnapshotCommandV1:
    task_id: str
    source_path: str
    requested_by: str


@dataclass(frozen=True)
class GetTaskSnapshotManifestQueryV1:
    archive_id: str


@dataclass(frozen=True)
class ArchiveManifestV1:
    archive_id: str
    location: str
    status: str


@dataclass(frozen=True)
class TaskSnapshotArchivedEventV1:
    task_id: str
    archive_id: str


class TaskSnapshotArchiveError(Exception):
    """Raised when task snapshot archive publication fails."""


__all__ = [
    "ArchiveManifestV1",
    "ArchiveTaskSnapshotCommandV1",
    "GetTaskSnapshotManifestQueryV1",
    "TaskSnapshotArchiveError",
    "TaskSnapshotArchivedEventV1",
]
