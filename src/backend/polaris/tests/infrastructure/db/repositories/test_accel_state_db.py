"""Tests for polaris.infrastructure.db.repositories.accel_state_db."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from polaris.infrastructure.db.repositories.accel_state_db import (
    FileState,
    clear_kernel_db_cache,
    compute_hash,
    delete_paths,
    load_state,
    upsert_state,
)


class TestFileState:
    """Tests for FileState dataclass."""

    def test_creation(self) -> None:
        """Happy path: create FileState."""
        state = FileState(
            path="/test/file.py",
            mtime_ns=1234567890,
            size=1024,
            content_hash="abc123",
            lang="python",
        )

        assert state.path == "/test/file.py"
        assert state.mtime_ns == 1234567890
        assert state.size == 1024
        assert state.content_hash == "abc123"
        assert state.lang == "python"

    def test_equality(self) -> None:
        """FileState instances can be compared."""
        state1 = FileState("/test.py", 1, 100, "hash1", "python")
        state2 = FileState("/test.py", 1, 100, "hash1", "python")
        state3 = FileState("/other.py", 1, 100, "hash1", "python")

        assert state1 == state2
        assert state1 != state3

    def test_not_hashable(self) -> None:
        """FileState is not hashable (dataclass without frozen=True)."""
        state = FileState("/test.py", 1, 100, "hash", "python")
        with pytest.raises(TypeError, match="unhashable"):
            _ = {state: "value"}


class TestClearKernelDbCache:
    """Tests for clear_kernel_db_cache."""

    def test_clear_cache(self) -> None:
        """Happy path: clear cache doesn't raise."""
        clear_kernel_db_cache()

    def test_idempotent(self) -> None:
        """Multiple clears are safe."""
        clear_kernel_db_cache()
        clear_kernel_db_cache()
        clear_kernel_db_cache()


class TestComputeHash:
    """Tests for compute_hash function."""

    def test_small_file(self, tmp_path: Path) -> None:
        """Happy path: hash small file by content."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world", encoding="utf-8")

        result = compute_hash(test_file)

        expected = hashlib.sha256(b"hello world").hexdigest()
        assert result == expected

    def test_empty_file(self, tmp_path: Path) -> None:
        """Hash empty file."""
        test_file = tmp_path / "empty.txt"
        test_file.write_text("", encoding="utf-8")

        result = compute_hash(test_file)

        expected = hashlib.sha256(b"").hexdigest()
        assert result == expected

    def test_large_file_uses_metadata(self, tmp_path: Path) -> None:
        """Large files use metadata-based hash."""
        test_file = tmp_path / "large.bin"
        test_file.write_bytes(b"x" * (51 * 1024 * 1024))

        result = compute_hash(test_file)

        # Should be metadata-based, not content-based
        expected_content = hashlib.sha256(b"x" * (51 * 1024 * 1024)).hexdigest()
        assert result != expected_content
        assert len(result) == 64  # sha256 hex length

    def test_custom_max_size(self, tmp_path: Path) -> None:
        """Custom max_file_size triggers metadata hash."""
        test_file = tmp_path / "medium.txt"
        test_file.write_text("hello", encoding="utf-8")

        result = compute_hash(test_file, max_file_size=1)

        # Should use metadata hash since file > max_file_size
        expected_content = hashlib.sha256(b"hello").hexdigest()
        assert result != expected_content

    def test_nonexistent_file_oserror(self, tmp_path: Path) -> None:
        """Nonexistent file falls back to path hash."""
        nonexistent = tmp_path / "does_not_exist.txt"

        result = compute_hash(nonexistent)

        expected = hashlib.sha256(str(nonexistent).encode()).hexdigest()
        assert result == expected

    def test_permission_error_fallback(self, tmp_path: Path) -> None:
        """Permission error falls back to metadata hash."""
        test_file = tmp_path / "protected.bin"
        test_file.write_bytes(b"secret data")

        with patch("pathlib.Path.open", side_effect=PermissionError("Access denied")):
            result = compute_hash(test_file)

        assert len(result) == 64

    def test_binary_file(self, tmp_path: Path) -> None:
        """Hash binary file."""
        test_file = tmp_path / "binary.bin"
        data = bytes(range(256))
        test_file.write_bytes(data)

        result = compute_hash(test_file)

        expected = hashlib.sha256(data).hexdigest()
        assert result == expected

    def test_unicode_filename(self, tmp_path: Path) -> None:
        """Hash file with unicode filename."""
        test_file = tmp_path / "文件.txt"
        test_file.write_text("content", encoding="utf-8")

        result = compute_hash(test_file)
        assert len(result) == 64


class TestLoadState:
    """Tests for load_state function."""

    def _create_mock_conn(self, rows: list[tuple] | None = None) -> MagicMock:
        """Helper to create a mock connection."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = rows or []
        mock_conn.execute.return_value = mock_cursor
        return mock_conn

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_load_empty_database(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """Happy path: load from empty database returns empty dict."""
        mock_connect.return_value = self._create_mock_conn([])

        result = load_state(tmp_path / "test.db")

        assert result == {}
        mock_connect.assert_called_once()

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_load_with_data(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """Load state with existing data."""
        rows = [
            ("/file1.py", 1000, 500, "hash1", "python"),
            ("/file2.js", 2000, 1000, "hash2", "javascript"),
        ]
        mock_connect.return_value = self._create_mock_conn(rows)

        result = load_state(tmp_path / "test.db")

        assert len(result) == 2
        assert "/file1.py" in result
        assert result["/file1.py"].mtime_ns == 1000
        assert result["/file1.py"].size == 500
        assert result["/file1.py"].content_hash == "hash1"
        assert result["/file1.py"].lang == "python"

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_load_returns_empty_on_runtime_error(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """RuntimeError returns empty dict instead of raising."""
        mock_connect.side_effect = RuntimeError("connection failed")

        result = load_state(tmp_path / "test.db")

        assert result == {}

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_load_returns_empty_on_value_error(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """ValueError returns empty dict instead of raising."""
        mock_connect.side_effect = ValueError("invalid value")

        result = load_state(tmp_path / "test.db")

        assert result == {}

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_load_closes_connection(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """Connection is always closed."""
        mock_conn = self._create_mock_conn([])
        mock_connect.return_value = mock_conn

        load_state(tmp_path / "test.db")

        mock_conn.close.assert_called_once()

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_load_timeout_check(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """Timeout check raises RuntimeError."""
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        with patch(
            "polaris.infrastructure.db.repositories.accel_state_db.time.perf_counter",
            side_effect=[0.0, 100.0],  # start, then way over timeout
        ):
            result = load_state(tmp_path / "test.db", timeout_seconds=1)

        assert result == {}


class TestUpsertState:
    """Tests for upsert_state function."""

    def _create_mock_conn(self) -> MagicMock:
        """Helper to create a mock connection."""
        mock_conn = MagicMock()
        return mock_conn

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_upsert_single_state(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """Happy path: upsert single FileState."""
        mock_conn = self._create_mock_conn()
        mock_connect.return_value = mock_conn

        states = [FileState("/test.py", 1000, 500, "hash1", "python")]
        upsert_state(tmp_path / "test.db", states, "2024-01-01T00:00:00Z")

        mock_connect.assert_called_once()
        mock_conn.executemany.assert_called_once()
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_upsert_multiple_states(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """Upsert multiple FileStates."""
        mock_conn = self._create_mock_conn()
        mock_connect.return_value = mock_conn

        states = [
            FileState("/a.py", 1, 100, "h1", "python"),
            FileState("/b.js", 2, 200, "h2", "javascript"),
        ]
        upsert_state(tmp_path / "test.db", states, "2024-01-01T00:00:00Z")

        call_args = mock_conn.executemany.call_args
        params = call_args[0][1]
        assert len(params) == 2
        assert params[0] == ("/a.py", 1, 100, "h1", "python", "2024-01-01T00:00:00Z")

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_upsert_empty_states(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """Empty states list does nothing."""
        upsert_state(tmp_path / "test.db", [], "2024-01-01T00:00:00Z")

        mock_connect.assert_not_called()

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_upsert_runtime_error_handled(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """RuntimeError is logged but not raised."""
        mock_connect.side_effect = RuntimeError("connection failed")

        states = [FileState("/test.py", 1, 100, "h1", "python")]
        # Should not raise
        upsert_state(tmp_path / "test.db", states, "2024-01-01T00:00:00Z")

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_upsert_value_error_handled(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """ValueError is logged but not raised."""
        mock_connect.side_effect = ValueError("invalid value")

        states = [FileState("/test.py", 1, 100, "h1", "python")]
        # Should not raise
        upsert_state(tmp_path / "test.db", states, "2024-01-01T00:00:00Z")

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_upsert_timeout_check(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """Timeout check raises RuntimeError which is handled."""
        mock_conn = self._create_mock_conn()
        mock_connect.return_value = mock_conn

        with patch(
            "polaris.infrastructure.db.repositories.accel_state_db.time.perf_counter",
            side_effect=[0.0, 100.0],
        ):
            states = [FileState("/test.py", 1, 100, "h1", "python")]
            upsert_state(tmp_path / "test.db", states, "2024-01-01T00:00:00Z", timeout_seconds=1)

        # Should complete without raising

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_upsert_closes_connection_on_error(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """Connection is closed even on error."""
        mock_conn = self._create_mock_conn()
        mock_conn.executemany.side_effect = RuntimeError("execute failed")
        mock_connect.return_value = mock_conn

        states = [FileState("/test.py", 1, 100, "h1", "python")]
        upsert_state(tmp_path / "test.db", states, "2024-01-01T00:00:00Z")

        mock_conn.close.assert_called_once()


class TestDeletePaths:
    """Tests for delete_paths function."""

    def _create_mock_conn(self) -> MagicMock:
        """Helper to create a mock connection."""
        mock_conn = MagicMock()
        return mock_conn

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_delete_single_path(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """Happy path: delete single path."""
        mock_conn = self._create_mock_conn()
        mock_connect.return_value = mock_conn

        delete_paths(tmp_path / "test.db", ["/file.py"])

        mock_connect.assert_called_once()
        mock_conn.executemany.assert_called_once()
        call_args = mock_conn.executemany.call_args
        assert call_args[0][1] == [("/file.py",)]
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_delete_multiple_paths(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """Delete multiple paths."""
        mock_conn = self._create_mock_conn()
        mock_connect.return_value = mock_conn

        delete_paths(tmp_path / "test.db", ["/a.py", "/b.js", "/c.ts"])

        call_args = mock_conn.executemany.call_args
        params = call_args[0][1]
        assert len(params) == 3
        assert params == [("/a.py",), ("/b.js",), ("/c.ts",)]

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_delete_empty_paths(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """Empty paths list does nothing."""
        delete_paths(tmp_path / "test.db", [])

        mock_connect.assert_not_called()

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_delete_runtime_error_handled(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """RuntimeError is logged but not raised."""
        mock_connect.side_effect = RuntimeError("connection failed")

        delete_paths(tmp_path / "test.db", ["/file.py"])

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_delete_value_error_handled(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """ValueError is logged but not raised."""
        mock_connect.side_effect = ValueError("invalid value")

        delete_paths(tmp_path / "test.db", ["/file.py"])

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_delete_timeout_check(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """Timeout check raises RuntimeError which is handled."""
        mock_conn = self._create_mock_conn()
        mock_connect.return_value = mock_conn

        with patch(
            "polaris.infrastructure.db.repositories.accel_state_db.time.perf_counter",
            side_effect=[0.0, 100.0],
        ):
            delete_paths(tmp_path / "test.db", ["/file.py"], timeout_seconds=1)

    @patch("polaris.infrastructure.db.repositories.accel_state_db._connect")
    def test_delete_closes_connection_on_error(self, mock_connect: MagicMock, tmp_path: Path) -> None:
        """Connection is closed even on error."""
        mock_conn = self._create_mock_conn()
        mock_conn.executemany.side_effect = RuntimeError("execute failed")
        mock_connect.return_value = mock_conn

        delete_paths(tmp_path / "test.db", ["/file.py"])

        mock_conn.close.assert_called_once()


class TestConnect:
    """Tests for _connect function."""

    @patch("polaris.infrastructure.db.repositories.accel_state_db._kernel_db_for")
    def test_connect_creates_table(self, mock_kernel_db: MagicMock, tmp_path: Path) -> None:
        """Happy path: _connect creates table if not exists."""
        mock_conn = MagicMock()
        mock_kernel_db.return_value.sqlite.return_value = mock_conn

        from polaris.infrastructure.db.repositories.accel_state_db import _connect

        db_path = tmp_path / "test.db"
        result = _connect(db_path)

        assert result == mock_conn
        mock_conn.execute.assert_called_once()
        create_sql = mock_conn.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS file_state" in create_sql
        assert "path TEXT PRIMARY KEY" in create_sql

    @patch("polaris.infrastructure.db.repositories.accel_state_db._kernel_db_for")
    def test_connect_creates_parent_dirs(self, mock_kernel_db: MagicMock, tmp_path: Path) -> None:
        """Creates parent directories for database."""
        mock_conn = MagicMock()
        mock_kernel_db.return_value.sqlite.return_value = mock_conn

        from polaris.infrastructure.db.repositories.accel_state_db import _connect

        db_path = tmp_path / "nested" / "deep" / "test.db"
        _connect(db_path)

        assert db_path.parent.exists()

    @patch("polaris.infrastructure.db.repositories.accel_state_db.time.sleep")
    @patch("polaris.infrastructure.db.repositories.accel_state_db._kernel_db_for")
    def test_connect_retries_on_locked(self, mock_kernel_db: MagicMock, mock_sleep: MagicMock, tmp_path: Path) -> None:
        """Retries on database locked error."""
        mock_conn = MagicMock()
        mock_kernel_db.return_value.sqlite.side_effect = [
            sqlite3.OperationalError("database is locked"),
            sqlite3.OperationalError("database is locked"),
            mock_conn,
        ]

        from polaris.infrastructure.db.repositories.accel_state_db import _connect

        db_path = tmp_path / "test.db"
        result = _connect(db_path)

        assert result == mock_conn
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)

    @patch("polaris.infrastructure.db.repositories.accel_state_db._kernel_db_for")
    def test_connect_raises_after_max_retries(self, mock_kernel_db: MagicMock, tmp_path: Path) -> None:
        """Raises RuntimeError after max retries."""
        mock_kernel_db.return_value.sqlite.side_effect = sqlite3.OperationalError("database is locked")

        from polaris.infrastructure.db.repositories.accel_state_db import _connect

        db_path = tmp_path / "test.db"
        with pytest.raises(RuntimeError) as exc_info:
            _connect(db_path)

        assert "Failed to connect to database" in str(exc_info.value)

    @patch("polaris.infrastructure.db.repositories.accel_state_db._kernel_db_for")
    def test_connect_non_locked_error_raises_immediately(self, mock_kernel_db: MagicMock, tmp_path: Path) -> None:
        """Non-locked errors raise immediately."""
        mock_kernel_db.return_value.sqlite.side_effect = sqlite3.OperationalError("no such table")

        from polaris.infrastructure.db.repositories.accel_state_db import _connect

        db_path = tmp_path / "test.db"
        with pytest.raises(RuntimeError):
            _connect(db_path)
