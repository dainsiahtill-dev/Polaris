"""Unit tests for `archive.run_archive` public contracts.

Tests cover dataclass construction, immutability, equality/hashing,
serialization/deserialization, edge cases, and error contracts.
"""

from __future__ import annotations

import dataclasses
import pickle

import pytest
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
    """Tests for ArchiveRunCommandV1 dataclass."""

    def test_construction_with_all_fields(self) -> None:
        cmd = ArchiveRunCommandV1(
            run_id="run-001",
            source_path="/tmp/run-001",
            requested_by="director",
        )
        assert cmd.run_id == "run-001"
        assert cmd.source_path == "/tmp/run-001"
        assert cmd.requested_by == "director"

    def test_frozen_prevents_mutation(self) -> None:
        cmd = ArchiveRunCommandV1(run_id="r1", source_path="/tmp", requested_by="user")
        with pytest.raises(dataclasses.FrozenInstanceError):
            cmd.run_id = "r2"  # type: ignore[misc]

    def test_equality_same_values(self) -> None:
        cmd1 = ArchiveRunCommandV1(run_id="r1", source_path="/tmp", requested_by="u1")
        cmd2 = ArchiveRunCommandV1(run_id="r1", source_path="/tmp", requested_by="u1")
        assert cmd1 == cmd2

    def test_inequality_different_values(self) -> None:
        cmd1 = ArchiveRunCommandV1(run_id="r1", source_path="/tmp", requested_by="u1")
        cmd2 = ArchiveRunCommandV1(run_id="r2", source_path="/tmp", requested_by="u1")
        assert cmd1 != cmd2

    def test_hash_consistency(self) -> None:
        cmd1 = ArchiveRunCommandV1(run_id="r1", source_path="/tmp", requested_by="u1")
        cmd2 = ArchiveRunCommandV1(run_id="r1", source_path="/tmp", requested_by="u1")
        assert hash(cmd1) == hash(cmd2)

    def test_can_use_as_dict_key(self) -> None:
        cmd = ArchiveRunCommandV1(run_id="r1", source_path="/tmp", requested_by="u1")
        d = {cmd: "value"}
        assert d[cmd] == "value"

    def test_special_characters_in_fields(self) -> None:
        cmd = ArchiveRunCommandV1(
            run_id="run/with/slashes",
            source_path="/tmp/path with spaces",
            requested_by="user@example.com",
        )
        assert cmd.run_id == "run/with/slashes"
        assert cmd.source_path == "/tmp/path with spaces"
        assert cmd.requested_by == "user@example.com"

    def test_unicode_fields(self) -> None:
        cmd = ArchiveRunCommandV1(
            run_id="运行-001",
            source_path="/tmp/归档路径",
            requested_by="用户",
        )
        assert cmd.run_id == "运行-001"
        assert cmd.source_path == "/tmp/归档路径"
        assert cmd.requested_by == "用户"

    def test_repr_contains_class_name(self) -> None:
        cmd = ArchiveRunCommandV1(run_id="r1", source_path="/tmp", requested_by="u1")
        assert "ArchiveRunCommandV1" in repr(cmd)

    def test_asdict_serialization(self) -> None:
        cmd = ArchiveRunCommandV1(run_id="r1", source_path="/tmp", requested_by="u1")
        data = dataclasses.asdict(cmd)
        assert data == {"run_id": "r1", "source_path": "/tmp", "requested_by": "u1"}

    def test_pickle_roundtrip(self) -> None:
        cmd = ArchiveRunCommandV1(run_id="r1", source_path="/tmp", requested_by="u1")
        restored = pickle.loads(pickle.dumps(cmd))
        assert cmd == restored


class TestListHistoryRunsQueryV1:
    """Tests for ListHistoryRunsQueryV1 dataclass."""

    def test_default_limit(self) -> None:
        q = ListHistoryRunsQueryV1()
        assert q.limit == 20

    def test_explicit_limit(self) -> None:
        q = ListHistoryRunsQueryV1(limit=50)
        assert q.limit == 50

    def test_limit_zero(self) -> None:
        q = ListHistoryRunsQueryV1(limit=0)
        assert q.limit == 0

    def test_frozen_prevents_mutation(self) -> None:
        q = ListHistoryRunsQueryV1(limit=10)
        with pytest.raises(dataclasses.FrozenInstanceError):
            q.limit = 20  # type: ignore[misc]

    def test_equality(self) -> None:
        q1 = ListHistoryRunsQueryV1(limit=10)
        q2 = ListHistoryRunsQueryV1(limit=10)
        assert q1 == q2

    def test_asdict_serialization(self) -> None:
        q = ListHistoryRunsQueryV1(limit=10)
        data = dataclasses.asdict(q)
        assert data == {"limit": 10}


class TestGetArchiveManifestQueryV1:
    """Tests for GetArchiveManifestQueryV1 dataclass."""

    def test_construction(self) -> None:
        q = GetArchiveManifestQueryV1(archive_id="archive-001")
        assert q.archive_id == "archive-001"

    def test_frozen_prevents_mutation(self) -> None:
        q = GetArchiveManifestQueryV1(archive_id="a1")
        with pytest.raises(dataclasses.FrozenInstanceError):
            q.archive_id = "a2"  # type: ignore[misc]

    def test_equality(self) -> None:
        q1 = GetArchiveManifestQueryV1(archive_id="a1")
        q2 = GetArchiveManifestQueryV1(archive_id="a1")
        assert q1 == q2

    def test_inequality(self) -> None:
        q1 = GetArchiveManifestQueryV1(archive_id="a1")
        q2 = GetArchiveManifestQueryV1(archive_id="a2")
        assert q1 != q2

    def test_asdict_serialization(self) -> None:
        q = GetArchiveManifestQueryV1(archive_id="a1")
        data = dataclasses.asdict(q)
        assert data == {"archive_id": "a1"}


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


class TestHistoryRunsResultV1:
    """Tests for HistoryRunsResultV1 dataclass."""

    def test_construction(self) -> None:
        r = HistoryRunsResultV1(runs=("run-1", "run-2"), total=2)
        assert r.runs == ("run-1", "run-2")
        assert r.total == 2

    def test_empty_runs(self) -> None:
        r = HistoryRunsResultV1(runs=(), total=0)
        assert r.runs == ()
        assert r.total == 0

    def test_single_run(self) -> None:
        r = HistoryRunsResultV1(runs=("run-1",), total=1)
        assert r.runs == ("run-1",)
        assert r.total == 1

    def test_frozen_prevents_mutation(self) -> None:
        r = HistoryRunsResultV1(runs=("r1",), total=1)
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.total = 2  # type: ignore[misc]

    def test_equality(self) -> None:
        r1 = HistoryRunsResultV1(runs=("r1", "r2"), total=2)
        r2 = HistoryRunsResultV1(runs=("r1", "r2"), total=2)
        assert r1 == r2

    def test_inequality_different_runs(self) -> None:
        r1 = HistoryRunsResultV1(runs=("r1",), total=1)
        r2 = HistoryRunsResultV1(runs=("r2",), total=1)
        assert r1 != r2

    def test_inequality_different_total(self) -> None:
        r1 = HistoryRunsResultV1(runs=("r1",), total=1)
        r2 = HistoryRunsResultV1(runs=("r1",), total=2)
        assert r1 != r2

    def test_asdict_serialization(self) -> None:
        r = HistoryRunsResultV1(runs=("r1", "r2"), total=2)
        data = dataclasses.asdict(r)
        assert data == {"runs": ("r1", "r2"), "total": 2}

    def test_pickle_roundtrip(self) -> None:
        r = HistoryRunsResultV1(runs=("r1", "r2"), total=2)
        restored = pickle.loads(pickle.dumps(r))
        assert r == restored


class TestRunArchivedEventV1:
    """Tests for RunArchivedEventV1 dataclass."""

    def test_construction(self) -> None:
        evt = RunArchivedEventV1(run_id="run-001", archive_id="archive-001")
        assert evt.run_id == "run-001"
        assert evt.archive_id == "archive-001"

    def test_frozen_prevents_mutation(self) -> None:
        evt = RunArchivedEventV1(run_id="r1", archive_id="a1")
        with pytest.raises(dataclasses.FrozenInstanceError):
            evt.run_id = "r2"  # type: ignore[misc]

    def test_equality(self) -> None:
        e1 = RunArchivedEventV1(run_id="r1", archive_id="a1")
        e2 = RunArchivedEventV1(run_id="r1", archive_id="a1")
        assert e1 == e2

    def test_inequality(self) -> None:
        e1 = RunArchivedEventV1(run_id="r1", archive_id="a1")
        e2 = RunArchivedEventV1(run_id="r2", archive_id="a1")
        assert e1 != e2

    def test_asdict_serialization(self) -> None:
        evt = RunArchivedEventV1(run_id="r1", archive_id="a1")
        data = dataclasses.asdict(evt)
        assert data == {"run_id": "r1", "archive_id": "a1"}

    def test_pickle_roundtrip(self) -> None:
        evt = RunArchivedEventV1(run_id="r1", archive_id="a1")
        restored = pickle.loads(pickle.dumps(evt))
        assert evt == restored


class TestRunArchiveError:
    """Tests for RunArchiveError exception."""

    def test_is_exception_subclass(self) -> None:
        assert issubclass(RunArchiveError, Exception)

    def test_raise_and_catch(self) -> None:
        with pytest.raises(RunArchiveError):
            raise RunArchiveError("archive failed")

    def test_message_preserved(self) -> None:
        with pytest.raises(RunArchiveError) as exc_info:
            raise RunArchiveError("custom message")
        assert str(exc_info.value) == "custom message"

    def test_can_catch_as_exception(self) -> None:
        caught = False
        try:
            raise RunArchiveError("test")
        except RunArchiveError as e:
            caught = True
            assert isinstance(e, RunArchiveError)
        assert caught

    def test_empty_message(self) -> None:
        with pytest.raises(RunArchiveError):
            raise RunArchiveError("")

    def test_unicode_message(self) -> None:
        with pytest.raises(RunArchiveError) as exc_info:
            raise RunArchiveError("归档失败")
        assert "归档失败" in str(exc_info.value)

    def test_error_with_cause(self) -> None:
        cause = ValueError("original error")
        with pytest.raises(RunArchiveError) as exc_info:
            raise RunArchiveError("wrapped") from cause
        assert exc_info.value.__cause__ is cause


class TestModuleExports:
    """Tests for module __all__ exports."""

    def test_all_exports_present(self) -> None:
        from polaris.cells.archive.run_archive.public import contracts as mod

        assert hasattr(mod, "__all__")
        assert "ArchiveRunCommandV1" in mod.__all__
        assert "ArchiveManifestV1" in mod.__all__
        assert "GetArchiveManifestQueryV1" in mod.__all__
        assert "HistoryRunsResultV1" in mod.__all__
        assert "ListHistoryRunsQueryV1" in mod.__all__
        assert "RunArchivedEventV1" in mod.__all__
        assert "RunArchiveError" in mod.__all__
        assert len(mod.__all__) == 7
