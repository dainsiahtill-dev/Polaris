"""Tests for polaris.cells.archive.task_snapshot_archive.public.contracts."""

from __future__ import annotations

from polaris.cells.archive.task_snapshot_archive.public.contracts import (
    ArchiveManifestV1,
    ArchiveTaskSnapshotCommandV1,
    GetTaskSnapshotManifestQueryV1,
    TaskSnapshotArchivedEventV1,
    TaskSnapshotArchiveError,
)


class TestArchiveTaskSnapshotCommandV1:
    def test_fields(self) -> None:
        cmd = ArchiveTaskSnapshotCommandV1(task_id="t1", source_path="/tmp", requested_by="user")
        assert cmd.task_id == "t1"
        assert cmd.source_path == "/tmp"
        assert cmd.requested_by == "user"


class TestGetTaskSnapshotManifestQueryV1:
    def test_fields(self) -> None:
        q = GetTaskSnapshotManifestQueryV1(archive_id="a1")
        assert q.archive_id == "a1"


class TestArchiveManifestV1:
    def test_fields(self) -> None:
        m = ArchiveManifestV1(archive_id="a1", location="s3://bucket", status="ok")
        assert m.archive_id == "a1"
        assert m.location == "s3://bucket"
        assert m.status == "ok"


class TestTaskSnapshotArchivedEventV1:
    def test_fields(self) -> None:
        ev = TaskSnapshotArchivedEventV1(task_id="t1", archive_id="a1")
        assert ev.task_id == "t1"
        assert ev.archive_id == "a1"


class TestTaskSnapshotArchiveError:
    def test_is_exception(self) -> None:
        assert issubclass(TaskSnapshotArchiveError, Exception)
