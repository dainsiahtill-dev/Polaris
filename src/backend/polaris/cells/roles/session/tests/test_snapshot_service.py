"""Unit tests for `SnapshotService` — session snapshot persistence.

Uses isolated tmp_path workspaces so tests are fully independent.
Covers: dataclass round-trip, snapshot creation, listing, retrieval,
and the 50-entry TTL trim policy.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from polaris.cells.roles.session.internal.snapshot_service import (
    SessionSnapshot,
    SnapshotService,
    _compute_fingerprint,
    _utc_now,
)

if TYPE_CHECKING:
    from pathlib import Path

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def svc(tmp_path: Path) -> SnapshotService:
    return SnapshotService(tmp_path)


@pytest.fixture
def workspace_with_session(tmp_path: Path) -> tuple[Path, str]:
    """Return (tmp_path, session_id) with the snapshot dir already created."""
    session_id = "sess-test-001"
    snap_dir = SnapshotService(tmp_path)._snapshots_path(session_id).parent
    snap_dir.mkdir(parents=True, exist_ok=True)
    return tmp_path, session_id


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _write_snapshots_jsonl(path: Path, snapshots: list[SessionSnapshot]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(s.to_dict(), ensure_ascii=False) for s in snapshots]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ------------------------------------------------------------------
# SessionSnapshot dataclass
# ------------------------------------------------------------------


class TestSessionSnapshotDataclass:
    def test_to_dict_roundtrip(self) -> None:
        before = SessionSnapshot(
            session_id="sess-abc",
            messages=[
                {
                    "id": "m1",
                    "role": "user",
                    "content": "hello",
                    "thinking": "",
                    "sequence": 0,
                    "created_at": "2026-03-24T10:00:00+00:00",
                    "meta": {},
                },
            ],
            artifacts=[
                {"artifact_id": "art1", "title": "Plan", "content": "# Plan"},
            ],
            fingerprints=["fp1", "fp2"],
            timestamp="2026-03-24T10:01:00+00:00",
            snapshot_id="snap-xyz",
        )
        data = before.to_dict()
        after = SessionSnapshot.from_dict(data)
        assert after.session_id == before.session_id
        assert after.snapshot_id == before.snapshot_id
        assert after.messages == before.messages
        assert after.artifacts == before.artifacts
        assert after.fingerprints == before.fingerprints
        assert after.timestamp == before.timestamp


# ------------------------------------------------------------------
# SnapshotService.path_helpers
# ------------------------------------------------------------------


class TestSnapshotServicePaths:
    def test_snapshots_rel(self, svc: SnapshotService) -> None:
        rel = svc._snapshots_rel("sess-42")
        assert "sess-42" in rel
        assert rel.endswith("snapshots.jsonl")

    def test_snapshots_path(self, svc: SnapshotService) -> None:
        p = svc._snapshots_path("sess-99")
        assert "sess-99" in str(p)
        assert p.name == "snapshots.jsonl"


# ------------------------------------------------------------------
# SnapshotService.snapshot (mock-free — writes real JSONL)
# ------------------------------------------------------------------


class TestSnapshotServiceSnapshot:
    def test_snapshot_empty_session_returns_snapshot_with_empty_messages(self, svc: SnapshotService) -> None:
        """snapshot() must not raise even when no DB session exists."""
        snap = svc.snapshot("sess-no-messages")
        assert snap.session_id == "sess-no-messages"
        assert snap.snapshot_id != ""
        assert snap.messages == []
        assert snap.fingerprints == []
        assert "T" in snap.timestamp  # ISO format

    def test_snapshot_writes_jsonl_file(self, tmp_path: Path) -> None:
        svc = SnapshotService(tmp_path)
        snap = svc.snapshot("sess-001")
        snap_file = svc._snapshots_path("sess-001")
        assert snap_file.is_file(), "snapshots.jsonl must be created"
        lines = _read_jsonl(snap_file)
        assert len(lines) == 1
        assert lines[0]["session_id"] == "sess-001"
        assert lines[0]["snapshot_id"] == snap.snapshot_id

    def test_snapshot_generates_unique_id(self, tmp_path: Path) -> None:
        svc = SnapshotService(tmp_path)
        s1 = svc.snapshot("sess-dup")
        s2 = svc.snapshot("sess-dup")
        assert s1.snapshot_id != s2.snapshot_id

    def test_snapshot_empty_session_id_returns_empty_snapshot(self, svc: SnapshotService) -> None:
        snap = svc.snapshot("")
        assert snap.session_id == ""
        assert snap.snapshot_id == ""

    def test_snapshot_whitespace_session_id_normalized(self, svc: SnapshotService) -> None:
        snap = svc.snapshot("  ")
        assert snap.session_id == ""

    def test_snapshot_idempotent_append(self, tmp_path: Path) -> None:
        svc = SnapshotService(tmp_path)
        svc.snapshot("sess-reuse")
        svc.snapshot("sess-reuse")
        snap_file = svc._snapshots_path("sess-reuse")
        lines = _read_jsonl(snap_file)
        assert len(lines) == 2


# ------------------------------------------------------------------
# SnapshotService.list_snapshots
# ------------------------------------------------------------------


class TestSnapshotServiceList:
    def test_list_empty_returns_empty(self, svc: SnapshotService) -> None:
        assert svc.list_snapshots("sess-does-not-exist") == []

    def test_list_returns_newest_last(self, workspace_with_session: tuple[Path, str]) -> None:
        tmp_path, session_id = workspace_with_session
        svc = SnapshotService(tmp_path)

        # Write 3 snapshots directly (simulating prior turns)
        snap_file = svc._snapshots_path(session_id)
        snaps = []
        for i in range(3):
            ts = f"2026-03-24T10:{i:02d}:00+00:00"
            s = SessionSnapshot(
                session_id=session_id,
                messages=[
                    {
                        "id": f"m{i}",
                        "role": "user",
                        "content": f"msg{i}",
                        "thinking": "",
                        "sequence": i,
                        "created_at": ts,
                        "meta": {},
                    }
                ],
                artifacts=[],
                fingerprints=[],
                timestamp=ts,
                snapshot_id=f"snap-{i}",
            )
            snaps.append(s)
        _write_snapshots_jsonl(snap_file, snaps)

        listed = svc.list_snapshots(session_id)
        assert len(listed) == 3
        # Newest-last with limit
        assert listed[-1].snapshot_id == "snap-2"

    def test_list_respects_limit(self, workspace_with_session: tuple[Path, str]) -> None:
        tmp_path, session_id = workspace_with_session
        svc = SnapshotService(tmp_path)
        snap_file = svc._snapshots_path(session_id)
        snaps = [
            SessionSnapshot(
                session_id=session_id,
                messages=[],
                artifacts=[],
                fingerprints=[],
                timestamp=f"2026-03-24T10:{i:02d}:00+00:00",
                snapshot_id=f"snap-{i:02d}",
            )
            for i in range(10)
        ]
        _write_snapshots_jsonl(snap_file, snaps)
        listed = svc.list_snapshots(session_id, limit=3)
        assert len(listed) == 3


# ------------------------------------------------------------------
# SnapshotService.get_snapshot
# ------------------------------------------------------------------


class TestSnapshotServiceGet:
    def test_get_snapshot_found(self, workspace_with_session: tuple[Path, str]) -> None:
        tmp_path, session_id = workspace_with_session
        svc = SnapshotService(tmp_path)
        snap_file = svc._snapshots_path(session_id)
        target = SessionSnapshot(
            session_id=session_id,
            messages=[
                {
                    "id": "m1",
                    "role": "user",
                    "content": "find me",
                    "thinking": "",
                    "sequence": 0,
                    "created_at": "2026-03-24T10:00:00+00:00",
                    "meta": {},
                }
            ],
            artifacts=[],
            fingerprints=[],
            timestamp="2026-03-24T10:01:00+00:00",
            snapshot_id="snap-target-42",
        )
        _write_snapshots_jsonl(snap_file, [target])

        found = svc.get_snapshot("snap-target-42")
        assert found is not None
        assert found.snapshot_id == "snap-target-42"
        assert found.messages[0]["content"] == "find me"

    def test_get_snapshot_not_found(self, workspace_with_session: tuple[Path, str]) -> None:
        tmp_path, _session_id = workspace_with_session
        svc = SnapshotService(tmp_path)
        found = svc.get_snapshot("snap-does-not-exist")
        assert found is None

    def test_get_snapshot_empty_id_returns_none(self, svc: SnapshotService) -> None:
        assert svc.get_snapshot("") is None
        assert svc.get_snapshot("   ") is None


# ------------------------------------------------------------------
# SnapshotService._trim_snapshots (50-entry TTL)
# ------------------------------------------------------------------


class TestSnapshotServiceTrim:
    def test_trim_keeps_50(self, tmp_path: Path) -> None:
        svc = SnapshotService(tmp_path)
        session_id = "sess-trim-test"
        snap_file = svc._snapshots_path(session_id)

        # Write 60 snapshots directly
        snaps = [
            SessionSnapshot(
                session_id=session_id,
                messages=[],
                artifacts=[],
                fingerprints=[],
                timestamp=f"2026-01-01T{i:02d}:00:00+00:00",
                snapshot_id=f"snap-{i:04d}",
            )
            for i in range(60)
        ]
        _write_snapshots_jsonl(snap_file, snaps)

        # snapshot() calls _trim_snapshots after append
        svc.snapshot(session_id)

        lines = _read_jsonl(snap_file)
        assert len(lines) == 50, f"Expected 50, got {len(lines)}"
        ids = [ln["snapshot_id"] for ln in lines]
        # 60 written + 1 appended = 61; trim to last 50 => indices 11-60 kept
        assert "snap-0000" not in ids  # oldest 11 removed
        assert "snap-0010" not in ids  # index 10 removed
        assert "snap-0011" in ids  # oldest kept (index 11)
        assert "snap-0059" in ids  # newest kept (index 59)

    def test_trim_no_op_under_50(self, tmp_path: Path) -> None:
        svc = SnapshotService(tmp_path)
        session_id = "sess-under-limit"
        snap_file = svc._snapshots_path(session_id)
        snaps = [
            SessionSnapshot(
                session_id=session_id,
                messages=[],
                artifacts=[],
                fingerprints=[],
                timestamp=f"2026-01-01T{i:02d}:00:00+00:00",
                snapshot_id=f"snap-sm-{i}",
            )
            for i in range(10)
        ]
        _write_snapshots_jsonl(snap_file, snaps)

        svc.snapshot(session_id)  # should not trim

        lines = _read_jsonl(snap_file)
        assert len(lines) == 11  # 10 written + 1 appended by snapshot()


# ------------------------------------------------------------------
# SnapshotService.get_snapshot across multiple sessions
# ------------------------------------------------------------------


class TestSnapshotServiceCrossSession:
    def test_get_snapshot_searches_all_session_dirs(self, tmp_path: Path) -> None:
        svc = SnapshotService(tmp_path)
        target_id = "snap-cross-session-target"

        # Write one snapshot in sess-A
        snap_a = svc._snapshots_path("sess-A")
        snap_a.parent.mkdir(parents=True)
        snap_a.write_text(
            json.dumps(
                SessionSnapshot(
                    session_id="sess-A",
                    messages=[],
                    artifacts=[],
                    fingerprints=[],
                    timestamp="2026-03-24T10:00:00+00:00",
                    snapshot_id=target_id,
                ).to_dict(),
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        # Write unrelated snapshot in sess-B
        snap_b = svc._snapshots_path("sess-B")
        snap_b.parent.mkdir(parents=True)
        snap_b.write_text(
            json.dumps(
                SessionSnapshot(
                    session_id="sess-B",
                    messages=[],
                    artifacts=[],
                    fingerprints=[],
                    timestamp="2026-03-24T10:00:00+00:00",
                    snapshot_id="snap-other",
                ).to_dict(),
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        found = svc.get_snapshot(target_id)
        assert found is not None
        assert found.snapshot_id == target_id
        assert found.session_id == "sess-A"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


class TestComputeFingerprint:
    def test_deterministic(self) -> None:
        f1 = _compute_fingerprint("hello world")
        f2 = _compute_fingerprint("hello world")
        assert f1 == f2

    def test_different_content_different_hash(self) -> None:
        f1 = _compute_fingerprint("hello")
        f2 = _compute_fingerprint("world")
        assert f1 != f2

    def test_length_16(self) -> None:
        f = _compute_fingerprint("any content here")
        assert len(f) == 16

    def test_utc_now_returns_aware_datetime(self) -> None:
        dt = _utc_now()
        assert dt.tzinfo is not None
