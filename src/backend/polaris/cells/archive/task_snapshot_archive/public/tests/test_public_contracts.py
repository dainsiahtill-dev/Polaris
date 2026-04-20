"""Unit tests for `archive/task_snapshot_archive` public contracts."""

from __future__ import annotations

from polaris.cells.archive.task_snapshot_archive.public.contracts import (
    ArchiveManifestV1,
    ArchiveTaskSnapshotCommandV1,
    GetTaskSnapshotManifestQueryV1,
    TaskSnapshotArchivedEventV1,
    TaskSnapshotArchiveError,
)


class TestArchiveTaskSnapshotCommandV1HappyPath:
    def test_construction(self) -> None:
        cmd = ArchiveTaskSnapshotCommandV1(
            task_id="task-1",
            source_path="/path/to/snapshot",
            requested_by="director",
        )
        assert cmd.task_id == "task-1"
        assert cmd.source_path == "/path/to/snapshot"
        assert cmd.requested_by == "director"


class TestArchiveTaskSnapshotCommandV1EdgeCases:
    def test_empty_strings_accepted(self) -> None:
        # No post_init validation in this contract - plain dataclass
        cmd = ArchiveTaskSnapshotCommandV1(task_id="", source_path="", requested_by="")
        assert cmd.task_id == ""
        assert cmd.source_path == ""
        assert cmd.requested_by == ""


class TestGetTaskSnapshotManifestQueryV1HappyPath:
    def test_construction(self) -> None:
        q = GetTaskSnapshotManifestQueryV1(archive_id="archive-42")
        assert q.archive_id == "archive-42"


class TestArchiveManifestV1HappyPath:
    def test_construction(self) -> None:
        m = ArchiveManifestV1(archive_id="a1", location="/archive/a1", status="active")
        assert m.archive_id == "a1"
        assert m.location == "/archive/a1"
        assert m.status == "active"


class TestTaskSnapshotArchivedEventV1HappyPath:
    def test_construction(self) -> None:
        evt = TaskSnapshotArchivedEventV1(task_id="task-1", archive_id="a1")
        assert evt.task_id == "task-1"
        assert evt.archive_id == "a1"


class TestTaskSnapshotArchiveError:
    def test_raise_and_catch(self) -> None:
        err = TaskSnapshotArchiveError("snapshot failed")
        assert str(err) == "snapshot failed"
        assert isinstance(err, Exception)
