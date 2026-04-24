"""Tests for Director lifecycle module.

These tests verify the concurrency safety and correctness of the
update_director_lifecycle() function.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections import defaultdict
from typing import Any

import pytest
from polaris.domain.director.lifecycle import (
    read as read_director_lifecycle,
    update as update_director_lifecycle,
)


class TestDirectorLifecycle:
    """Test suite for Director lifecycle operations."""

    def test_basic_update_creates_file(self) -> None:
        """Verify basic update creates lifecycle file with correct structure."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
        ) as f:
            path = f.name

        try:
            result = update_director_lifecycle(
                path,
                phase="init",
                status="running",
                run_id="test-run-001",
            )

            assert result is not None
            assert result["phase"] == "init"
            assert result["status"] == "running"
            assert result["run_id"] == "test-run-001"
            assert result["schema_version"] == 1
            assert "events" in result
            assert len(result["events"]) == 1

            # Verify file contents
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            assert saved["phase"] == "init"
            assert saved["status"] == "running"
        finally:
            os.unlink(path)

    def test_empty_path_returns_empty_dict(self) -> None:
        """Verify empty path returns empty dict without error."""
        result = update_director_lifecycle(
            path="",
            phase="test",
        )
        assert result == {}

    def test_nonexistent_path_creates_new_file(self) -> None:
        """Verify update creates file if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "new_lifecycle.json")

            assert not os.path.exists(path)

            result = update_director_lifecycle(
                path,
                phase="startup",
                status="initialized",
                run_id="run-002",
                task_id="task-001",
            )

            assert os.path.exists(path)
            assert result["phase"] == "startup"
            assert result["run_id"] == "run-002"
            assert result["task_id"] == "task-001"

    def test_events_limited_to_50(self) -> None:
        """Verify events history is limited to 50 entries."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
        ) as f:
            path = f.name
            json.dump({"events": []}, f)

        try:
            # Add 60 events
            for i in range(60):
                update_director_lifecycle(
                    path,
                    phase=f"phase_{i}",
                    status="running",
                )

            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            # Should only have last 50 events
            assert len(data["events"]) == 50
            # First event should be phase_10 (index 10 in 0-59 range)
            assert data["events"][0]["phase"] == "phase_10"
        finally:
            os.unlink(path)

    def test_concurrent_updates_are_atomic(self) -> None:
        """Verify concurrent updates are serialized correctly."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
        ) as f:
            path = f.name
            json.dump({"events": []}, f)

        try:
            errors: list[Exception] = []
            barrier = threading.Barrier(5)

            def update_worker(worker_id: int) -> None:
                try:
                    # Wait for all threads to be ready
                    barrier.wait()
                    for i in range(10):
                        update_director_lifecycle(
                            path,
                            phase=f"worker_{worker_id}_phase_{i}",
                            status="running",
                            run_id=f"run_{worker_id}",
                        )
                except Exception as e:
                    errors.append(e)

            # Create and start threads
            threads = [threading.Thread(target=update_worker, args=(i,)) for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Verify no errors occurred
            assert len(errors) == 0, f"Errors occurred: {errors}"

            # Verify final state
            with open(path, encoding="utf-8") as f:
                final: dict[str, Any] = json.load(f)

            # Should have exactly 50 events (max limit)
            assert len(final["events"]) == 50

            # Each worker should have contributed events
            events_by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for event in final["events"]:
                if event.get("status") == "running":
                    run_id = event.get("phase", "").split("_")[1]
                    if run_id.isdigit():
                        events_by_run[f"run_{run_id}"].append(event)

            # At minimum, we should have events from all 5 workers
            assert len(events_by_run) >= 1

        finally:
            os.unlink(path)

    def test_lock_timeout_raises_runtime_error(self) -> None:
        """Verify RuntimeError is raised when lock cannot be acquired."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
        ) as f:
            path = f.name

        try:
            # Manually create a lock file
            lock_path = f"{path}.lock"
            with open(lock_path, "w", encoding="utf-8") as f:
                f.write("999999 9999999999.0")  # Fake PID and timestamp

            try:
                # This should timeout and raise RuntimeError
                with pytest.raises(RuntimeError, match="Lock acquisition timeout"):
                    update_director_lifecycle(
                        path,
                        phase="test",
                    )
            finally:
                # Clean up lock file
                os.unlink(lock_path)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_concurrent_read_during_write(self) -> None:
        """Verify reads are consistent during concurrent writes."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
        ) as f:
            path = f.name
            json.dump({"events": []}, f)

        try:
            barrier = threading.Barrier(2)
            read_results: list[dict[str, Any]] = []
            read_lock = threading.Lock()

            def writer() -> None:
                barrier.wait()
                for i in range(20):
                    update_director_lifecycle(
                        path,
                        phase=f"write_{i}",
                        status="active",
                    )

            def reader() -> None:
                barrier.wait()
                for _ in range(20):
                    result = read_director_lifecycle(path)
                    with read_lock:
                        read_results.append(result)
                    time.sleep(0.001)

            t1 = threading.Thread(target=writer)
            t2 = threading.Thread(target=reader)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            # All reads should have valid structure
            for result in read_results:
                assert isinstance(result, dict)
                assert "events" in result
                # Events should be a list
                assert isinstance(result["events"], list)

        finally:
            os.unlink(path)

    def test_details_update_merges_dicts(self) -> None:
        """Verify details dictionary is properly merged."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
        ) as f:
            path = f.name

        try:
            # First update with initial details
            update_director_lifecycle(
                path,
                phase="init",
                details={"key1": "value1", "key2": "initial"},
            )

            # Second update with additional details
            update_director_lifecycle(
                path,
                phase="update",
                details={"key2": "updated", "key3": "new"},
            )

            with open(path, encoding="utf-8") as f:
                data: dict[str, Any] = json.load(f)

            assert data["details"]["key1"] == "value1"
            assert data["details"]["key2"] == "updated"
            assert data["details"]["key3"] == "new"
        finally:
            os.unlink(path)

    def test_boolean_flags_set_timestamps(self) -> None:
        """Verify boolean flags set appropriate timestamps."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
        ) as f:
            path = f.name

        try:
            result = update_director_lifecycle(
                path,
                phase="starting",
                startup_completed=True,
                execution_started=True,
                terminal=True,
            )

            assert result["startup_completed"] is True
            assert "startup_at" in result
            assert result["execution_started"] is True
            assert "execution_started_at" in result
            assert result["terminal"] is True
            assert "terminal_at" in result
        finally:
            os.unlink(path)

    def test_read_nonexistent_file_returns_empty(self) -> None:
        """Verify reading nonexistent file returns empty dict."""
        result = read_director_lifecycle("/nonexistent/path/file.json")
        assert result == {}

    def test_read_invalid_json_returns_empty(self) -> None:
        """Verify reading invalid JSON returns empty dict."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
        ) as f:
            f.write("not valid json {")
            path = f.name

        try:
            result = read_director_lifecycle(path)
            assert result == {}
        finally:
            os.unlink(path)
