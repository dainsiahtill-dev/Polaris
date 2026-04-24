"""Tests for polaris.infrastructure.storage.adapter."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from polaris.infrastructure.storage.adapter import (
    FileSystemAdapter,
    clear_adapter_cache,
    get_storage_adapter,
)


class TestStorageAdapter:
    def test_workspace_is_absolute(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            assert os.path.isabs(adapter.workspace)

    def test_resolve_runtime_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            path = adapter.resolve_path("runtime/test.json")
            assert "runtime" in path
            assert "test.json" in path

    def test_resolve_workspace_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            path = adapter.resolve_path("workspace/data.txt")
            # Path resolves to persistent root, check it's absolute
            assert os.path.isabs(path)
            assert "data.txt" in path

    def test_resolve_unsupported_prefix_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            with pytest.raises(ValueError, match="Unsupported path prefix"):
                adapter.resolve_path("unknown/path")

    def test_ensure_dir_creates_parent_directory(self) -> None:
        """ensure_dir creates parent directories for the given path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            test_path = os.path.join(tmpdir, "subdir", "nested", "file.txt")
            # ensure_parent_dir creates "subdir/nested" when we reference "file.txt"
            result = adapter.ensure_dir(test_path)
            # The parent of the file should exist
            assert os.path.isdir(os.path.dirname(result))

    def test_write_text_and_read_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            test_file = os.path.join(tmpdir, "test.txt")
            adapter.write_text(test_file, "Hello, World!")

            content = adapter.read_text(test_file)
            assert content == "Hello, World!"

    def test_read_text_nonexistent_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            result = adapter.read_text(os.path.join(tmpdir, "nonexistent.txt"))
            assert result is None

    def test_write_json_and_read_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            test_file = os.path.join(tmpdir, "test.json")
            data = {"key": "value", "number": 42}

            adapter.write_json(test_file, data)
            result = adapter.read_json(test_file)
            assert result == data

    def test_read_json_nonexistent_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            result = adapter.read_json(os.path.join(tmpdir, "nonexistent.json"))
            assert result is None

    def test_append_jsonl_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            test_file = os.path.join(tmpdir, "test.jsonl")

            adapter.append_jsonl(test_file, {"record": 1})
            adapter.append_jsonl(test_file, {"record": 2})

            records = adapter.read_jsonl(test_file)
            assert len(records) == 2
            assert records[0]["record"] == 1
            assert records[1]["record"] == 2

    def test_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            test_file = os.path.join(tmpdir, "exists.txt")

            assert adapter.exists(test_file) is False
            Path(test_file).touch()
            assert adapter.exists(test_file) is True

    def test_is_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            test_file = os.path.join(tmpdir, "file.txt")

            assert adapter.is_file(test_file) is False
            Path(test_file).touch()
            assert adapter.is_file(test_file) is True

    def test_is_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            subdir = os.path.join(tmpdir, "subdir")

            assert adapter.is_dir(subdir) is False
            os.makedirs(subdir)
            assert adapter.is_dir(subdir) is True

    def test_delete_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            test_file = os.path.join(tmpdir, "to_delete.txt")
            Path(test_file).touch()

            result = adapter.delete(test_file)
            assert result is True
            assert not os.path.exists(test_file)

    def test_delete_nonexistent_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            result = adapter.delete(os.path.join(tmpdir, "nonexistent"))
            assert result is False

    def test_to_absolute_with_runtime_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            result = adapter._to_absolute("runtime/test.txt")
            assert result is not None

    def test_to_absolute_relative_to_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            result = adapter._to_absolute("relative/path.txt")
            assert result.startswith(tmpdir)

    def test_to_absolute_outside_workspace_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            # Absolute path outside workspace should raise
            with pytest.raises(ValueError, match="outside workspace boundary"):
                adapter._to_absolute("C:/completely/different/path.txt")

    def test_read_jsonl_iter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FileSystemAdapter(tmpdir)
            test_file = os.path.join(tmpdir, "test.jsonl")

            adapter.append_jsonl(test_file, {"a": 1})
            adapter.append_jsonl(test_file, {"b": 2})

            records = list(adapter.read_jsonl_iter(test_file))
            assert len(records) == 2


class TestGetStorageAdapter:
    def test_returns_adapter(self) -> None:
        clear_adapter_cache()
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = get_storage_adapter(tmpdir)
            assert isinstance(adapter, FileSystemAdapter)

    def test_caches_adapter(self) -> None:
        clear_adapter_cache()
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter1 = get_storage_adapter(tmpdir)
            adapter2 = get_storage_adapter(tmpdir)
            assert adapter1 is adapter2

    def test_different_workspaces_different_adapters(self) -> None:
        clear_adapter_cache()
        with tempfile.TemporaryDirectory() as tmpdir1, tempfile.TemporaryDirectory() as tmpdir2:
            adapter1 = get_storage_adapter(tmpdir1)
            adapter2 = get_storage_adapter(tmpdir2)
            assert adapter1 is not adapter2


class TestClearAdapterCache:
    def test_clears_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter1 = get_storage_adapter(tmpdir)
            clear_adapter_cache()
            adapter2 = get_storage_adapter(tmpdir)
            assert adapter1 is not adapter2
