"""Tests for polaris.kernelone.fs.contracts standalone helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path

from polaris.kernelone.fs.contracts import _atomic_write_json, _atomic_write_text
from polaris.kernelone.fs.types import FileWriteReceipt


class TestAtomicWriteText:
    def test_writes_content(self, tmp_path: Path) -> None:
        target = str(tmp_path / "test.txt")
        bytes_written = _atomic_write_text(target, "hello world")
        assert Path(target).read_text(encoding="utf-8") == "hello world"
        assert bytes_written == len(b"hello world")

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        target = str(tmp_path / "a" / "b" / "test.txt")
        _atomic_write_text(target, "content")
        assert Path(target).exists()

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        target = str(tmp_path / "test.txt")
        Path(target).write_text("old", encoding="utf-8")
        _atomic_write_text(target, "new")
        assert Path(target).read_text(encoding="utf-8") == "new"

    def test_custom_encoding(self, tmp_path: Path) -> None:
        target = str(tmp_path / "test.txt")
        content = "hello"
        bytes_written = _atomic_write_text(target, content, encoding="utf-8")
        assert bytes_written == len(content.encode("utf-8"))


class TestAtomicWriteJson:
    def test_writes_json(self, tmp_path: Path) -> None:
        target = str(tmp_path / "test.json")
        data = {"key": "value", "nested": {"a": 1}}
        receipt = _atomic_write_json(target, data)
        assert isinstance(receipt, FileWriteReceipt)
        assert receipt.logical_path == target
        assert receipt.absolute_path == os.path.abspath(target)
        assert receipt.atomic is True

        loaded = json.loads(Path(target).read_text(encoding="utf-8"))
        assert loaded == data

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        target = str(tmp_path / "a" / "b" / "test.json")
        receipt = _atomic_write_json(target, {"x": 1})
        assert Path(target).exists()
        assert receipt.bytes_written > 0

    def test_custom_indent(self, tmp_path: Path) -> None:
        target = str(tmp_path / "test.json")
        _atomic_write_json(target, {"a": 1}, indent=4)
        content = Path(target).read_text(encoding="utf-8")
        assert "    " in content  # 4-space indent

    def test_non_ascii_preserved(self, tmp_path: Path) -> None:
        target = str(tmp_path / "test.json")
        data = {"message": "你好世界"}
        _atomic_write_json(target, data)
        content = Path(target).read_text(encoding="utf-8")
        assert "你好世界" in content
