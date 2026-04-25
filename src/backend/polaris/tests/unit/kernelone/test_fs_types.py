"""Tests for polaris.kernelone.fs.types."""

from __future__ import annotations

from polaris.kernelone.fs.types import FileWriteReceipt


class TestFileWriteReceipt:
    def test_creation(self) -> None:
        receipt = FileWriteReceipt(
            logical_path="/tmp/test.json",
            absolute_path="/workspace/tmp/test.json",
            bytes_written=128,
            atomic=True,
        )
        assert receipt.logical_path == "/tmp/test.json"
        assert receipt.absolute_path == "/workspace/tmp/test.json"
        assert receipt.bytes_written == 128
        assert receipt.atomic is True

    def test_defaults(self) -> None:
        receipt = FileWriteReceipt(
            logical_path="/tmp/test.json",
            absolute_path="/workspace/tmp/test.json",
            bytes_written=128,
        )
        assert receipt.atomic is False

    def test_immutable(self) -> None:
        receipt = FileWriteReceipt(
            logical_path="/tmp/test.json",
            absolute_path="/workspace/tmp/test.json",
            bytes_written=128,
        )
        with AttributeError:
            receipt.bytes_written = 256  # type: ignore[misc]

    def test_slots(self) -> None:
        receipt = FileWriteReceipt(
            logical_path="/tmp/test.json",
            absolute_path="/workspace/tmp/test.json",
            bytes_written=128,
        )
        # slots=True means no __dict__
        assert not hasattr(receipt, "__dict__")

    def test_equality(self) -> None:
        a = FileWriteReceipt(
            logical_path="/tmp/test.json",
            absolute_path="/workspace/tmp/test.json",
            bytes_written=128,
        )
        b = FileWriteReceipt(
            logical_path="/tmp/test.json",
            absolute_path="/workspace/tmp/test.json",
            bytes_written=128,
        )
        assert a == b

    def test_inequality(self) -> None:
        a = FileWriteReceipt(
            logical_path="/tmp/test.json",
            absolute_path="/workspace/tmp/test.json",
            bytes_written=128,
        )
        b = FileWriteReceipt(
            logical_path="/tmp/other.json",
            absolute_path="/workspace/tmp/other.json",
            bytes_written=128,
        )
        assert a != b

    def test_hashable(self) -> None:
        receipt = FileWriteReceipt(
            logical_path="/tmp/test.json",
            absolute_path="/workspace/tmp/test.json",
            bytes_written=128,
        )
        assert hash(receipt) == hash(receipt)
