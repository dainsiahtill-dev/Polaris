"""Tests for polaris.cells.archive.task_snapshot_archive.public.contracts."""

from __future__ import annotations

import pytest
from polaris.cells.archive.task_snapshot_archive.public.contracts import (
    ArchiveManifestV1,
    ArchiveTaskSnapshotCommandV1,
    GetTaskSnapshotManifestQueryV1,
    TaskSnapshotArchivedEventV1,
    TaskSnapshotArchiveError,
)


class TestArchiveTaskSnapshotCommandV1:
    """Tests for ArchiveTaskSnapshotCommandV1 dataclass."""

    def test_create_with_all_fields(self) -> None:
        cmd = ArchiveTaskSnapshotCommandV1(
            task_id="task-001",
            source_path="/tmp/snapshots",
            requested_by="user-001",
        )
        assert cmd.task_id == "task-001"
        assert cmd.source_path == "/tmp/snapshots"
        assert cmd.requested_by == "user-001"

    def test_is_frozen(self) -> None:
        cmd = ArchiveTaskSnapshotCommandV1(
            task_id="task-001",
            source_path="/tmp/snapshots",
            requested_by="user-001",
        )
        with pytest.raises(AttributeError):
            cmd.task_id = "task-002"  # type: ignore[misc]

    def test_repr(self) -> None:
        cmd = ArchiveTaskSnapshotCommandV1(
            task_id="task-001",
            source_path="/tmp/snapshots",
            requested_by="user-001",
        )
        assert "ArchiveTaskSnapshotCommandV1" in repr(cmd)
        assert "task-001" in repr(cmd)

    def test_equality_same_values(self) -> None:
        cmd1 = ArchiveTaskSnapshotCommandV1(task_id="t", source_path="s", requested_by="r")
        cmd2 = ArchiveTaskSnapshotCommandV1(task_id="t", source_path="s", requested_by="r")
        assert cmd1 == cmd2

    def test_inequality_different_values(self) -> None:
        cmd1 = ArchiveTaskSnapshotCommandV1(task_id="t1", source_path="s", requested_by="r")
        cmd2 = ArchiveTaskSnapshotCommandV1(task_id="t2", source_path="s", requested_by="r")
        assert cmd1 != cmd2

    def test_hash_consistency(self) -> None:
        cmd1 = ArchiveTaskSnapshotCommandV1(task_id="t", source_path="s", requested_by="r")
        cmd2 = ArchiveTaskSnapshotCommandV1(task_id="t", source_path="s", requested_by="r")
        assert hash(cmd1) == hash(cmd2)

    def test_empty_strings(self) -> None:
        cmd = ArchiveTaskSnapshotCommandV1(task_id="", source_path="", requested_by="")
        assert cmd.task_id == ""
        assert cmd.source_path == ""
        assert cmd.requested_by == ""

    def test_special_characters_in_fields(self) -> None:
        cmd = ArchiveTaskSnapshotCommandV1(
            task_id="task/with/slashes",
            source_path="/tmp/path with spaces",
            requested_by="user@example.com",
        )
        assert cmd.task_id == "task/with/slashes"
        assert cmd.source_path == "/tmp/path with spaces"
        assert cmd.requested_by == "user@example.com"


class TestGetTaskSnapshotManifestQueryV1:
    """Tests for GetTaskSnapshotManifestQueryV1 dataclass."""

    def test_create(self) -> None:
        query = GetTaskSnapshotManifestQueryV1(archive_id="archive-001")
        assert query.archive_id == "archive-001"

    def test_is_frozen(self) -> None:
        query = GetTaskSnapshotManifestQueryV1(archive_id="archive-001")
        with pytest.raises(AttributeError):
            query.archive_id = "archive-002"  # type: ignore[misc]

    def test_equality(self) -> None:
        q1 = GetTaskSnapshotManifestQueryV1(archive_id="archive-001")
        q2 = GetTaskSnapshotManifestQueryV1(archive_id="archive-001")
        assert q1 == q2


class TestArchiveManifestV1:
    """Tests for ArchiveManifestV1 dataclass."""

    def test_create(self) -> None:
        manifest = ArchiveManifestV1(
            archive_id="archive-001",
            location="s3://bucket/key",
            status="completed",
        )
        assert manifest.archive_id == "archive-001"
        assert manifest.location == "s3://bucket/key"
        assert manifest.status == "completed"

    def test_is_frozen(self) -> None:
        manifest = ArchiveManifestV1(
            archive_id="archive-001",
            location="s3://bucket/key",
            status="completed",
        )
        with pytest.raises(AttributeError):
            manifest.status = "failed"  # type: ignore[misc]

    def test_equality(self) -> None:
        m1 = ArchiveManifestV1(archive_id="a", location="l", status="s")
        m2 = ArchiveManifestV1(archive_id="a", location="l", status="s")
        assert m1 == m2

    def test_various_statuses(self) -> None:
        for status in ["pending", "completed", "failed", "archived"]:
            manifest = ArchiveManifestV1(archive_id="id", location="loc", status=status)
            assert manifest.status == status


class TestTaskSnapshotArchivedEventV1:
    """Tests for TaskSnapshotArchivedEventV1 dataclass."""

    def test_create(self) -> None:
        event = TaskSnapshotArchivedEventV1(
            task_id="task-001",
            archive_id="archive-001",
        )
        assert event.task_id == "task-001"
        assert event.archive_id == "archive-001"

    def test_is_frozen(self) -> None:
        event = TaskSnapshotArchivedEventV1(task_id="task-001", archive_id="archive-001")
        with pytest.raises(AttributeError):
            event.task_id = "task-002"  # type: ignore[misc]

    def test_equality(self) -> None:
        e1 = TaskSnapshotArchivedEventV1(task_id="t", archive_id="a")
        e2 = TaskSnapshotArchivedEventV1(task_id="t", archive_id="a")
        assert e1 == e2

    def test_different_events_not_equal(self) -> None:
        e1 = TaskSnapshotArchivedEventV1(task_id="t1", archive_id="a")
        e2 = TaskSnapshotArchivedEventV1(task_id="t2", archive_id="a")
        assert e1 != e2


class TestTaskSnapshotArchiveError:
    """Tests for TaskSnapshotArchiveError exception."""

    def test_is_exception_subclass(self) -> None:
        assert issubclass(TaskSnapshotArchiveError, Exception)

    def test_raise_and_catch(self) -> None:
        with pytest.raises(TaskSnapshotArchiveError):
            raise TaskSnapshotArchiveError("archive failed")

    def test_message_preserved(self) -> None:
        with pytest.raises(TaskSnapshotArchiveError) as exc_info:
            raise TaskSnapshotArchiveError("custom message")
        assert str(exc_info.value) == "custom message"

    def test_can_catch_as_exception(self) -> None:
        caught = False
        try:
            raise TaskSnapshotArchiveError("test")
        except TaskSnapshotArchiveError as e:
            caught = True
            assert isinstance(e, TaskSnapshotArchiveError)
        assert caught

    def test_error_with_empty_message(self) -> None:
        with pytest.raises(TaskSnapshotArchiveError):
            raise TaskSnapshotArchiveError("")

    def test_error_with_unicode_message(self) -> None:
        with pytest.raises(TaskSnapshotArchiveError) as exc_info:
            raise TaskSnapshotArchiveError("归档失败")
        assert "归档失败" in str(exc_info.value)


class TestModuleExports:
    """Tests for module __all__ exports."""

    def test_all_exports_present(self) -> None:
        from polaris.cells.archive.task_snapshot_archive.public import contracts as mod

        assert hasattr(mod, "__all__")
        assert "ArchiveTaskSnapshotCommandV1" in mod.__all__
        assert "ArchiveManifestV1" in mod.__all__
        assert "TaskSnapshotArchiveError" in mod.__all__
        assert "TaskSnapshotArchivedEventV1" in mod.__all__
        assert "GetTaskSnapshotManifestQueryV1" in mod.__all__
        assert len(mod.__all__) == 5
