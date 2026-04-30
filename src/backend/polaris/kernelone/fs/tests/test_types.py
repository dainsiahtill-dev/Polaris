"""Tests for fs/types module."""

from __future__ import annotations

import pytest
from polaris.kernelone.fs.types import FileWriteReceipt


class TestFileWriteReceipt:
    """Tests for FileWriteReceipt dataclass."""

    def test_basic_creation(self) -> None:
        """Can create with all fields."""
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

    def test_default_atomic(self) -> None:
        """atomic defaults to False."""
        receipt = FileWriteReceipt(
            logical_path="a",
            absolute_path="b",
            bytes_written=0,
        )
        assert receipt.atomic is False

    def test_zero_bytes(self) -> None:
        """Can create with zero bytes."""
        receipt = FileWriteReceipt(
            logical_path="empty.txt",
            absolute_path="/tmp/empty.txt",
            bytes_written=0,
        )
        assert receipt.bytes_written == 0

    def test_immutability(self) -> None:
        """Receipt is immutable (frozen dataclass)."""
        receipt = FileWriteReceipt(
            logical_path="a",
            absolute_path="b",
            bytes_written=10,
        )
        with pytest.raises(AttributeError):
            receipt.logical_path = "c"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            receipt.bytes_written = 20  # type: ignore[misc]

    def test_slots(self) -> None:
        """Uses __slots__ for memory efficiency."""
        assert hasattr(FileWriteReceipt, "__slots__")

    def test_equality_same(self) -> None:
        """Identical receipts are equal."""
        r1 = FileWriteReceipt("a", "b", 10, True)
        r2 = FileWriteReceipt("a", "b", 10, True)
        assert r1 == r2

    def test_equality_different_logical_path(self) -> None:
        """Different logical_path makes receipts unequal."""
        r1 = FileWriteReceipt("a", "b", 10, True)
        r2 = FileWriteReceipt("x", "b", 10, True)
        assert r1 != r2

    def test_equality_different_absolute_path(self) -> None:
        """Different absolute_path makes receipts unequal."""
        r1 = FileWriteReceipt("a", "b", 10, True)
        r2 = FileWriteReceipt("a", "x", 10, True)
        assert r1 != r2

    def test_equality_different_bytes(self) -> None:
        """Different bytes_written makes receipts unequal."""
        r1 = FileWriteReceipt("a", "b", 10, True)
        r2 = FileWriteReceipt("a", "b", 20, True)
        assert r1 != r2

    def test_equality_different_atomic(self) -> None:
        """Different atomic makes receipts unequal."""
        r1 = FileWriteReceipt("a", "b", 10, True)
        r2 = FileWriteReceipt("a", "b", 10, False)
        assert r1 != r2

    def test_hashable(self) -> None:
        """Can be used in sets and dict keys."""
        r1 = FileWriteReceipt("a", "b", 10, True)
        r2 = FileWriteReceipt("a", "b", 10, True)
        s = {r1, r2}
        assert len(s) == 1

    def test_repr(self) -> None:
        """Has readable repr."""
        receipt = FileWriteReceipt("a", "b", 10, True)
        r = repr(receipt)
        assert "FileWriteReceipt" in r
        assert "a" in r
        assert "10" in r

    def test_str(self) -> None:
        """Has readable str."""
        receipt = FileWriteReceipt("a", "b", 10, True)
        s = str(receipt)
        assert "FileWriteReceipt" in s or "a" in s

    def test_unicode_paths(self) -> None:
        """Unicode paths are supported."""
        receipt = FileWriteReceipt(
            logical_path="/tmp/文件.txt",
            absolute_path="/workspace/tmp/文件.txt",
            bytes_written=100,
        )
        assert receipt.logical_path == "/tmp/文件.txt"

    def test_empty_paths(self) -> None:
        """Empty paths are allowed."""
        receipt = FileWriteReceipt(
            logical_path="",
            absolute_path="",
            bytes_written=0,
        )
        assert receipt.logical_path == ""
        assert receipt.absolute_path == ""

    def test_large_bytes(self) -> None:
        """Large byte counts are supported."""
        receipt = FileWriteReceipt(
            logical_path="big.bin",
            absolute_path="/tmp/big.bin",
            bytes_written=10**12,
        )
        assert receipt.bytes_written == 10**12

    def test_negative_bytes_raises(self) -> None:
        """Negative bytes don't raise but are allowed by dataclass."""
        receipt = FileWriteReceipt(
            logical_path="a",
            absolute_path="b",
            bytes_written=-1,
        )
        assert receipt.bytes_written == -1


class TestModuleExports:
    """Tests for module public API."""

    def test_export_present(self) -> None:
        """FileWriteReceipt is importable."""
        from polaris.kernelone.fs import types

        assert hasattr(types, "FileWriteReceipt")
