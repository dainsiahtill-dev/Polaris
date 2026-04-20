from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArchiveRunCommandV1:
    run_id: str
    source_path: str
    requested_by: str


@dataclass(frozen=True)
class ListHistoryRunsQueryV1:
    limit: int = 20


@dataclass(frozen=True)
class GetArchiveManifestQueryV1:
    archive_id: str


@dataclass(frozen=True)
class ArchiveManifestV1:
    archive_id: str
    location: str
    status: str


@dataclass(frozen=True)
class HistoryRunsResultV1:
    runs: tuple[str, ...]
    total: int


@dataclass(frozen=True)
class RunArchivedEventV1:
    run_id: str
    archive_id: str


class RunArchiveError(Exception):
    """Raised when run archive publication fails."""


__all__ = [
    "ArchiveManifestV1",
    "ArchiveRunCommandV1",
    "GetArchiveManifestQueryV1",
    "HistoryRunsResultV1",
    "ListHistoryRunsQueryV1",
    "RunArchiveError",
    "RunArchivedEventV1",
]
