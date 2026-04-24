"""Tests for polaris.domain.verification.existence_gate."""

from __future__ import annotations

import os
import tempfile

from polaris.domain.verification.existence_gate import (
    ExistenceGate,
    check_mode,
)


class TestExistenceGate:
    def test_all_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create files
            open(os.path.join(tmpdir, "a.py"), "w").close()
            open(os.path.join(tmpdir, "b.py"), "w").close()
            result = ExistenceGate.check(["a.py", "b.py"], tmpdir)
            assert result.mode == "modify"
            assert result.all_exist is True
            assert result.all_missing is False
            assert result.existing == ["a.py", "b.py"]
            assert result.missing == []

    def test_all_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = ExistenceGate.check(["a.py", "b.py"], tmpdir)
            assert result.mode == "create"
            assert result.all_exist is False
            assert result.all_missing is True
            assert result.existing == []
            assert result.missing == ["a.py", "b.py"]

    def test_mixed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "a.py"), "w").close()
            result = ExistenceGate.check(["a.py", "b.py"], tmpdir)
            assert result.mode == "mixed"
            assert result.all_exist is False
            assert result.all_missing is False
            assert result.existing == ["a.py"]
            assert result.missing == ["b.py"]

    def test_mode_hint_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = ExistenceGate.check(["a.py"], tmpdir, mode_hint="create")
            assert result.mode == "create"

    def test_empty_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = ExistenceGate.check([], tmpdir)
            assert result.all_exist is False
            assert result.all_missing is False

    def test_filter_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "a.py"), "w").close()
            result = ExistenceGate.filter_existing(["a.py", "b.py"], tmpdir)
            assert result == ["a.py"]

    def test_filter_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "a.py"), "w").close()
            result = ExistenceGate.filter_missing(["a.py", "b.py"], tmpdir)
            assert result == ["b.py"]


class TestCheckMode:
    def test_convenience_function(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = check_mode(["a.py"], tmpdir)
            assert result.mode == "create"
