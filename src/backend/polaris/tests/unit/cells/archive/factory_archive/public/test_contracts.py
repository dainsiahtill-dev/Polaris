"""Tests for polaris.cells.archive.factory_archive.public.contracts."""

from __future__ import annotations

from polaris.cells.archive.factory_archive.public.contracts import (
    ArchiveFactoryRunCommandV1,
    ArchiveManifestV1,
    FactoryArchivedEventV1,
    FactoryArchiveError,
    GetFactoryArchiveManifestQueryV1,
)


class TestArchiveFactoryRunCommandV1:
    def test_fields(self) -> None:
        cmd = ArchiveFactoryRunCommandV1(run_id="r1", source_path="/tmp", requested_by="user")
        assert cmd.run_id == "r1"
        assert cmd.source_path == "/tmp"
        assert cmd.requested_by == "user"


class TestGetFactoryArchiveManifestQueryV1:
    def test_fields(self) -> None:
        q = GetFactoryArchiveManifestQueryV1(archive_id="a1")
        assert q.archive_id == "a1"


class TestArchiveManifestV1:
    def test_fields(self) -> None:
        m = ArchiveManifestV1(archive_id="a1", location="s3://bucket", status="ok")
        assert m.archive_id == "a1"
        assert m.location == "s3://bucket"
        assert m.status == "ok"


class TestFactoryArchivedEventV1:
    def test_fields(self) -> None:
        ev = FactoryArchivedEventV1(run_id="r1", archive_id="a1")
        assert ev.run_id == "r1"
        assert ev.archive_id == "a1"


class TestFactoryArchiveError:
    def test_is_exception(self) -> None:
        assert issubclass(FactoryArchiveError, Exception)
