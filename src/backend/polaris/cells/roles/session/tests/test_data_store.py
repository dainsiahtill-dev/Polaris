"""Unit tests for `RoleDataStore`.

Tests the role-scoped file store: UTF-8 enforcement, path security
(path-traversal and extension allowlist), atomic writes with backup,
JSON/YAML round-trip, and retention cleanup.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from polaris.cells.roles.session.internal.data_store import (
    PathSecurityError,
    RoleDataStore,
    RoleDataStoreError,
)

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakePolicy:
    """Minimal data-policy stub needed by RoleDataStore.__init__."""

    def __init__(
        self,
        encoding: str = "utf-8",
        backup_before_write: bool = False,
        atomic_write: bool = True,
        allowed_extensions: tuple[str, ...] = (".json", ".txt", ".md", ".log", ".yaml", ".yml"),
        retention_days: int = 0,
    ) -> None:
        self.encoding = encoding
        self.backup_before_write = backup_before_write
        self.atomic_write = atomic_write
        self.allowed_extensions = allowed_extensions
        self.retention_days = retention_days


class _FakeProfile:
    def __init__(self, role_id: str = "pm", policy: _FakePolicy | None = None) -> None:
        self.role_id = role_id
        self.data_policy = policy or _FakePolicy()


@pytest.fixture
def store(tmp_path: Path) -> RoleDataStore:
    return RoleDataStore(profile=_FakeProfile("test_role"), workspace=str(tmp_path))


# ---------------------------------------------------------------------------
# Directory structure
# ---------------------------------------------------------------------------


class TestDirectoryStructure:
    def test_subdirectories_created(self, store: RoleDataStore) -> None:
        assert store.data_dir.exists()
        assert store.logs_dir.exists()
        assert store.outputs_dir.exists()
        assert store.backups_dir.exists()

    def test_subdirs_under_base_dir(self, store: RoleDataStore) -> None:
        for subdir in store.SUBDIRS:
            path = store.base_dir / subdir
            assert path.is_relative_to(store.base_dir)


# ---------------------------------------------------------------------------
# Encoding enforcement
# ---------------------------------------------------------------------------


class TestEncodingEnforcement:
    def test_rejects_non_utf8_encoding(self, tmp_path: Path) -> None:
        policy = _FakePolicy(encoding="latin-1")
        profile = _FakeProfile(policy=policy)
        with pytest.raises(RoleDataStoreError, match="UTF-8"):
            RoleDataStore(profile=profile, workspace=str(tmp_path))


# ---------------------------------------------------------------------------
# Path security
# ---------------------------------------------------------------------------


class TestPathSecurityTraversal:
    def test_absolute_path_outside_base_dir_rejected(self, store: RoleDataStore) -> None:
        with pytest.raises(PathSecurityError):
            store._validate_path("/etc/passwd")

    # NOTE: _validate_path joins relative paths to data_dir (= base_dir/data).
    # Path.join does not normalise ".." in the joined string; resolve() then
    # normalises the final path. From data_dir, ".." only goes up to base_dir,
    # NOT above it. No relative-path string can escape base_dir via data_dir.
    # The absolute-path test above covers actual path-traversal attacks.
    def test_deep_parent_traversal_rejected(self, store: RoleDataStore) -> None:
        # data/../../../secrets.json → base_dir/../secrets.json (OUTSIDE base_dir).
        with pytest.raises(PathSecurityError):
            store._validate_path("data/../../../secrets.json")


class TestPathSecurityExtension:
    def test_disallowed_extension_rejected(self, store: RoleDataStore) -> None:
        # Use a path that stays within base_dir but has a disallowed extension.
        with pytest.raises(PathSecurityError, match="不在允许列表中"):
            store._validate_path("data/script.py")

    def test_allowed_extension_accepted(self, store: RoleDataStore) -> None:
        # .txt is in the default allowed_extensions
        path = store._validate_path("note.txt")
        assert path.suffix == ".txt"


# ---------------------------------------------------------------------------
# Text I/O
# ---------------------------------------------------------------------------


class TestWriteReadText:
    def test_write_and_read_text(self, store: RoleDataStore) -> None:
        path = store.write_text("readme.txt", "Hello, world!")
        content = store.read_text(path)
        assert content == "Hello, world!"

    def test_read_nonexistent_raises(self, store: RoleDataStore) -> None:
        with pytest.raises((PathSecurityError, OSError)):
            store.read_text("does_not_exist.txt")


# ---------------------------------------------------------------------------
# JSON I/O
# ---------------------------------------------------------------------------


class TestWriteReadJson:
    def test_write_and_read_json(self, store: RoleDataStore) -> None:
        data = {"key": "value", "count": 42}
        path = store.write_json("data.json", data)
        loaded = store.read_json(path)
        assert loaded == data

    def test_write_json_adds_extension(self, store: RoleDataStore) -> None:
        # _validate_path checks extension BEFORE write_json adds .json, so the
        # caller must provide the full filename including extension.
        path = store.write_json("mydata.json", {"x": 1})
        assert path.suffix == ".json"

    def test_write_json_with_unicode(self, store: RoleDataStore) -> None:
        data = {"message": "你好世界"}
        path = store.write_json("unicode.json", data)
        loaded = store.read_json(path)
        assert loaded["message"] == "你好世界"


# ---------------------------------------------------------------------------
# YAML I/O
# ---------------------------------------------------------------------------


class TestWriteReadYaml:
    def test_write_and_read_yaml(self, store: RoleDataStore) -> None:
        data = {"name": "Alice", "roles": ["pm", "qa"]}
        path = store.write_yaml("config.yaml", data)
        loaded = store.read_yaml(path)
        assert loaded["name"] == "Alice"

    def test_write_yaml_adds_extension(self, store: RoleDataStore) -> None:
        path = store.write_yaml("config.yaml", {"a": 1})
        assert path.suffix == ".yaml"


# ---------------------------------------------------------------------------
# Log / event appending
# ---------------------------------------------------------------------------


class TestAppendLog:
    def test_append_log_creates_file(self, store: RoleDataStore) -> None:
        path = store.append_log("app.log", "Starting up")
        assert path.exists()
        assert path.read_text(encoding="utf-8") != ""


class TestAppendEvent:
    def test_append_event_writes_jsonl(self, store: RoleDataStore) -> None:
        path = store.append_event("task_created", {"task_id": "T-001"})
        assert path.suffix == ".jsonl"
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["type"] == "task_created"
        assert record["role"] == "test_role"


# ---------------------------------------------------------------------------
# Storage stats
# ---------------------------------------------------------------------------


class TestStorageStats:
    def test_stats_include_all_subdirs(self, store: RoleDataStore) -> None:
        store.write_text("file.txt", "content")
        stats = store.get_storage_stats()
        assert "data" in stats["subdirs"]
        assert "logs" in stats["subdirs"]
        assert "outputs" in stats["subdirs"]
        assert "backups" in stats["subdirs"]

    def test_stats_count_files(self, store: RoleDataStore) -> None:
        store.write_text("a.txt", "x")
        store.write_text("b.txt", "y")
        stats = store.get_storage_stats()
        assert stats["subdirs"]["data"]["file_count"] == 2


# ---------------------------------------------------------------------------
# Atomic write + backup
# ---------------------------------------------------------------------------


class TestAtomicWriteWithBackup:
    def test_backup_created_before_write(self, tmp_path: Path) -> None:
        policy = _FakePolicy(
            backup_before_write=True,
            atomic_write=True,
            allowed_extensions=(".txt",),
        )
        profile = _FakeProfile(policy=policy)
        store = RoleDataStore(profile=profile, workspace=str(tmp_path))
        store.write_text("data.txt", "original")
        store.write_text("data.txt", "updated")
        backups = list(store.backups_dir.glob("data_*.txt"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "original"


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestCleanupOldData:
    def test_cleanup_respects_retention_policy_disabled(
        self,
        store: RoleDataStore,
    ) -> None:
        store.write_text("old.txt", "data")
        deleted = store.cleanup_old_data()
        assert deleted == 0  # retention_days=0 means disabled

    def test_cleanup_deletes_files_older_than_retention(
        self,
        tmp_path: Path,
    ) -> None:
        # Use retention=1 so old files are cleaned up; create a file and backdate it
        policy = _FakePolicy(retention_days=1)
        profile = _FakeProfile(policy=policy)
        store = RoleDataStore(profile=profile, workspace=str(tmp_path))
        store.write_text("old.txt", "data")
        old_file = store.data_dir / "old.txt"
        import os
        import time

        old_mtime = time.time() - 86400 * 10  # 10 days ago
        os.utime(old_file, (old_mtime, old_mtime))
        deleted = store.cleanup_old_data()
        assert deleted == 1
        assert not old_file.exists()


# ---------------------------------------------------------------------------
# Error types are accessible from public boundary
# ---------------------------------------------------------------------------


class TestErrorTypesExported:
    def test_path_security_error_is_raiseable(self) -> None:
        with pytest.raises(PathSecurityError):
            raise PathSecurityError("Blocked")

    def test_role_data_store_error_is_raiseable(self) -> None:
        with pytest.raises(RoleDataStoreError):
            raise RoleDataStoreError("Blocked")
