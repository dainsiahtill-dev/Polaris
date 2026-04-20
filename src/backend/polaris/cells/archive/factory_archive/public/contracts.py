from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArchiveFactoryRunCommandV1:
    run_id: str
    source_path: str
    requested_by: str


@dataclass(frozen=True)
class GetFactoryArchiveManifestQueryV1:
    archive_id: str


@dataclass(frozen=True)
class ArchiveManifestV1:
    archive_id: str
    location: str
    status: str


@dataclass(frozen=True)
class FactoryArchivedEventV1:
    run_id: str
    archive_id: str


class FactoryArchiveError(Exception):
    """Raised when factory archive publication fails."""


__all__ = [
    "ArchiveFactoryRunCommandV1",
    "ArchiveManifestV1",
    "FactoryArchiveError",
    "FactoryArchivedEventV1",
    "GetFactoryArchiveManifestQueryV1",
]
