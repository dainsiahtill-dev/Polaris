"""Unit tests for `archive.factory_archive` public contracts.

Tests cover dataclass construction, immutability, equality/hashing,
serialization/deserialization, edge cases, and error contracts.
"""

from __future__ import annotations

import dataclasses
import pickle

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

    def test_construction_with_all_fields(self) -> None:
        cmd = ArchiveFactoryRunCommandV1(
            run_id="run-001",
            source_path="/tmp/factory",
            requested_by="director",
        )
        assert cmd.run_id == "run-001"
        assert cmd.source_path == "/tmp/factory"
        assert cmd.requested_by == "director"

    def test_frozen_prevents_mutation(self) -> None:
        cmd = ArchiveFactoryRunCommandV1(run_id="r1", source_path="/tmp", requested_by="user")
        with pytest.raises(dataclasses.FrozenInstanceError):
            cmd.run_id = "r2"  # type: ignore[misc]

    def test_equality_same_values(self) -> None:
        cmd1 = ArchiveFactoryRunCommandV1(run_id="r1", source_path="/tmp", requested_by="u1")
        cmd2 = ArchiveFactoryRunCommandV1(run_id="r1", source_path="/tmp", requested_by="u1")
        assert cmd1 == cmd2

    def test_inequality_different_values(self) -> None:
        cmd1 = ArchiveFactoryRunCommandV1(run_id="r1", source_path="/tmp", requested_by="u1")
        cmd2 = ArchiveFactoryRunCommandV1(run_id="r2", source_path="/tmp", requested_by="u1")
        assert cmd1 != cmd2

    def test_hash_consistency(self) -> None:
        cmd1 = ArchiveFactoryRunCommandV1(run_id="r1", source_path="/tmp", requested_by="u1")
        cmd2 = ArchiveFactoryRunCommandV1(run_id="r1", source_path="/tmp", requested_by="u1")
        assert hash(cmd1) == hash(cmd2)

    def test_can_use_as_dict_key(self) -> None:
        cmd = ArchiveFactoryRunCommandV1(run_id="r1", source_path="/tmp", requested_by="u1")
        d = {cmd: "value"}
        assert d[cmd] == "value"

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

    def test_unicode_fields(self) -> None:
        cmd = ArchiveFactoryRunCommandV1(
            run_id="工厂运行-001",
            source_path="/tmp/工厂路径",
            requested_by="用户",
        )
        assert cmd.run_id == "工厂运行-001"
        assert cmd.source_path == "/tmp/工厂路径"
        assert cmd.requested_by == "用户"

    def test_repr_contains_class_name(self) -> None:
        cmd = ArchiveFactoryRunCommandV1(run_id="r1", source_path="/tmp", requested_by="u1")
        assert "ArchiveFactoryRunCommandV1" in repr(cmd)

    def test_asdict_serialization(self) -> None:
        cmd = ArchiveFactoryRunCommandV1(run_id="r1", source_path="/tmp", requested_by="u1")
        data = dataclasses.asdict(cmd)
        assert data == {"run_id": "r1", "source_path": "/tmp", "requested_by": "u1"}

    def test_pickle_roundtrip(self) -> None:
        cmd = ArchiveFactoryRunCommandV1(run_id="r1", source_path="/tmp", requested_by="u1")
        restored = pickle.loads(pickle.dumps(cmd))
        assert cmd == restored


class TestGetFactoryArchiveManifestQueryV1:
    """Tests for GetFactoryArchiveManifestQueryV1 dataclass."""

    def test_construction(self) -> None:
        q = GetFactoryArchiveManifestQueryV1(archive_id="archive-001")
        assert q.archive_id == "archive-001"

    def test_frozen_prevents_mutation(self) -> None:
        q = GetFactoryArchiveManifestQueryV1(archive_id="a1")
        with pytest.raises(dataclasses.FrozenInstanceError):
            q.archive_id = "a2"  # type: ignore[misc]

    def test_equality(self) -> None:
        q1 = GetFactoryArchiveManifestQueryV1(archive_id="a1")
        q2 = GetFactoryArchiveManifestQueryV1(archive_id="a1")
        assert q1 == q2

    def test_inequality(self) -> None:
        q1 = GetFactoryArchiveManifestQueryV1(archive_id="a1")
        q2 = GetFactoryArchiveManifestQueryV1(archive_id="a2")
        assert q1 != q2

    def test_asdict_serialization(self) -> None:
        q = GetFactoryArchiveManifestQueryV1(archive_id="a1")
        data = dataclasses.asdict(q)
        assert data == {"archive_id": "a1"}

    def test_pickle_roundtrip(self) -> None:
        q = GetFactoryArchiveManifestQueryV1(archive_id="a1")
        restored = pickle.loads(pickle.dumps(q))
        assert q == restored


class TestArchiveManifestV1:
    """Tests for ArchiveManifestV1 dataclass."""

    def test_construction(self) -> None:
        m = ArchiveManifestV1(
            archive_id="archive-001",
            location="s3://bucket/key",
            status="completed",
        )
        assert m.archive_id == "archive-001"
        assert m.location == "s3://bucket/key"
        assert m.status == "completed"

    def test_frozen_prevents_mutation(self) -> None:
        m = ArchiveManifestV1(archive_id="a1", location="/tmp", status="ok")
        with pytest.raises(dataclasses.FrozenInstanceError):
            m.status = "failed"  # type: ignore[misc]

    def test_various_statuses(self) -> None:
        for status in ("pending", "completed", "failed", "archived"):
            m = ArchiveManifestV1(archive_id="id", location="loc", status=status)
            assert m.status == status

    def test_equality(self) -> None:
        m1 = ArchiveManifestV1(archive_id="a", location="l", status="s")
        m2 = ArchiveManifestV1(archive_id="a", location="l", status="s")
        assert m1 == m2

    def test_inequality(self) -> None:
        m1 = ArchiveManifestV1(archive_id="a1", location="l", status="s")
        m2 = ArchiveManifestV1(archive_id="a2", location="l", status="s")
        assert m1 != m2

    def test_asdict_serialization(self) -> None:
        m = ArchiveManifestV1(archive_id="a1", location="s3://bucket", status="ok")
        data = dataclasses.asdict(m)
        assert data == {
            "archive_id": "a1",
            "location": "s3://bucket",
            "status": "ok",
        }

    def test_pickle_roundtrip(self) -> None:
        m = ArchiveManifestV1(archive_id="a1", location="/tmp", status="ok")
        restored = pickle.loads(pickle.dumps(m))
        assert m == restored


class TestFactoryArchivedEventV1:
    """Tests for FactoryArchivedEventV1 dataclass."""

    def test_construction(self) -> None:
        evt = FactoryArchivedEventV1(run_id="run-001", archive_id="archive-001")
        assert evt.run_id == "run-001"
        assert evt.archive_id == "archive-001"

    def test_frozen_prevents_mutation(self) -> None:
        evt = FactoryArchivedEventV1(run_id="r1", archive_id="a1")
        with pytest.raises(dataclasses.FrozenInstanceError):
            evt.run_id = "r2"  # type: ignore[misc]

    def test_equality(self) -> None:
        e1 = FactoryArchivedEventV1(run_id="r1", archive_id="a1")
        e2 = FactoryArchivedEventV1(run_id="r1", archive_id="a1")
        assert e1 == e2

    def test_inequality(self) -> None:
        e1 = FactoryArchivedEventV1(run_id="r1", archive_id="a1")
        e2 = FactoryArchivedEventV1(run_id="r2", archive_id="a1")
        assert e1 != e2

    def test_asdict_serialization(self) -> None:
        evt = FactoryArchivedEventV1(run_id="r1", archive_id="a1")
        data = dataclasses.asdict(evt)
        assert data == {"run_id": "r1", "archive_id": "a1"}

    def test_pickle_roundtrip(self) -> None:
        evt = FactoryArchivedEventV1(run_id="r1", archive_id="a1")
        restored = pickle.loads(pickle.dumps(evt))
        assert evt == restored


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

    def test_empty_message(self) -> None:
        with pytest.raises(FactoryArchiveError):
            raise FactoryArchiveError("")

    def test_unicode_message(self) -> None:
        with pytest.raises(FactoryArchiveError) as exc_info:
            raise FactoryArchiveError("归档失败")
        assert "归档失败" in str(exc_info.value)

    def test_error_with_cause(self) -> None:
        cause = ValueError("original error")
        with pytest.raises(FactoryArchiveError) as exc_info:
            raise FactoryArchiveError("wrapped") from cause
        assert exc_info.value.__cause__ is cause


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
