"""Tests for polaris.cells.archive.run_archive.public.contracts."""

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


class TestArchiveRunCommandV1:
    def test_fields(self) -> None:
        cmd = ArchiveRunCommandV1(run_id="r1", source_path="/tmp", requested_by="user")
        assert cmd.run_id == "r1"
        assert cmd.source_path == "/tmp"
        assert cmd.requested_by == "user"


class TestListHistoryRunsQueryV1:
    def test_default_limit(self) -> None:
        q = ListHistoryRunsQueryV1()
        assert q.limit == 20

    def test_custom_limit(self) -> None:
        q = ListHistoryRunsQueryV1(limit=50)
        assert q.limit == 50


class TestGetArchiveManifestQueryV1:
    def test_fields(self) -> None:
        q = GetArchiveManifestQueryV1(archive_id="a1")
        assert q.archive_id == "a1"


class TestArchiveManifestV1:
    def test_fields(self) -> None:
        m = ArchiveManifestV1(archive_id="a1", location="s3://bucket", status="ok")
        assert m.archive_id == "a1"
        assert m.location == "s3://bucket"
        assert m.status == "ok"


class TestHistoryRunsResultV1:
    def test_fields(self) -> None:
        r = HistoryRunsResultV1(runs=("r1", "r2"), total=2)
        assert r.total == 2
        assert r.runs == ("r1", "r2")


class TestRunArchivedEventV1:
    def test_fields(self) -> None:
        ev = RunArchivedEventV1(run_id="r1", archive_id="a1")
        assert ev.run_id == "r1"
        assert ev.archive_id == "a1"


class TestRunArchiveError:
    def test_is_exception(self) -> None:
        assert issubclass(RunArchiveError, Exception)
