"""Tests for polaris.cells.archive.factory_archive.public.contracts."""

from __future__ import annotations

import pytest
from polaris.cells.archive.factory_archive.public.contracts import (
    ArchiveFactoryRunCommandV1,
    ArchiveManifestV1,
    FactoryArchivedEventV1,
    FactoryArchiveError,
    GetFactoryArchiveManifestQueryV1,
)


class TestArchiveFactoryRunCommandV1:
    """Tests for ArchiveFactoryRunCommandV1 dataclass."""

    def test_create_with_all_fields(self) -> None:
        cmd = ArchiveFactoryRunCommandV1(
            run_id="run-001",
            source_path="/tmp/factory",
            requested_by="user-001",
        )
        assert cmd.run_id == "run-001"
        assert cmd.source_path == "/tmp/factory"
        assert cmd.requested_by == "user-001"

    def test_is_frozen(self) -> None:
        cmd = ArchiveFactoryRunCommandV1(
            run_id="run-001",
            source_path="/tmp/factory",
            requested_by="user-001",
        )
        with pytest.raises(AttributeError):
            cmd.run_id = "run-002"  # type: ignore[misc]

    def test_repr(self) -> None:
        cmd = ArchiveFactoryRunCommandV1(
            run_id="run-001",
            source_path="/tmp/factory",
            requested_by="user-001",
        )
        assert "ArchiveFactoryRunCommandV1" in repr(cmd)
        assert "run-001" in repr(cmd)

    def test_equality_same_values(self) -> None:
        cmd1 = ArchiveFactoryRunCommandV1(run_id="run-001", source_path="/tmp/factory", requested_by="user-001")
        cmd2 = ArchiveFactoryRunCommandV1(run_id="run-001", source_path="/tmp/factory", requested_by="user-001")
        assert cmd1 == cmd2

    def test_inequality_different_values(self) -> None:
        cmd1 = ArchiveFactoryRunCommandV1(run_id="run-001", source_path="/tmp/factory", requested_by="user-001")
        cmd2 = ArchiveFactoryRunCommandV1(run_id="run-002", source_path="/tmp/factory", requested_by="user-001")
        assert cmd1 != cmd2

    def test_hash_consistency(self) -> None:
        cmd1 = ArchiveFactoryRunCommandV1(run_id="run-001", source_path="/tmp/factory", requested_by="user-001")
        cmd2 = ArchiveFactoryRunCommandV1(run_id="run-001", source_path="/tmp/factory", requested_by="user-001")
        assert hash(cmd1) == hash(cmd2)

    def test_empty_strings(self) -> None:
        cmd = ArchiveFactoryRunCommandV1(run_id="", source_path="", requested_by="")
        assert cmd.run_id == ""
        assert cmd.source_path == ""
        assert cmd.requested_by == ""

    def test_special_characters_in_fields(self) -> None:
        cmd = ArchiveFactoryRunCommandV1(
            run_id="run/with/slashes",
            source_path="/tmp/path with spaces",
            requested_by="user@example.com",
        )
        assert cmd.run_id == "run/with/slashes"
        assert cmd.source_path == "/tmp/path with spaces"
        assert cmd.requested_by == "user@example.com"


class TestGetFactoryArchiveManifestQueryV1:
    """Tests for GetFactoryArchiveManifestQueryV1 dataclass."""

    def test_create(self) -> None:
        query = GetFactoryArchiveManifestQueryV1(archive_id="archive-001")
        assert query.archive_id == "archive-001"

    def test_is_frozen(self) -> None:
        query = GetFactoryArchiveManifestQueryV1(archive_id="archive-001")
        with pytest.raises(AttributeError):
            query.archive_id = "archive-002"  # type: ignore[misc]

    def test_equality(self) -> None:
        q1 = GetFactoryArchiveManifestQueryV1(archive_id="archive-001")
        q2 = GetFactoryArchiveManifestQueryV1(archive_id="archive-001")
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


class TestFactoryArchivedEventV1:
    """Tests for FactoryArchivedEventV1 dataclass."""

    def test_create(self) -> None:
        event = FactoryArchivedEventV1(
            run_id="run-001",
            archive_id="archive-001",
        )
        assert event.run_id == "run-001"
        assert event.archive_id == "archive-001"

    def test_is_frozen(self) -> None:
        event = FactoryArchivedEventV1(run_id="run-001", archive_id="archive-001")
        with pytest.raises(AttributeError):
            event.run_id = "run-002"  # type: ignore[misc]

    def test_equality(self) -> None:
        e1 = FactoryArchivedEventV1(run_id="r", archive_id="a")
        e2 = FactoryArchivedEventV1(run_id="r", archive_id="a")
        assert e1 == e2

    def test_different_events_not_equal(self) -> None:
        e1 = FactoryArchivedEventV1(run_id="r1", archive_id="a")
        e2 = FactoryArchivedEventV1(run_id="r2", archive_id="a")
        assert e1 != e2


class TestFactoryArchiveError:
    """Tests for FactoryArchiveError exception."""

    def test_is_exception_subclass(self) -> None:
        assert issubclass(FactoryArchiveError, Exception)

    def test_raise_and_catch(self) -> None:
        with pytest.raises(FactoryArchiveError):
            raise FactoryArchiveError("archive failed")

    def test_message_preserved(self) -> None:
        with pytest.raises(FactoryArchiveError) as exc_info:
            raise FactoryArchiveError("custom message")
        assert str(exc_info.value) == "custom message"

    def test_can_catch_as_exception(self) -> None:
        caught = False
        try:
            raise FactoryArchiveError("test")
        except FactoryArchiveError as e:
            caught = True
            assert isinstance(e, FactoryArchiveError)
        assert caught

    def test_error_with_empty_message(self) -> None:
        with pytest.raises(FactoryArchiveError):
            raise FactoryArchiveError("")

    def test_error_with_unicode_message(self) -> None:
        with pytest.raises(FactoryArchiveError) as exc_info:
            raise FactoryArchiveError("归档失败")
        assert "归档失败" in str(exc_info.value)


class TestModuleExports:
    """Tests for module __all__ exports."""

    def test_all_exports_present(self) -> None:
        from polaris.cells.archive.factory_archive.public import contracts as mod

        assert hasattr(mod, "__all__")
        assert "ArchiveFactoryRunCommandV1" in mod.__all__
        assert "ArchiveManifestV1" in mod.__all__
        assert "FactoryArchiveError" in mod.__all__
        assert "FactoryArchivedEventV1" in mod.__all__
        assert "GetFactoryArchiveManifestQueryV1" in mod.__all__
        assert len(mod.__all__) == 5
