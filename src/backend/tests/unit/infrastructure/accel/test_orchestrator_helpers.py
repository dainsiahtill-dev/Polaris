"""Tests for polaris.infrastructure.accel.verify.orchestrator_helpers module."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from polaris.infrastructure.accel.verify.orchestrator_helpers import (
    _append_unfinished_entries,
    _build_changed_files_fingerprint,
    _cache_entry_is_failure,
    _cache_file_path,
    _cache_key,
    _can_use_cached_entry,
    _classify_verify_failures,
    _is_executor_failure_result,
    _is_failure,
    _load_cache_entries,
    _normalize_cached_result,
    _normalize_changed_path,
    _normalize_live_result,
    _normalize_positive_int,
    _parse_utc,
    _prune_cache_entries,
    _remaining_wall_time_seconds,
    _safe_callback_call,
    _tail_output_text,
    _timeboxed_command_timeout,
    _write_cache_entries_atomic,
)


class TestNormalizePositiveInt:
    """Tests for _normalize_positive_int function."""

    def test_positive_int_unchanged(self) -> None:
        """Positive integers should remain unchanged."""
        assert _normalize_positive_int(42, 10) == 42
        assert _normalize_positive_int(100, 10) == 100

    def test_zero_becomes_minimum(self) -> None:
        """Zero should become minimum (1)."""
        assert _normalize_positive_int(0, 10) == 1

    def test_negative_becomes_minimum(self) -> None:
        """Negative numbers should become minimum (1)."""
        assert _normalize_positive_int(-5, 10) == 1

    def test_string_int_parsed(self) -> None:
        """String integers should be parsed."""
        assert _normalize_positive_int("42", 10) == 42
        assert _normalize_positive_int("0", 10) == 1

    def test_invalid_type_returns_default(self) -> None:
        """Invalid types should return default value."""
        assert _normalize_positive_int("abc", 10) == 10
        assert _normalize_positive_int(None, 10) == 10
        assert _normalize_positive_int([], 10) == 10


class TestNormalizeChangedPath:
    """Tests for _normalize_changed_path function."""

    def test_relative_path_normalized(self, tmp_path: Path) -> None:
        """Relative paths should be normalized."""
        result_key, result_path = _normalize_changed_path(tmp_path, "file.txt")
        assert result_key == "file.txt"
        assert result_path == tmp_path / "file.txt"

    def test_absolute_path_resolved(self, tmp_path: Path) -> None:
        """Absolute paths should be resolved relative to project."""
        abs_path = tmp_path / "subdir" / "file.txt"
        result_key, result_path = _normalize_changed_path(tmp_path, str(abs_path))
        assert "subdir/file.txt" in result_key
        assert result_path == abs_path

    def test_backslash_conversion(self, tmp_path: Path) -> None:
        """Backslashes should be converted to forward slashes."""
        result_key, _ = _normalize_changed_path(tmp_path, r"dir\subdir\file.txt")
        assert "\\" not in result_key

    def test_path_outside_project(self, tmp_path: Path) -> None:
        """Path outside project should return raw path with resolved abs."""
        outside_path = Path("C:/other/path/file.txt")
        _result_key, result_path = _normalize_changed_path(tmp_path, str(outside_path))
        # Path should be the resolved absolute path
        assert result_path == outside_path


class TestBuildChangedFilesFingerprint:
    """Tests for _build_changed_files_fingerprint function."""

    def test_empty_list(self, tmp_path: Path) -> None:
        """Empty list should return empty result."""
        result = _build_changed_files_fingerprint(tmp_path, [])
        assert result == []

    def test_none_input(self, tmp_path: Path) -> None:
        """None input should return empty result."""
        result = _build_changed_files_fingerprint(tmp_path, None)
        assert result == []

    def test_existing_file(self, tmp_path: Path) -> None:
        """Existing file should have exists=True and metadata."""
        test_file = tmp_path / "existing.txt"
        test_file.write_text("content")
        result = _build_changed_files_fingerprint(tmp_path, ["existing.txt"])
        assert len(result) == 1
        assert result[0]["exists"] is True
        assert result[0]["size"] == 7  # "content"
        assert "mtime_ns" in result[0]
        assert result[0]["is_dir"] is False

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Nonexistent file should have exists=False."""
        result = _build_changed_files_fingerprint(tmp_path, ["nonexistent.txt"])
        assert len(result) == 1
        assert result[0]["exists"] is False

    def test_results_sorted(self, tmp_path: Path) -> None:
        """Results should be sorted by path."""
        # Create some files
        (tmp_path / "z_file.txt").write_text("z")
        (tmp_path / "a_file.txt").write_text("a")
        (tmp_path / "m_file.txt").write_text("m")
        result = _build_changed_files_fingerprint(tmp_path, ["z_file.txt", "a_file.txt", "m_file.txt"])
        keys = [r["path"] for r in result]
        assert keys == sorted(keys)


class TestCacheKey:
    """Tests for _cache_key function."""

    def test_same_inputs_same_key(self, tmp_path: Path) -> None:
        """Same inputs should produce same cache key."""
        fingerprint = [{"path": "file.txt", "exists": True}]
        key1 = _cache_key("pytest", tmp_path, fingerprint)
        key2 = _cache_key("pytest", tmp_path, fingerprint)
        assert key1 == key2

    def test_different_command_different_key(self, tmp_path: Path) -> None:
        """Different commands should produce different keys."""
        fingerprint = [{"path": "file.txt", "exists": True}]
        key1 = _cache_key("pytest", tmp_path, fingerprint)
        key2 = _cache_key("ruff", tmp_path, fingerprint)
        assert key1 != key2

    def test_different_fingerprint_different_key(self, tmp_path: Path) -> None:
        """Different fingerprints should produce different keys."""
        fp1 = [{"path": "file1.txt", "exists": True}]
        fp2 = [{"path": "file2.txt", "exists": True}]
        key1 = _cache_key("pytest", tmp_path, fp1)
        key2 = _cache_key("pytest", tmp_path, fp2)
        assert key1 != key2

    def test_returns_sha256_hex(self, tmp_path: Path) -> None:
        """Should return a SHA256 hex string."""
        key = _cache_key("pytest", tmp_path, [])
        assert len(key) == 64  # SHA256 hex length
        assert all(c in "0123456789abcdef" for c in key)


class TestParseUtc:
    """Tests for _parse_utc function."""

    def test_iso_format_with_z(self) -> None:
        """Should parse ISO format with Z suffix."""
        result = _parse_utc("2024-01-15T10:30:00Z")
        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_iso_format_with_plus_offset(self) -> None:
        """Should parse ISO format with +00:00 offset."""
        result = _parse_utc("2024-01-15T10:30:00+00:00")
        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_naive_datetime_gets_utc(self) -> None:
        """Naive datetime should get UTC timezone."""
        result = _parse_utc("2024-01-15T10:30:00")
        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_none_input(self) -> None:
        """None input should return None."""
        assert _parse_utc(None) is None

    def test_empty_string(self) -> None:
        """Empty string should return None."""
        assert _parse_utc("") is None
        assert _parse_utc("   ") is None

    def test_invalid_format(self) -> None:
        """Invalid format should return None."""
        assert _parse_utc("not-a-date") is None


class TestIsFailure:
    """Tests for _is_failure function."""

    def test_exit_code_zero_not_failure(self) -> None:
        """Exit code 0 should not be a failure."""
        assert _is_failure({"exit_code": 0}) is False

    def test_exit_code_nonzero_is_failure(self) -> None:
        """Non-zero exit code should be a failure."""
        assert _is_failure({"exit_code": 1}) is True
        assert _is_failure({"exit_code": 127}) is True

    def test_missing_exit_code_is_failure(self) -> None:
        """Missing exit code should default to failure."""
        assert _is_failure({}) is True


class TestIsExecutorFailureResult:
    """Tests for _is_executor_failure_result function."""

    def test_timed_out_is_failure(self) -> None:
        """Timed out result should be executor failure."""
        assert _is_executor_failure_result({"timed_out": True}) is True

    def test_cancelled_is_failure(self) -> None:
        """Cancelled result should be executor failure."""
        assert _is_executor_failure_result({"cancelled": True}) is True

    def test_stalled_is_failure(self) -> None:
        """Stalled result should be executor failure."""
        assert _is_executor_failure_result({"stalled": True}) is True

    def test_external_cancel_reason(self) -> None:
        """External cancel reason should be executor failure."""
        assert _is_executor_failure_result({"cancel_reason": "external_cancel"}) is True

    def test_stall_timeout_reason(self) -> None:
        """Stall timeout reason should be executor failure."""
        assert _is_executor_failure_result({"cancel_reason": "stall_timeout"}) is True

    def test_agent_accel_error_marker(self) -> None:
        """Stderr with agent-accel error should be executor failure."""
        assert _is_executor_failure_result({"stderr": "agent-accel process error: something"}) is True

    def test_threadpool_error_marker(self) -> None:
        """Stderr with threadpool error should be executor failure."""
        # The marker is "threadpool future error:" (with colon)
        assert _is_executor_failure_result({"stderr": "threadpool future error: something"}) is True
        # Also "threadpool timeout" marker
        assert _is_executor_failure_result({"stderr": "threadpool timeout reached"}) is True

    def test_normal_failure_not_executor(self) -> None:
        """Normal failure with exit_code != 0 should not be executor failure."""
        result = _is_executor_failure_result({"exit_code": 1, "stderr": "test error"})
        assert result is False


class TestClassifyVerifyFailures:
    """Tests for _classify_verify_failures function."""

    def test_empty_results(self) -> None:
        """Empty results should have failure_kind 'none'."""
        result = _classify_verify_failures([])
        assert result["failure_kind"] == "none"
        assert result["failed_commands"] == []

    def test_all_pass(self) -> None:
        """All passing commands should have failure_kind 'none'."""
        results = [
            {"command": "pytest", "exit_code": 0},
            {"command": "ruff", "exit_code": 0},
        ]
        result = _classify_verify_failures(results)
        assert result["failure_kind"] == "none"

    def test_project_gate_failed(self) -> None:
        """Non-executor failures should be project_gate_failed."""
        results = [
            {"command": "pytest", "exit_code": 1, "stderr": "test failed"},
        ]
        result = _classify_verify_failures(results)
        assert result["failure_kind"] == "project_gate_failed"
        assert result["failed_commands"] == ["pytest"]

    def test_executor_failed(self) -> None:
        """Executor failures should be classified correctly."""
        results = [
            {"command": "pytest", "exit_code": 1, "timed_out": True},
        ]
        result = _classify_verify_failures(results)
        assert result["failure_kind"] == "executor_failed"
        assert result["executor_failed_commands"] == ["pytest"]

    def test_mixed_failures(self) -> None:
        """Mixed failures should be classified correctly."""
        results = [
            {"command": "pytest", "exit_code": 1, "timed_out": True},
            {"command": "ruff", "exit_code": 1, "stderr": "lint error"},
        ]
        result = _classify_verify_failures(results)
        assert result["failure_kind"] == "mixed_failed"
        assert "pytest" in result["executor_failed_commands"]
        assert "ruff" in result["project_failed_commands"]

    def test_deduplicates_commands(self) -> None:
        """Duplicate commands should be deduplicated."""
        results = [
            {"command": "pytest", "exit_code": 1},
            {"command": "pytest", "exit_code": 1},
        ]
        result = _classify_verify_failures(results)
        assert len(result["failed_commands"]) == 1


class TestNormalizeLiveResult:
    """Tests for _normalize_live_result function."""

    def test_normalizes_all_fields(self) -> None:
        """Should normalize all required fields."""
        result = _normalize_live_result(
            {
                "command": "pytest tests/",
                "exit_code": 0,
                "duration_seconds": 10.5,
                "stdout": "output",
                "stderr": "errors",
                "timed_out": False,
                "cancelled": False,
                "stalled": False,
                "cancel_reason": "",
            }
        )
        assert result["command"] == "pytest tests/"
        assert result["exit_code"] == 0
        assert result["duration_seconds"] == 10.5
        assert result["stdout"] == "output"
        assert result["stderr"] == "errors"
        assert result["cached"] is False

    def test_defaults_for_missing_fields(self) -> None:
        """Should provide defaults for missing fields."""
        result = _normalize_live_result({})
        assert result["command"] == ""
        assert result["exit_code"] == 1  # default
        assert result["duration_seconds"] == 0.0
        assert result["cached"] is False


class TestNormalizeCachedResult:
    """Tests for _normalize_cached_result function."""

    def test_normalizes_cached_result(self) -> None:
        """Should normalize cached result."""
        entry = {
            "result": {
                "exit_code": 0,
                "duration_seconds": 5.0,
                "stdout": "cached output",
                "stderr": "",
            },
            "cache_kind": "success",
        }
        result = _normalize_cached_result("pytest", entry)
        assert result["command"] == "pytest"
        assert result["exit_code"] == 0
        assert result["cached"] is True
        assert result["cache_kind"] == "success"

    def test_failure_cache_kind(self) -> None:
        """Should handle failure cache kind."""
        entry = {
            "result": {"exit_code": 1},
            "cache_kind": "failure",
        }
        result = _normalize_cached_result("pytest", entry)
        assert result["cache_kind"] == "failure"


class TestCacheEntryIsFailure:
    """Tests for _cache_entry_is_failure function."""

    def test_failure_cache_kind(self) -> None:
        """Cache kind 'failure' should be failure."""
        entry = {"cache_kind": "failure", "result": {}}
        assert _cache_entry_is_failure(entry) is True

    def test_success_cache_kind_not_failure(self) -> None:
        """Cache kind 'success' should not be failure."""
        entry = {"cache_kind": "success", "result": {"exit_code": 0}}
        assert _cache_entry_is_failure(entry) is False

    def test_timed_out_result(self) -> None:
        """Timed out result should be failure."""
        entry = {"result": {"exit_code": 0, "timed_out": True}}
        assert _cache_entry_is_failure(entry) is True

    def test_nonzero_exit_code(self) -> None:
        """Non-zero exit code should be failure."""
        entry = {"result": {"exit_code": 1}}
        assert _cache_entry_is_failure(entry) is True


class TestCanUseCachedEntry:
    """Tests for _can_use_cached_entry function."""

    def test_allow_failed_with_failure(self) -> None:
        """Should allow failed when allow_failed=True."""
        entry = {"cache_kind": "failure", "result": {}}
        assert _can_use_cached_entry(entry, allow_failed=True) is True

    def test_allow_failed_without_failure(self) -> None:
        """Should allow success when allow_failed=True."""
        entry = {"cache_kind": "success", "result": {}}
        assert _can_use_cached_entry(entry, allow_failed=True) is True

    def test_disallow_failed_with_failure(self) -> None:
        """Should disallow failed when allow_failed=False."""
        entry = {"cache_kind": "failure", "result": {}}
        assert _can_use_cached_entry(entry, allow_failed=False) is False

    def test_disallow_failed_without_failure(self) -> None:
        """Should allow success when allow_failed=False."""
        entry = {"cache_kind": "success", "result": {}}
        assert _can_use_cached_entry(entry, allow_failed=False) is True


class TestTailOutputText:
    """Tests for _tail_output_text function."""

    def test_short_text_unchanged(self) -> None:
        """Text shorter than limit should be unchanged."""
        text = "short text"
        result = _tail_output_text(text, 100)
        assert result == text

    def test_long_text_truncated(self) -> None:
        """Text longer than limit should be truncated from end."""
        text = "a" * 1000
        result = _tail_output_text(text, 100)
        # Returns the last 100 characters (tail)
        assert len(result) == 100
        assert result == "a" * 100  # Last 100 'a's

    def test_none_input(self) -> None:
        """None input should return empty string."""
        result = _tail_output_text(None, 100)
        assert result == ""


class TestRemainingWallTimeSeconds:
    """Tests for _remaining_wall_time_seconds function."""

    def test_no_limit(self) -> None:
        """None limit should return None."""
        result = _remaining_wall_time_seconds(started_at=0.0, max_wall_time_seconds=None)
        assert result is None

    def test_time_remaining(self) -> None:
        """Should return remaining time."""
        started = time.perf_counter() - 5.0  # 5 seconds ago
        result = _remaining_wall_time_seconds(started_at=started, max_wall_time_seconds=10.0)
        assert result is not None
        assert result > 0

    def test_time_expired(self) -> None:
        """Expired time should return 0."""
        started = time.perf_counter() - 100.0  # 100 seconds ago
        result = _remaining_wall_time_seconds(started_at=started, max_wall_time_seconds=10.0)
        assert result == 0.0


class TestTimeboxedCommandTimeout:
    """Tests for _timeboxed_command_timeout function."""

    def test_no_remaining_wall_time(self) -> None:
        """Zero remaining time should return 0."""
        result = _timeboxed_command_timeout(per_command_timeout=120, remaining_wall_time=0.0)
        assert result == 0

    def test_less_than_one_second(self) -> None:
        """Less than 1 second remaining should return 0."""
        result = _timeboxed_command_timeout(per_command_timeout=120, remaining_wall_time=0.5)
        assert result == 0

    def test_normal_case(self) -> None:
        """Normal case should return min of command timeout and remaining."""
        result = _timeboxed_command_timeout(per_command_timeout=120, remaining_wall_time=60.0)
        assert result == 60

    def test_command_timeout_smaller(self) -> None:
        """Command timeout smaller than remaining should be used."""
        result = _timeboxed_command_timeout(per_command_timeout=30, remaining_wall_time=60.0)
        assert result == 30


class TestAppendUnfinishedEntries:
    """Tests for _append_unfinished_entries function."""

    def test_appends_new_commands(self) -> None:
        """Should append new commands to unfinished list."""
        unfinished: list[dict] = []
        _append_unfinished_entries(unfinished_items=unfinished, commands=["cmd1", "cmd2"], reason="timeout")
        assert len(unfinished) == 2
        assert unfinished[0]["command"] == "cmd1"
        assert unfinished[0]["reason"] == "timeout"

    def test_skips_existing_commands(self) -> None:
        """Should not duplicate existing commands."""
        unfinished = [{"command": "cmd1", "reason": "old"}]
        _append_unfinished_entries(unfinished_items=unfinished, commands=["cmd1", "cmd2"], reason="new")
        assert len(unfinished) == 2

    def test_skips_empty_commands(self) -> None:
        """Should skip empty command strings."""
        unfinished: list[dict] = []
        _append_unfinished_entries(unfinished_items=unfinished, commands=["", "cmd1", ""], reason="test")
        assert len(unfinished) == 1


class TestSafeCallbackCall:
    """Tests for _safe_callback_call function."""

    def test_calls_method(self) -> None:
        """Should call the specified method."""
        called = []

        class MockCallback:
            def on_start(self, job_id: str, total: int) -> None:
                called.append((job_id, total))

        callback = MockCallback()
        _safe_callback_call(callback, "on_start", "job1", 10)
        assert called == [("job1", 10)]

    def test_missing_method_no_error(self) -> None:
        """Missing method should not raise error."""
        _safe_callback_call(object(), "on_start", "job1", 10)  # type: ignore

    def test_type_error_fallback(self) -> None:
        """TypeError should be caught and method called with positional args only."""
        called = []

        class MockCallback:
            def on_start(self, job_id: str, total: int) -> None:
                called.append((job_id, total))

        callback = MockCallback()
        # Call with extra keyword arg that the method doesn't accept
        _safe_callback_call(callback, "on_start", "job1", 10, extra_kwarg=True)
        assert called == [("job1", 10)]


class TestPruneCacheEntries:
    """Tests for _prune_cache_entries function."""

    def test_empty_entries(self) -> None:
        """Empty entries should return empty."""
        entries: dict[str, dict] = {}
        pruned, was_pruned = _prune_cache_entries(entries, ttl_seconds=3600, max_entries=100)
        assert pruned == {}
        assert was_pruned is False

    def test_prunes_expired_entries(self) -> None:
        """Should prune expired entries."""
        now = datetime.now(timezone.utc)
        entries = {
            "old": {
                "saved_utc": (now - timedelta(seconds=7200)).isoformat(),
                "ttl_seconds": 3600,
            },
            "new": {
                "saved_utc": (now - timedelta(seconds=1800)).isoformat(),
                "ttl_seconds": 3600,
            },
        }
        pruned, was_pruned = _prune_cache_entries(entries, ttl_seconds=3600, max_entries=100)
        assert "old" not in pruned
        assert "new" in pruned
        assert was_pruned is True

    def test_respects_max_entries(self) -> None:
        """Should respect max_entries limit."""
        now = datetime.now(timezone.utc)
        entries = {f"entry_{i}": {"saved_utc": now.isoformat(), "ttl_seconds": 3600} for i in range(50)}
        pruned, was_pruned = _prune_cache_entries(entries, ttl_seconds=3600, max_entries=10)
        assert len(pruned) == 10
        assert was_pruned is True


class TestLoadCacheEntries:
    """Tests for _load_cache_entries function."""

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Non-existent file should return empty dict."""
        result = _load_cache_entries(tmp_path / "nonexistent.json")
        assert result == {}

    def test_invalid_json(self, tmp_path: Path) -> None:
        """Invalid JSON should return empty dict."""
        cache_file = tmp_path / "cache.json"
        cache_file.write_text("not valid json {")
        result = _load_cache_entries(cache_file)
        assert result == {}

    def test_valid_cache_file(self, tmp_path: Path) -> None:
        """Valid cache file should be loaded."""
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "entries": {
                        "key1": {"command": "pytest", "cache_kind": "success"},
                        "key2": {"command": "ruff", "cache_kind": "failure"},
                    },
                }
            )
        )
        result = _load_cache_entries(cache_file)
        assert len(result) == 2
        assert "key1" in result


class TestWriteCacheEntriesAtomic:
    """Tests for _write_cache_entries_atomic function."""

    @pytest.mark.skipif(os.name == "nt", reason="Windows file locking issues with atomic writes")
    def test_writes_file(self, tmp_path: Path) -> None:
        """Should write cache entries to file."""
        cache_path = tmp_path / "cache.json"
        entries = {
            "key1": {"command": "pytest", "result": {"exit_code": 0}},
        }
        _write_cache_entries_atomic(cache_path, entries)
        assert cache_path.exists()

    @pytest.mark.skipif(os.name == "nt", reason="Windows file locking issues with atomic writes")
    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Should create parent directories."""
        cache_path = tmp_path / "subdir" / "cache.json"
        _write_cache_entries_atomic(cache_path, {})
        assert cache_path.exists()

    @pytest.mark.skipif(os.name == "nt", reason="Windows file locking issues with atomic writes")
    def test_overwrites_existing(self, tmp_path: Path) -> None:
        """Should overwrite existing file."""
        cache_path = tmp_path / "cache.json"
        cache_path.write_text("old content")
        _write_cache_entries_atomic(cache_path, {"key": {"data": "new"}})
        content = json.loads(cache_path.read_text())
        assert content["entries"] == {"key": {"data": "new"}}


class TestCacheFilePath:
    """Tests for _cache_file_path function."""

    def test_returns_verify_cache_path(self) -> None:
        """Should return path in verify directory."""
        paths = {"verify": Path("/tmp/verify")}
        result = _cache_file_path(paths)
        assert result == Path("/tmp/verify/command_cache.json")
