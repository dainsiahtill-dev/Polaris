"""Tests for polaris.kernelone.fs.memory_snapshot quality."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from polaris.kernelone.fs.memory_snapshot import (
    ensure_memory_dir,
    get_memory_summary,
    read_memory_snapshot,
    write_loop_warning,
    write_memory_snapshot,
)

if TYPE_CHECKING:
    import pytest

# ---------------------------------------------------------------------------
# ensure_memory_dir
# ---------------------------------------------------------------------------


def test_ensure_memory_dir_creates_directory(tmp_path: Path) -> None:
    target = str(tmp_path / "a" / "b" / "memory")
    ensure_memory_dir(target)
    assert os.path.isdir(target)


def test_ensure_memory_dir_noop_empty_path() -> None:
    ensure_memory_dir("")  # must not raise


# ---------------------------------------------------------------------------
# read_memory_snapshot
# ---------------------------------------------------------------------------


def test_read_memory_snapshot_returns_dict(tmp_path: Path) -> None:
    p = tmp_path / "mem.json"
    p.write_text(json.dumps({"key": "val"}), encoding="utf-8")
    result = read_memory_snapshot(str(p))
    assert result == {"key": "val"}


def test_read_memory_snapshot_missing_file_returns_none(tmp_path: Path) -> None:
    assert read_memory_snapshot(str(tmp_path / "missing.json")) is None


def test_read_memory_snapshot_empty_path_returns_none() -> None:
    assert read_memory_snapshot("") is None


def test_read_memory_snapshot_invalid_json_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_bytes(b"not json at all {{{")
    result = read_memory_snapshot(str(p))
    assert result is None


def test_read_memory_snapshot_non_dict_json_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "list.json"
    p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert read_memory_snapshot(str(p)) is None


def test_read_memory_snapshot_logs_on_ioerror(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    p = tmp_path / "locked.json"
    p.write_text("{}", encoding="utf-8")
    with (
        patch("builtins.open", side_effect=OSError("permission denied")),
        caplog.at_level(logging.DEBUG, logger="polaris.kernelone.fs.memory_snapshot"),
    ):
        result = read_memory_snapshot(str(p))
    assert result is None
    assert any("read_memory_snapshot failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# write_memory_snapshot
# ---------------------------------------------------------------------------


def test_write_memory_snapshot_creates_file(tmp_path: Path) -> None:
    p = str(tmp_path / "snap.json")
    write_memory_snapshot(p, {"last_run_at": "2024-01-01T00:00:00Z"})
    loaded = json.loads(Path(p).read_text(encoding="utf-8"))
    assert loaded["last_run_at"] == "2024-01-01T00:00:00Z"


def test_write_memory_snapshot_empty_path_is_noop() -> None:
    write_memory_snapshot("", {"key": "val"})  # must not raise


def test_write_memory_snapshot_logs_on_ioerror(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with (
        patch("polaris.kernelone.fs.memory_snapshot.write_json_atomic", side_effect=OSError("disk full")),
        caplog.at_level(logging.DEBUG, logger="polaris.kernelone.fs.memory_snapshot"),
    ):
        write_memory_snapshot("/some/path.json", {"x": 1})
    assert any("Failed to write memory snapshot" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# get_memory_summary
# ---------------------------------------------------------------------------


class TestGetMemorySummary:
    def test_empty_snapshot_returns_none_string(self) -> None:
        assert get_memory_summary(None, 1000) == "none"

    def test_empty_dict_returns_none_string(self) -> None:
        assert get_memory_summary({}, 1000) == "none"

    def test_known_fields_included(self) -> None:
        snap = {
            "last_run_at": "2024-01-01",
            "last_summary": "all good",
            "last_next_step": "write tests",
            "last_log_path": "/tmp/log.md",
        }
        result = get_memory_summary(snap, 1000)
        assert "2024-01-01" in result
        assert "all good" in result
        assert "write tests" in result
        assert "/tmp/log.md" in result

    def test_max_chars_truncates(self) -> None:
        snap = {"last_summary": "a" * 200}
        result = get_memory_summary(snap, 50)
        assert len(result) <= 53  # 50 + "..."
        assert result.endswith("...")

    def test_max_chars_zero_means_no_truncation(self) -> None:
        snap = {"last_summary": "x" * 300}
        result = get_memory_summary(snap, 0)
        assert "x" * 300 in result


# ---------------------------------------------------------------------------
# write_loop_warning
# ---------------------------------------------------------------------------


def test_write_loop_warning_creates_log_file(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    log_path = str(tmp_path / "nested" / "warn.log")
    write_loop_warning(log_path, "disk usage high")
    content = Path(log_path).read_text(encoding="utf-8")
    assert "[WARN] disk usage high" in content
    captured = capsys.readouterr()
    assert "disk usage high" in captured.out


def test_write_loop_warning_empty_path_only_prints(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    write_loop_warning("", "warning without path")
    captured = capsys.readouterr()
    assert "warning without path" in captured.out


def test_write_loop_warning_logs_on_ioerror(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture
) -> None:
    import logging

    log_path = str(tmp_path / "warn.log")
    with (
        patch("builtins.open", side_effect=OSError("no space")),
        caplog.at_level(logging.DEBUG, logger="polaris.kernelone.fs.memory_snapshot"),
    ):
        write_loop_warning(log_path, "test message")
    # Print still happens even when write fails
    captured = capsys.readouterr()
    assert "test message" in captured.out
    assert any("write_loop_warning failed" in r.message for r in caplog.records)
