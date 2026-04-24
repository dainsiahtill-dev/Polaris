"""Tests for polaris.infrastructure.storage.local_fs_adapter."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter


class TestLocalFileSystemAdapter:
    def setup_method(self) -> None:
        self.adapter = LocalFileSystemAdapter()

    def test_read_text(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as f:
            f.write("Hello, World!")
            path = f.name

        try:
            content = self.adapter.read_text(path)
            assert content == "Hello, World!"
        finally:
            os.unlink(path)

    def test_read_bytes(self) -> None:
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as f:
            f.write(b"binary content \x00\xff")
            path = f.name

        try:
            content = self.adapter.read_bytes(path)
            assert content == b"binary content \x00\xff"
        finally:
            os.unlink(path)

    def test_write_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.txt")
            size = self.adapter.write_text(path, "Hello, World!")

            assert size > 0
            assert Path(path).read_text() == "Hello, World!"

    def test_write_text_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_atomic.txt")
            size = self.adapter.write_text(path, "Atomic write", atomic=True)

            assert size > 0
            assert Path(path).read_text() == "Atomic write"

    def test_write_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.bin")
            size = self.adapter.write_bytes(path, b"binary data")

            assert size > 0
            assert Path(path).read_bytes() == b"binary data"

    def test_append_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_append.txt")

            self.adapter.append_text(path, "First ")
            self.adapter.append_text(path, "Second")

            assert Path(path).read_text() == "First Second"

    def test_write_json_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.json")
            data = {"key": "value", "number": 42}

            receipt = self.adapter.write_json_atomic(path, data)

            assert receipt.logical_path == "test.json"
            assert receipt.bytes_written > 0
            assert Path(path).read_text() == json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    def test_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "exists.txt")

            assert self.adapter.exists(path) is False
            Path(path).touch()
            assert self.adapter.exists(path) is True

    def test_is_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "file.txt")

            assert self.adapter.is_file(path) is False
            Path(path).touch()
            assert self.adapter.is_file(path) is True

    def test_is_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = os.path.join(tmpdir, "subdir")

            assert self.adapter.is_dir(subdir) is False
            os.makedirs(subdir)
            assert self.adapter.is_dir(subdir) is True

    def test_remove_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "to_delete.txt")
            Path(path).touch()

            result = self.adapter.remove(path)
            assert result is True
            assert not Path(path).exists()

    def test_remove_missing_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "nonexistent.txt")

            result = self.adapter.remove(path, missing_ok=True)
            assert result is True

    def test_remove_non_missing_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "nonexistent.txt")

            result = self.adapter.remove(path, missing_ok=False)
            assert result is False

    def test_read_nonexistent_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "nonexistent.txt")
            with pytest.raises(FileNotFoundError):
                self.adapter.read_text(path)

    def test_write_creates_parent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "subdir", "nested", "file.txt")
            self.adapter.write_text(path, "content")

            assert Path(path).exists()
            assert Path(path).read_text() == "content"
