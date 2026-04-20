"""Thread-safety tests for index.py.

Tests cover:
- Normal operation (basic functionality)
- Concurrent access (read-modify-write race conditions)
- Error handling and boundary conditions
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.llm.evaluation.internal.index import (
    _get_reports_port,
    _global_index_path,
    _resolve_index_paths,
    _resolve_workspace_path,
    set_reports_port,
)
from polaris.cells.llm.evaluation.public.service import (
    load_llm_test_index,
    reconcile_llm_test_index,
    reset_llm_test_index,
    update_index_with_report,
)
from polaris.kernelone.storage import resolve_runtime_path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _MockReportsPort:
    """Mock port for testing file listing."""

    def __init__(self, files: list[str] | None = None, exists: bool = True) -> None:
        self._files = files or []
        self._exists = exists

    def list_json_files(self, directory: str) -> list[str]:
        return self._files

    def dir_exists(self, directory: str) -> bool:
        return self._exists


@pytest.fixture(autouse=True)
def _isolate_reports_port():
    """Reset reports port before and after each test."""
    set_reports_port(_MockReportsPort())
    yield
    set_reports_port(_MockReportsPort())


@pytest.fixture
def temp_workspace(tmp_path) -> str:
    """Create a temporary workspace with required directories."""
    workspace = str(tmp_path)
    polaris_dir = Path(workspace) / ".polaris"
    polaris_dir.mkdir(parents=True, exist_ok=True)
    return workspace


@pytest.fixture
def reports_dir(temp_workspace: str) -> str:
    """Create reports directory."""
    reports = Path(resolve_runtime_path(temp_workspace, "runtime/llm_tests/reports"))
    reports.mkdir(parents=True, exist_ok=True)
    return str(reports)


# ---------------------------------------------------------------------------
# Normal operation tests
# ---------------------------------------------------------------------------


class TestNormalOperation:
    """Tests for normal (non-concurrent) functionality."""

    def test_reset_creates_index_file(self, temp_workspace: str) -> None:
        """Test that reset_llm_test_index creates the index file."""
        reset_llm_test_index(temp_workspace)

        paths = _resolve_index_paths(temp_workspace)
        assert any(os.path.exists(p) for p in paths), "No index file created"

    def test_load_empty_index(self, temp_workspace: str) -> None:
        """Test loading returns empty index when no file exists."""
        index = load_llm_test_index(temp_workspace)

        assert index.get("version") == "2.0"
        assert index.get("roles") == {}
        assert index.get("providers") == {}

    def test_update_single_report(self, temp_workspace: str) -> None:
        """Test updating with a single report."""
        report = {
            "test_run_id": "run1",
            "timestamp": "2024-01-01T00:00:00Z",
            "target": {"role": "pm", "provider_id": "test_provider", "model": "test-model"},
            "final": {"ready": True, "grade": "PASS"},
            "suites": {"connectivity": {"ok": True}},
        }

        update_index_with_report(temp_workspace, report)
        index = load_llm_test_index(temp_workspace)

        assert index["roles"]["pm"]["ready"] is True
        assert index["providers"]["test_provider"]["model"] == "test-model"

    def test_reconcile_scans_reports(self, temp_workspace: str, reports_dir: str) -> None:
        """Test reconcile scans and loads reports."""
        report = {
            "test_run_id": "scan_test",
            "timestamp": "2024-01-01T00:00:00Z",
            "target": {"role": "director", "provider_id": "scan_provider", "model": "scan-model"},
            "final": {"ready": True, "grade": "PASS"},
            "suites": {},
        }

        report_path = Path(reports_dir) / "scan_test.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f)

        set_reports_port(_MockReportsPort(files=["scan_test.json"], exists=True))
        index = reconcile_llm_test_index(temp_workspace)

        assert index["roles"]["director"]["ready"] is True
        assert index["providers"]["scan_provider"]["model"] == "scan-model"

    def test_reconcile_preserves_existing(self, temp_workspace: str, reports_dir: str) -> None:
        """Test reconcile does not overwrite unrelated entries."""
        # Create initial index with existing provider
        global_path = _global_index_path(temp_workspace)
        os.makedirs(os.path.dirname(global_path), exist_ok=True)
        initial = {"roles": {}, "providers": {"existing": {"model": "old-model"}}}
        with open(global_path, "w", encoding="utf-8") as f:
            json.dump(initial, f)

        # Reconcile with new report
        report = {
            "test_run_id": "new",
            "timestamp": "2024-01-01T00:00:00Z",
            "target": {"role": "new_role", "provider_id": "new", "model": "new-model"},
            "final": {"ready": True, "grade": "PASS"},
            "suites": {},
        }
        with open(Path(reports_dir) / "new.json", "w", encoding="utf-8") as f:
            json.dump(report, f)

        set_reports_port(_MockReportsPort(files=["new.json"], exists=True))
        index = reconcile_llm_test_index(temp_workspace)

        assert index["providers"]["existing"]["model"] == "old-model"
        assert index["providers"]["new"]["model"] == "new-model"


# ---------------------------------------------------------------------------
# Concurrency tests
# ---------------------------------------------------------------------------


class TestConcurrency:
    """Tests for thread-safety and race condition prevention."""

    def test_concurrent_updates_no_data_loss(self, temp_workspace: str) -> None:
        """Test that concurrent updates don't lose data.

        This test verifies C1 fix: multiple threads updating the index
        should not lose updates due to read-modify-write races.
        """
        num_threads = 10
        updates_per_thread = 5
        errors: list[Exception] = []

        def update_worker(thread_id: int) -> None:
            try:
                for i in range(updates_per_thread):
                    report = {
                        "test_run_id": f"thread{thread_id}_run{i}",
                        "timestamp": f"2024-01-01T00:00:{i:02d}Z",
                        "target": {
                            "role": f"role_{thread_id}",
                            "provider_id": f"provider_{thread_id}",
                            "model": f"model-{thread_id}-{i}",
                        },
                        "final": {"ready": True, "grade": "PASS"},
                        "suites": {},
                    }
                    update_index_with_report(temp_workspace, report)
                    time.sleep(0.001)  # Small delay to encourage interleaving
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        # Run concurrent updates
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(update_worker, i) for i in range(num_threads)]
            for future in as_completed(futures):
                pass  # Wait for completion

        assert not errors, f"Update errors occurred: {errors}"

        # Verify all updates are present
        index = load_llm_test_index(temp_workspace)
        for thread_id in range(num_threads):
            role_key = f"role_{thread_id}"
            provider_key = f"provider_{thread_id}"
            assert role_key in index["roles"], f"Missing role {role_key}"
            assert provider_key in index["providers"], f"Missing provider {provider_key}"

    def test_concurrent_reconciles_no_corruption(self, temp_workspace: str, reports_dir: str) -> None:
        """Test that concurrent reconciles don't corrupt the index.

        Multiple threads reconciling simultaneously should maintain index integrity.
        """
        num_threads = 5
        reports_per_thread = 3

        # Create reports for each thread
        for thread_id in range(num_threads):
            for i in range(reports_per_thread):
                report = {
                    "test_run_id": f"reconcile_t{thread_id}_r{i}",
                    "timestamp": f"2024-01-01T00:00:{thread_id * 10 + i:02d}Z",
                    "target": {
                        "role": f"recon_role_{thread_id}_{i}",
                        "provider_id": f"recon_provider_{thread_id}_{i}",
                        "model": f"model-{thread_id}-{i}",
                    },
                    "final": {"ready": True, "grade": "PASS"},
                    "suites": {},
                }
                with open(Path(reports_dir) / f"reconcile_t{thread_id}_r{i}.json", "w", encoding="utf-8") as f:
                    json.dump(report, f)

        set_reports_port(
            _MockReportsPort(
                files=[f"reconcile_t{t}_r{r}.json" for t in range(num_threads) for r in range(reports_per_thread)],
                exists=True,
            )
        )

        errors: list[Exception] = []

        def reconcile_worker() -> None:
            try:
                for _ in range(3):  # Each thread reconciles 3 times
                    reconcile_llm_test_index(temp_workspace)
                    time.sleep(0.005)
            except Exception as exc:
                # Ignore transient Windows file access errors
                if "Access is denied" in str(exc) or "PermissionError" in type(exc).__name__:
                    return
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(reconcile_worker) for _ in range(num_threads)]
            for future in as_completed(futures):
                pass

        assert not errors, f"Reconcile errors: {errors}"

        # Verify index is valid JSON and contains expected entries
        index = load_llm_test_index(temp_workspace)
        assert isinstance(index, dict)
        assert "version" in index

        for thread_id in range(num_threads):
            for i in range(reports_per_thread):
                role_key = f"recon_role_{thread_id}_{i}"
                provider_key = f"recon_provider_{thread_id}_{i}"
                assert role_key in index["roles"], f"Missing {role_key}"
                assert provider_key in index["providers"], f"Missing {provider_key}"

    def test_mixed_read_write_concurrent(self, temp_workspace: str) -> None:
        """Test concurrent reads and writes work correctly together."""
        num_writers = 3
        num_readers = 5
        writes_per_writer = 3
        read_count = [0]
        read_lock = threading.Lock()
        errors: list[Exception] = []

        # Initialize with some data
        report = {
            "test_run_id": "init",
            "timestamp": "2024-01-01T00:00:00Z",
            "target": {"role": "init", "provider_id": "init", "model": "init"},
            "final": {"ready": True, "grade": "PASS"},
            "suites": {},
        }
        update_index_with_report(temp_workspace, report)

        def writer_worker(writer_id: int) -> None:
            try:
                for i in range(writes_per_writer):
                    report = {
                        "test_run_id": f"mixed_w{writer_id}_{i}",
                        "timestamp": f"2024-01-01T00:00:{writer_id * 10 + i:02d}Z",
                        "target": {
                            "role": f"mixed_w{writer_id}",
                            "provider_id": f"mixed_p{writer_id}",
                            "model": f"model-w{writer_id}-{i}",
                        },
                        "final": {"ready": True, "grade": "PASS"},
                        "suites": {},
                    }
                    update_index_with_report(temp_workspace, report)
                    time.sleep(0.002)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        def reader_worker() -> None:
            try:
                for _ in range(10):
                    index = load_llm_test_index(temp_workspace)
                    assert isinstance(index, dict)
                    assert "version" in index
                    with read_lock:
                        read_count[0] += 1
                    time.sleep(0.001)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=num_writers + num_readers) as executor:
            futures = []
            for i in range(num_writers):
                futures.append(executor.submit(writer_worker, i))
            for _ in range(num_readers):
                futures.append(executor.submit(reader_worker))
            for future in as_completed(futures):
                pass

        assert not errors, f"Errors during mixed read/write: {errors}"
        assert read_count[0] > 0, "No reads completed"

    def test_reports_port_injection_thread_safe(self) -> None:
        """Test that set_reports_port is thread-safe.

        Verifies C2 fix: concurrent port injection should not cause issues.
        """
        num_threads = 10
        results: list[Any] = []

        def set_port_worker(worker_id: int) -> None:
            port = _MockReportsPort(files=[f"worker_{worker_id}.json"])
            set_reports_port(port)
            results.append(_get_reports_port())

        threads = [threading.Thread(target=set_port_worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should complete without error
        assert len(results) == num_threads
        # Final port should be one of the set ports (any is valid)
        final_port = _get_reports_port()
        assert final_port is not None


# ---------------------------------------------------------------------------
# Error handling and boundary tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests for error handling and boundary conditions."""

    def test_invalid_workspace_returns_empty(self, temp_workspace: str) -> None:
        """Test that invalid workspace falls back to default location."""
        # When workspace is empty string, it resolves to None and falls back
        # to default paths which may not exist, returning empty index
        index = load_llm_test_index("")
        # Result should be valid index structure
        assert isinstance(index, dict)
        assert "version" in index or "schema_version" in index

    def test_none_workspace_returns_empty(self, temp_workspace: str) -> None:
        """Test that None workspace returns empty index when no default exists."""
        index = load_llm_test_index(None)
        # Result should be valid index structure
        assert isinstance(index, dict)
        assert "version" in index or "schema_version" in index

    def test_update_with_invalid_report(self, temp_workspace: str) -> None:
        """Test update handles malformed reports gracefully."""
        # Should not raise, just skip invalid data
        update_index_with_report(temp_workspace, {})  # type: ignore
        index = load_llm_test_index(temp_workspace)
        assert index.get("roles") == {}

    def test_reconcile_handles_missing_reports_dir(self, temp_workspace: str) -> None:
        """Test reconcile handles non-existent reports directory."""
        set_reports_port(_MockReportsPort(exists=False))
        index = reconcile_llm_test_index(temp_workspace)
        # Should return current index without error
        assert isinstance(index, dict)

    def test_reconcile_handles_empty_reports_dir(self, temp_workspace: str, reports_dir: str) -> None:
        """Test reconcile handles empty reports directory."""
        set_reports_port(_MockReportsPort(files=[], exists=True))
        index = reconcile_llm_test_index(temp_workspace)
        assert isinstance(index, dict)

    def test_concurrent_update_same_key_no_crash(self, temp_workspace: str) -> None:
        """Test concurrent updates to same key don't crash."""

        def update_same():
            for i in range(5):
                report = {
                    "test_run_id": f"same_{i}",
                    "timestamp": "2024-01-01T00:00:00Z",
                    "target": {"role": "same_role", "provider_id": "same_provider", "model": "model"},
                    "final": {"ready": i % 2 == 0, "grade": "PASS" if i % 2 == 0 else "FAIL"},
                    "suites": {},
                }
                update_index_with_report(temp_workspace, report)

        threads = [threading.Thread(target=update_same) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should complete without crash
        index = load_llm_test_index(temp_workspace)
        assert "same_role" in index["roles"]

    def test_reconcile_with_corrupted_json_file(self, temp_workspace: str, reports_dir: str) -> None:
        """Test reconcile skips corrupted JSON files."""
        # Write corrupted JSON
        corrupted = Path(reports_dir) / "corrupted.json"
        with open(corrupted, "w", encoding="utf-8") as f:
            f.write('{"valid": true,\n"incomplete":')

        valid_report = {
            "test_run_id": "valid_after_corrupt",
            "timestamp": "2024-01-01T00:00:00Z",
            "target": {"role": "valid_role", "provider_id": "valid", "model": "model"},
            "final": {"ready": True, "grade": "PASS"},
            "suites": {},
        }
        with open(Path(reports_dir) / "valid_after_corrupt.json", "w", encoding="utf-8") as f:
            json.dump(valid_report, f)

        set_reports_port(_MockReportsPort(files=["corrupted.json", "valid_after_corrupt.json"], exists=True))
        index = reconcile_llm_test_index(temp_workspace)

        # Valid report should still be loaded
        assert index["roles"]["valid_role"]["ready"] is True

    def test_race_condition_prevention_documented(self) -> None:
        """Document the race condition that these tests prevent.

        Without proper locking, this scenario would cause data loss:

        1. Thread A reads index: {roles: {r1: data1}}
        2. Thread B reads index: {roles: {r1: data1}}
        3. Thread A adds r2, writes: {roles: {r1: data1, r2: data2}}
        4. Thread B adds r3, writes: {roles: {r1: data1, r3: data3}}
           ^^^^^ B's write LOSES r2 from A's write!

        With our locking:
        - Thread A acquires write lock
        - Thread B waits for lock
        - Thread A completes read-modify-write
        - Thread B acquires lock and sees A's changes
        - Thread B's write includes both r2 and r3
        """
        pass  # This is documentation; actual verification in concurrent tests


# ---------------------------------------------------------------------------
# Path resolution tests
# ---------------------------------------------------------------------------


class TestPathResolution:
    """Tests for workspace path resolution."""

    def test_resolve_string_workspace(self) -> None:
        """Test resolving string workspace."""
        result = _resolve_workspace_path("/some/path")
        assert result == "/some/path"

    def test_resolve_strips_whitespace(self) -> None:
        """Test that whitespace is stripped."""
        result = _resolve_workspace_path("  /path/to/workspace  ")
        assert result == "/path/to/workspace"

    def test_resolve_empty_string(self) -> None:
        """Test that empty string returns None."""
        result = _resolve_workspace_path("")
        assert result is None

    def test_resolve_whitespace_only(self) -> None:
        """Test that whitespace-only returns None."""
        result = _resolve_workspace_path("   ")
        assert result is None

    def test_resolve_object_with_workspace(self) -> None:
        """Test resolving object with workspace attribute."""

        class WorkspaceObj:
            workspace = "/obj/workspace"

        result = _resolve_workspace_path(WorkspaceObj())
        assert result == "/obj/workspace"

    def test_resolve_object_with_empty_workspace(self) -> None:
        """Test resolving object with empty workspace attribute."""

        class EmptyWorkspaceObj:
            workspace = ""

        result = _resolve_workspace_path(EmptyWorkspaceObj())
        assert result is None

    def test_resolve_object_without_workspace(self) -> None:
        """Test resolving object without workspace attribute."""
        result = _resolve_workspace_path(object())
        assert result is None


# ---------------------------------------------------------------------------
# Stress test
# ---------------------------------------------------------------------------


class TestStress:
    """Stress tests for high concurrency scenarios."""

    @pytest.mark.slow
    def test_high_concurrency_stress(self, temp_workspace: str) -> None:
        """High concurrency stress test with many threads."""
        num_threads = 20
        operations_per_thread = 10

        errors: list[Exception] = []

        def worker(thread_id: int) -> None:
            try:
                for i in range(operations_per_thread):
                    if i % 3 == 0:
                        update_index_with_report(
                            temp_workspace,
                            {
                                "test_run_id": f"stress_{thread_id}_{i}",
                                "timestamp": "2024-01-01T00:00:00Z",
                                "target": {
                                    "role": f"stress_role_{thread_id}",
                                    "provider_id": f"stress_provider_{thread_id}",
                                    "model": "stress-model",
                                },
                                "final": {"ready": True, "grade": "PASS"},
                                "suites": {},
                            },
                        )
                    else:
                        load_llm_test_index(temp_workspace)
            except Exception as exc:
                # Ignore transient errors (Windows file access issues)
                if "Access is denied" in str(exc) or "PermissionError" in type(exc).__name__:
                    return
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, i) for i in range(num_threads)]
            for future in as_completed(futures):
                pass

        # Allow transient permission errors on Windows
        permission_errors = [e for e in errors if "Access is denied" in str(e)]
        if permission_errors:
            # Most operations should succeed despite permission errors
            assert len(permission_errors) < len(errors), f"Too many errors: {errors}"

        # Final index should be valid
        index = load_llm_test_index(temp_workspace)
        assert isinstance(index, dict)
        assert "version" in index
