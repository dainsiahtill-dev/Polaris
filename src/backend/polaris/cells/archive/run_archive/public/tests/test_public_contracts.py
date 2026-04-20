"""Unit tests for `archive/run_archive` public contracts."""

from __future__ import annotations

from polaris.cells.archive.run_archive.public.contracts import (
    ArchiveManifestV1,
    ArchiveRunCommandV1,
    GetArchiveManifestQueryV1,
    HistoryRunsResultV1,
    ListHistoryRunsQueryV1,
    RunArchivedEventV1,
    RunArchiveError,
)


class TestArchiveRunCommandV1HappyPath:
    def test_construction(self) -> None:
        cmd = ArchiveRunCommandV1(
            run_id="run-1",
            source_path="/path/to/run",
            requested_by="director",
        )
        assert cmd.run_id == "run-1"
        assert cmd.source_path == "/path/to/run"
        assert cmd.requested_by == "director"


class TestListHistoryRunsQueryV1HappyPath:
    def test_defaults(self) -> None:
        q = ListHistoryRunsQueryV1()
        assert q.limit == 20

    def test_explicit_limit(self) -> None:
        q = ListHistoryRunsQueryV1(limit=50)
        assert q.limit == 50


class TestGetArchiveManifestQueryV1HappyPath:
    def test_construction(self) -> None:
        q = GetArchiveManifestQueryV1(archive_id="ra-1")
        assert q.archive_id == "ra-1"


class TestArchiveManifestV1HappyPath:
    def test_construction(self) -> None:
        m = ArchiveManifestV1(archive_id="ra-1", location="/ra/ra-1", status="active")
        assert m.archive_id == "ra-1"
        assert m.location == "/ra/ra-1"
        assert m.status == "active"


class TestHistoryRunsResultV1HappyPath:
    def test_construction(self) -> None:
        r = HistoryRunsResultV1(runs=("run-1", "run-2"), total=2)
        assert r.runs == ("run-1", "run-2")
        assert r.total == 2


class TestRunArchivedEventV1HappyPath:
    def test_construction(self) -> None:
        evt = RunArchivedEventV1(run_id="run-1", archive_id="ra-1")
        assert evt.run_id == "run-1"
        assert evt.archive_id == "ra-1"


class TestRunArchiveError:
    def test_raise_and_catch(self) -> None:
        err = RunArchiveError("run archive failed")
        assert str(err) == "run archive failed"
        assert isinstance(err, Exception)
