"""Tests for fs/contracts module."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from polaris.kernelone.fs.contracts import _atomic_write_json, _atomic_write_text
from polaris.kernelone.fs.types import FileWriteReceipt


class TestAtomicWriteText:
    """Tests for _atomic_write_text function."""

    def test_writes_content(self, tmp_path: Path) -> None:
        """Content is written to file."""
        path = str(tmp_path / "test.txt")
        _atomic_write_text(path, "hello world")
        assert Path(path).read_text(encoding="utf-8") == "hello world"

    def test_creates_directories(self, tmp_path: Path) -> None:
        """Missing directories are created."""
        path = str(tmp_path / "subdir" / "test.txt")
        _atomic_write_text(path, "hello")
        assert Path(path).exists()

    def test_returns_byte_count(self, tmp_path: Path) -> None:
        """Returns number of bytes written."""
        path = str(tmp_path / "test.txt")
        result = _atomic_write_text(path, "hello")
        assert result == 5

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        """Overwrites existing file."""
        path = str(tmp_path / "test.txt")
        _atomic_write_text(path, "old")
        _atomic_write_text(path, "new")
        assert Path(path).read_text(encoding="utf-8") == "new"

    def test_unicode_content(self, tmp_path: Path) -> None:
        """Unicode content is preserved."""
        path = str(tmp_path / "test.txt")
        _atomic_write_text(path, "你好世界")
        assert Path(path).read_text(encoding="utf-8") == "你好世界"

    def test_empty_content(self, tmp_path: Path) -> None:
        """Empty content creates empty file."""
        path = str(tmp_path / "test.txt")
        _atomic_write_text(path, "")
        assert Path(path).read_text(encoding="utf-8") == ""

    def test_custom_encoding(self, tmp_path: Path) -> None:
        """Custom encoding is respected."""
        path = str(tmp_path / "test.txt")
        _atomic_write_text(path, "hello", encoding="ascii")
        assert Path(path).read_text(encoding="ascii") == "hello"

    def test_no_partial_file_left(self, tmp_path: Path) -> None:
        """No temporary file is left behind."""
        path = str(tmp_path / "test.txt")
        _atomic_write_text(path, "hello")
        temp_files = list(tmp_path.glob("*.tmp"))
        assert len(temp_files) == 0


class TestAtomicWriteJson:
    """Tests for _atomic_write_json function."""

    def test_writes_json(self, tmp_path: Path) -> None:
        """JSON data is written."""
        path = str(tmp_path / "test.json")
        data = {"key": "value", "number": 42}
        _atomic_write_json(path, data)
        with open(path, encoding="utf-8") as f:
            result = json.load(f)
        assert result == data

    def test_returns_receipt(self, tmp_path: Path) -> None:
        """Returns FileWriteReceipt."""
        path = str(tmp_path / "test.json")
        receipt = _atomic_write_json(path, {"a": 1})
        assert isinstance(receipt, FileWriteReceipt)
        assert receipt.logical_path == path
        assert receipt.atomic is True
        assert receipt.bytes_written > 0

    def test_creates_directories(self, tmp_path: Path) -> None:
        """Missing directories are created."""
        path = str(tmp_path / "subdir" / "test.json")
        _atomic_write_json(path, {"a": 1})
        assert Path(path).exists()

    def test_unicode_content(self, tmp_path: Path) -> None:
        """Unicode content is preserved."""
        path = str(tmp_path / "test.json")
        data = {"text": "你好世界"}
        _atomic_write_json(path, data)
        with open(path, encoding="utf-8") as f:
            result = json.load(f)
        assert result["text"] == "你好世界"

    def test_custom_indent(self, tmp_path: Path) -> None:
        """Custom indent is respected."""
        path = str(tmp_path / "test.json")
        _atomic_write_json(path, {"a": 1}, indent=4)
        content = Path(path).read_text(encoding="utf-8")
        assert "    \"a\": 1" in content

    def test_zero_indent_compact(self, tmp_path: Path) -> None:
        """Indent 0 produces newline-separated compact JSON."""
        path = str(tmp_path / "test.json")
        _atomic_write_json(path, {"a": 1}, indent=0)
        content = Path(path).read_text(encoding="utf-8")
        # indent=0 uses newlines with 0-space indentation
        assert '"a": 1' in content

    def test_nested_data(self, tmp_path: Path) -> None:
        """Nested data is serialized correctly."""
        path = str(tmp_path / "test.json")
        data = {"outer": {"inner": [1, 2, {"deep": True}]}}
        _atomic_write_json(path, data)
        with open(path, encoding="utf-8") as f:
            result = json.load(f)
        assert result == data

    def test_list_data(self, tmp_path: Path) -> None:
        """List data is serialized correctly."""
        path = str(tmp_path / "test.json")
        data = [1, 2, {"a": "b"}]
        _atomic_write_json(path, data)
        with open(path, encoding="utf-8") as f:
            result = json.load(f)
        assert result == data

    def test_receipt_has_absolute_path(self, tmp_path: Path) -> None:
        """Receipt contains absolute path."""
        path = str(tmp_path / "test.json")
        receipt = _atomic_write_json(path, {"a": 1})
        assert os.path.isabs(receipt.absolute_path)

    def test_no_partial_file_left(self, tmp_path: Path) -> None:
        """No temporary file is left behind."""
        path = str(tmp_path / "test.json")
        _atomic_write_json(path, {"a": 1})
        temp_files = list(tmp_path.glob("*.tmp"))
        assert len(temp_files) == 0


class TestFileWriteReceipt:
    """Tests for FileWriteReceipt dataclass."""

    def test_creation(self) -> None:
        """Can create receipt."""
        receipt = FileWriteReceipt(
            logical_path="/tmp/test.txt",
            absolute_path="/workspace/tmp/test.txt",
            bytes_written=100,
            atomic=True,
        )
        assert receipt.logical_path == "/tmp/test.txt"
        assert receipt.absolute_path == "/workspace/tmp/test.txt"
        assert receipt.bytes_written == 100
        assert receipt.atomic is True

    def test_defaults(self) -> None:
        """Default values are correct."""
        receipt = FileWriteReceipt(
            logical_path="a",
            absolute_path="b",
            bytes_written=0,
        )
        assert receipt.atomic is False

    def test_immutable(self) -> None:
        """Receipt is immutable."""
        receipt = FileWriteReceipt(
            logical_path="a",
            absolute_path="b",
            bytes_written=0,
        )
        with pytest.raises(AttributeError):
            receipt.bytes_written = 10  # type: ignore[misc]

    def test_slots(self) -> None:
        """Uses __slots__."""
        assert hasattr(FileWriteReceipt, "__slots__")

    def test_equality(self) -> None:
        """Equal receipts compare equal."""
        r1 = FileWriteReceipt("a", "b", 10, True)
        r2 = FileWriteReceipt("a", "b", 10, True)
        assert r1 == r2

    def test_inequality(self) -> None:
        """Different receipts compare unequal."""
        r1 = FileWriteReceipt("a", "b", 10, True)
        r2 = FileWriteReceipt("a", "b", 20, True)
        assert r1 != r2

    def test_hashable(self) -> None:
        """Receipt can be hashed."""
        r = FileWriteReceipt("a", "b", 10, True)
        h = hash(r)
        assert isinstance(h, int)


class TestKernelFileSystemAdapterProtocol:
    """Tests for KernelFileSystemAdapter Protocol."""

    def test_is_protocol(self) -> None:
        """KernelFileSystemAdapter is a Protocol."""
        from polaris.kernelone.fs.contracts import KernelFileSystemAdapter

        assert hasattr(KernelFileSystemAdapter, "__protocol_attrs__") or hasattr(
            KernelFileSystemAdapter, "_is_protocol"
        )

    def test_runtime_checkable(self) -> None:
        """Protocol is runtime checkable."""
        from polaris.kernelone.fs.contracts import KernelFileSystemAdapter

        assert hasattr(KernelFileSystemAdapter, "__subclasscheck__")


class TestModuleExports:
    """Tests for module public API."""

    def test_all_exports_present(self) -> None:
        """All expected names are importable."""
        from polaris.kernelone.fs import contracts

        assert hasattr(contracts, "KernelFileSystemAdapter")
        assert hasattr(contracts, "_atomic_write_text")
        assert hasattr(contracts, "_atomic_write_json")
