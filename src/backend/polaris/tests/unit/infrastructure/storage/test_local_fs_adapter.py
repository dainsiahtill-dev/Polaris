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

    def test_remove_permission_denied_is_not_reported_as_success(self):
        """A real operation failure must not be masked as a successful delete."""
        adapter = LocalFileSystemAdapter()
        with patch("os.remove", side_effect=PermissionError("denied")) as mock_remove, pytest.raises(PermissionError):
            adapter.remove("/tmp/test.txt")
        mock_remove.assert_called_once_with("/tmp/test.txt")

    def test_remove_permission_denied_is_not_reported_as_success_when_missing_ok_false(self):
        """missing_ok only tolerates an absent file, never a denied removal."""
        adapter = LocalFileSystemAdapter()
        with patch("os.remove", side_effect=PermissionError("denied")) as mock_remove, pytest.raises(PermissionError):
            adapter.remove("/tmp/test.txt", missing_ok=False)
        mock_remove.assert_called_once_with("/tmp/test.txt")

    def test_remove_io_error_propagates_cause(self):
        """An unknown/failed effect keeps its original cause and is not retried."""
        adapter = LocalFileSystemAdapter()
        with (
            patch("os.remove", side_effect=OSError("I/O error")) as mock_remove,
            pytest.raises(OSError, match="I/O error"),
        ):
            adapter.remove("/tmp/test.txt")
        mock_remove.assert_called_once_with("/tmp/test.txt")

    def test_remove_real_file_removes_it_from_disk(self, tmp_path: Path):
        """Physical evidence: a real removal deletes the file and reports success."""
        adapter = LocalFileSystemAdapter()
        target = tmp_path / "removable.txt"
        target.write_text("data", encoding="utf-8")

        assert adapter.remove(str(target)) is True
        assert not target.exists()

    def test_remove_real_missing_file_keeps_both_established_conventions(self, tmp_path: Path):
        """A genuinely absent file keeps missing_ok=True -> True and False -> False."""
        adapter = LocalFileSystemAdapter()
        absent = tmp_path / "absent.txt"
        assert not absent.exists()

        assert adapter.remove(str(absent), missing_ok=True) is True
        assert adapter.remove(str(absent), missing_ok=False) is False

    def test_remove_real_file_permission_denied_keeps_file_on_disk(self, tmp_path: Path):
        """Physical evidence: a denied removal keeps the file and raises."""
        adapter = LocalFileSystemAdapter()
        target = tmp_path / "locked.txt"
        target.write_text("data", encoding="utf-8")

        def _denied(path, *args, **kwargs):
            raise PermissionError(f"injected remove denial: {path}")

        with patch("os.remove", _denied), pytest.raises(PermissionError):
            adapter.remove(str(target))

        assert target.exists()
        assert target.read_text(encoding="utf-8") == "data"


class TestLocalFileSystemAdapterRemoveThroughKernelFileSystem:
    """The KFS layer must surface the adapter failure instead of a false success."""

    def _kernel_fs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from polaris.kernelone.fs import KernelFileSystem
        from polaris.kernelone.fs.registry import get_default_adapter

        workspace = tmp_path / "workspace"
        runtime_root = workspace / ".polaris" / "runtime"
        runtime_root.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
        monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
        monkeypatch.delenv("KERNELONE_RAMDISK_ROOT", raising=False)
        monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
        return KernelFileSystem(str(workspace), get_default_adapter()), runtime_root

    def test_kernel_fs_remove_propagates_permission_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kernel_fs, runtime_root = self._kernel_fs(tmp_path, monkeypatch)
        target = runtime_root / "artifact.bin"
        target.write_bytes(b"data")

        with patch("os.remove", side_effect=PermissionError("denied")), pytest.raises(PermissionError):
            kernel_fs.remove("runtime/artifact.bin")

        assert target.exists()

    def test_kernel_fs_remove_missing_ok_false_still_reports_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kernel_fs, _runtime_root = self._kernel_fs(tmp_path, monkeypatch)

        assert kernel_fs.remove("runtime/absent.bin", missing_ok=True) is True
        assert kernel_fs.remove("runtime/absent.bin", missing_ok=False) is False

    def test_kernel_fs_workspace_remove_propagates_permission_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kernel_fs, _runtime_root = self._kernel_fs(tmp_path, monkeypatch)
        target = tmp_path / "workspace" / "artifact.bin"
        target.write_bytes(b"data")

        with patch("os.remove", side_effect=PermissionError("denied")), pytest.raises(PermissionError):
            kernel_fs.workspace_remove("artifact.bin")

        assert target.exists()
