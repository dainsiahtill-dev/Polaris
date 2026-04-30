"""Tests for polaris.infrastructure.storage.local_fs_adapter module."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter


class TestLocalFileSystemAdapterRead:
    def test_read_text_default_encoding(self):
        adapter = LocalFileSystemAdapter()
        with patch.object(Path, "read_text", return_value="hello") as mock_read:
            result = adapter.read_text("/tmp/test.txt")
        assert result == "hello"
        mock_read.assert_called_once_with(encoding="utf-8")

    def test_read_text_custom_encoding(self):
        adapter = LocalFileSystemAdapter()
        with patch.object(Path, "read_text", return_value="hello") as mock_read:
            result = adapter.read_text("/tmp/test.txt", encoding="latin-1")
        mock_read.assert_called_once_with(encoding="latin-1")

    def test_read_bytes(self):
        adapter = LocalFileSystemAdapter()
        with patch.object(Path, "read_bytes", return_value=b"hello") as mock_read:
            result = adapter.read_bytes("/tmp/test.bin")
        assert result == b"hello"
        mock_read.assert_called_once()


class TestLocalFileSystemAdapterWrite:
    def test_write_text_creates_parent_dirs(self):
        adapter = LocalFileSystemAdapter()
        mock_parent = MagicMock()
        with patch.object(Path, "parent", mock_parent):
            with patch.object(Path, "write_text") as mock_write:
                with patch("builtins.open", MagicMock()):
                    adapter.write_text("/tmp/test.txt", "hello")
        mock_parent.mkdir.assert_called_once_with(parents=True, exist_ok=True)

    def test_write_text_returns_byte_count(self):
        adapter = LocalFileSystemAdapter()
        with patch.object(Path, "parent", MagicMock()):
            with patch("builtins.open", MagicMock()):
                result = adapter.write_text("/tmp/test.txt", "hello")
        assert result == 5

    def test_write_text_atomic_success(self):
        adapter = LocalFileSystemAdapter()
        mock_tmp = MagicMock()
        mock_tmp.name = "/tmp/tmpfile"
        with patch("tempfile.NamedTemporaryFile", return_value=mock_tmp):
            with patch.object(Path, "replace") as mock_replace:
                with patch.object(Path, "parent", MagicMock()):
                    adapter.write_text("/tmp/test.txt", "hello", atomic=True)
        mock_replace.assert_called_once()

    def test_write_text_atomic_failure_cleanup(self):
        adapter = LocalFileSystemAdapter()
        mock_tmp = MagicMock()
        mock_tmp.name = "/tmp/tmpfile"
        with patch("tempfile.NamedTemporaryFile", return_value=mock_tmp):
            with patch.object(Path, "replace", side_effect=RuntimeError):
                with patch.object(Path, "unlink") as mock_unlink:
                    with patch.object(Path, "parent", MagicMock()):
                        with pytest.raises(RuntimeError):
                            adapter.write_text("/tmp/test.txt", "hello", atomic=True)
        mock_unlink.assert_called_once_with(missing_ok=True)

    def test_write_bytes_creates_parent_dirs(self):
        adapter = LocalFileSystemAdapter()
        mock_parent = MagicMock()
        with patch.object(Path, "parent", mock_parent):
            with patch("builtins.open", MagicMock()):
                adapter.write_bytes("/tmp/test.bin", b"hello")
        mock_parent.mkdir.assert_called_once_with(parents=True, exist_ok=True)

    def test_write_bytes_returns_length(self):
        adapter = LocalFileSystemAdapter()
        with patch.object(Path, "parent", MagicMock()):
            with patch("builtins.open", MagicMock()):
                result = adapter.write_bytes("/tmp/test.bin", b"hello")
        assert result == 5

    def test_append_text_creates_parent_dirs(self):
        adapter = LocalFileSystemAdapter()
        mock_parent = MagicMock()
        with patch.object(Path, "parent", mock_parent):
            with patch("builtins.open", MagicMock()):
                adapter.append_text("/tmp/test.txt", "hello")
        mock_parent.mkdir.assert_called_once_with(parents=True, exist_ok=True)

    def test_append_text_returns_byte_count(self):
        adapter = LocalFileSystemAdapter()
        with patch.object(Path, "parent", MagicMock()):
            with patch("builtins.open", MagicMock()):
                result = adapter.append_text("/tmp/test.txt", "hello")
        assert result == 5


class TestLocalFileSystemAdapterWriteJsonAtomic:
    def test_write_json_atomic_serializes_data(self):
        adapter = LocalFileSystemAdapter()
        with patch.object(adapter, "write_text", return_value=10) as mock_write:
            receipt = adapter.write_json_atomic("/tmp/test.json", {"key": "value"})
        mock_write.assert_called_once()
        written = mock_write.call_args[0][1]
        assert json.loads(written) == {"key": "value"}

    def test_write_json_atomic_uses_atomic_flag(self):
        adapter = LocalFileSystemAdapter()
        with patch.object(adapter, "write_text", return_value=10) as mock_write:
            adapter.write_json_atomic("/tmp/test.json", {"key": "value"})
        assert mock_write.call_args[1]["atomic"] is True

    def test_write_json_atomic_receipt_fields(self):
        adapter = LocalFileSystemAdapter()
        with patch.object(adapter, "write_text", return_value=10):
            receipt = adapter.write_json_atomic("/tmp/test.json", {"key": "value"})
        assert receipt.logical_path == "test.json"
        assert receipt.absolute_path == "/tmp/test.json"
        assert receipt.bytes_written == 10

    def test_write_json_atomic_custom_indent(self):
        adapter = LocalFileSystemAdapter()
        with patch.object(adapter, "write_text", return_value=10) as mock_write:
            adapter.write_json_atomic("/tmp/test.json", {"key": "value"}, indent=4)
        written = mock_write.call_args[0][1]
        assert "    " in written  # 4-space indent


class TestLocalFileSystemAdapterExists:
    def test_exists_true(self):
        adapter = LocalFileSystemAdapter()
        with patch("os.path.exists", return_value=True) as mock_exists:
            result = adapter.exists("/tmp/test.txt")
        assert result is True
        mock_exists.assert_called_once_with("/tmp/test.txt")

    def test_exists_false(self):
        adapter = LocalFileSystemAdapter()
        with patch("os.path.exists", return_value=False) as mock_exists:
            result = adapter.exists("/tmp/test.txt")
        assert result is False


class TestLocalFileSystemAdapterIsFile:
    def test_is_file_true(self):
        adapter = LocalFileSystemAdapter()
        with patch("os.path.isfile", return_value=True) as mock_isfile:
            result = adapter.is_file("/tmp/test.txt")
        assert result is True
        mock_isfile.assert_called_once_with("/tmp/test.txt")

    def test_is_file_false(self):
        adapter = LocalFileSystemAdapter()
        with patch("os.path.isfile", return_value=False) as mock_isfile:
            result = adapter.is_file("/tmp/test.txt")
        assert result is False


class TestLocalFileSystemAdapterIsDir:
    def test_is_dir_true(self):
        adapter = LocalFileSystemAdapter()
        with patch("os.path.isdir", return_value=True) as mock_isdir:
            result = adapter.is_dir("/tmp/testdir")
        assert result is True
        mock_isdir.assert_called_once_with("/tmp/testdir")

    def test_is_dir_false(self):
        adapter = LocalFileSystemAdapter()
        with patch("os.path.isdir", return_value=False) as mock_isdir:
            result = adapter.is_dir("/tmp/testdir")
        assert result is False


class TestLocalFileSystemAdapterRemove:
    def test_remove_success(self):
        adapter = LocalFileSystemAdapter()
        with patch("os.remove") as mock_remove:
            result = adapter.remove("/tmp/test.txt")
        assert result is True
        mock_remove.assert_called_once_with("/tmp/test.txt")

    def test_remove_missing_ok_true(self):
        adapter = LocalFileSystemAdapter()
        with patch("os.remove", side_effect=FileNotFoundError) as mock_remove:
            result = adapter.remove("/tmp/test.txt", missing_ok=True)
        assert result is True
        mock_remove.assert_called_once_with("/tmp/test.txt")

    def test_remove_missing_ok_false(self):
        adapter = LocalFileSystemAdapter()
        with patch("os.remove", side_effect=FileNotFoundError) as mock_remove:
            result = adapter.remove("/tmp/test.txt", missing_ok=False)
        assert result is False
        mock_remove.assert_called_once_with("/tmp/test.txt")
