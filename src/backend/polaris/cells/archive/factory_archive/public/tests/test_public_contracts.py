"""Unit tests for `archive/factory_archive` public contracts."""

from __future__ import annotations

from polaris.cells.archive.factory_archive.public.contracts import (
    ArchiveFactoryRunCommandV1,
    ArchiveManifestV1,
    FactoryArchivedEventV1,
    FactoryArchiveError,
    GetFactoryArchiveManifestQueryV1,
)


class TestArchiveFactoryRunCommandV1HappyPath:
    def test_construction(self) -> None:
        cmd = ArchiveFactoryRunCommandV1(
            run_id="run-1",
            source_path="/path/to/run",
            requested_by="director",
        )
        assert cmd.run_id == "run-1"
        assert cmd.source_path == "/path/to/run"
        assert cmd.requested_by == "director"


class TestGetFactoryArchiveManifestQueryV1HappyPath:
    def test_construction(self) -> None:
        q = GetFactoryArchiveManifestQueryV1(archive_id="fa-1")
        assert q.archive_id == "fa-1"


class TestArchiveManifestV1HappyPath:
    def test_construction(self) -> None:
        m = ArchiveManifestV1(archive_id="fa-1", location="/fa/fa-1", status="active")
        assert m.archive_id == "fa-1"
        assert m.location == "/fa/fa-1"
        assert m.status == "active"


class TestFactoryArchivedEventV1HappyPath:
    def test_construction(self) -> None:
        evt = FactoryArchivedEventV1(run_id="run-1", archive_id="fa-1")
        assert evt.run_id == "run-1"
        assert evt.archive_id == "fa-1"


class TestFactoryArchiveError:
    def test_raise_and_catch(self) -> None:
        err = FactoryArchiveError("archive failed")
        assert str(err) == "archive failed"
        assert isinstance(err, Exception)
