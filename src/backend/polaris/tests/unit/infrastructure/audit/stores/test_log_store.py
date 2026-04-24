"""Tests for polaris.infrastructure.audit.stores.log_store."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from polaris.infrastructure.audit.stores.log_store import LogStore


class TestLogStore:
    def test_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LogStore(tmpdir)
            assert store.runtime_root == Path(tmpdir).resolve()
            assert store.logs_dir.exists()

    def test_get_task_log_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LogStore(tmpdir)
            log_dir = store._get_task_log_dir("task-123")
            assert log_dir.exists()
            assert "task-123" in str(log_dir)

    def test_write_director_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LogStore(tmpdir)
            store.write_director_log("Test message", "INFO")

            log_file = store.logs_dir / "director.log"
            content = log_file.read_text()
            assert "Test message" in content
            assert "[INFO    ]" in content

    def test_write_director_log_with_level(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LogStore(tmpdir)
            store.write_director_log("Error occurred", "ERROR")

            log_file = store.logs_dir / "director.log"
            content = log_file.read_text()
            assert "[ERROR   ]" in content

    def test_write_task_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LogStore(tmpdir)
            store.write_task_log("task-abc", "Task started", "INFO", "planner")

            log_file = store.logs_dir / "task-abc" / "task.log"
            content = log_file.read_text()
            assert "Task started" in content
            assert "[planner]" in content

    def test_write_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LogStore(tmpdir)
            path = store.write_event(
                event_type="task.created",
                task_id="task-xyz",
                run_id="run-1",
                data={"key": "value"},
            )

            assert Path(path).exists()
            content = Path(path).read_text()
            event = json.loads(content.strip())
            assert event["type"] == "task.created"
            assert event["task_id"] == "task-xyz"

    def test_read_director_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LogStore(tmpdir)
            store.write_director_log("Message 1", "INFO")
            store.write_director_log("Message 2", "WARNING")

            entries = store.read_director_log(lines=10)
            assert len(entries) >= 1
            assert all("message" in entry for entry in entries)

    def test_read_director_log_with_level_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LogStore(tmpdir)
            store.write_director_log("Info message", "INFO")
            store.write_director_log("Warn message", "WARNING")

            entries = store.read_director_log(lines=10, level_filter="WARNING")
            assert all(entry.get("level") == "WARNING" for entry in entries)

    def test_read_director_log_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LogStore(tmpdir)
            entries = store.read_director_log()
            assert entries == []

    def test_read_task_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LogStore(tmpdir)
            store.write_task_log("task-read", "Step 1", "INFO")
            store.write_task_log("task-read", "Step 2", "INFO")

            entries = store.read_task_log("task-read")
            assert len(entries) >= 1

    def test_read_task_log_nonexistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LogStore(tmpdir)
            entries = store.read_task_log("nonexistent-task")
            assert entries == []

    def test_read_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LogStore(tmpdir)
            store.write_event("event1", "task-1")
            store.write_event("event2", "task-2")

            events = store.read_events(limit=10)
            assert len(events) == 2

    def test_read_events_filter_by_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LogStore(tmpdir)
            store.write_event("type-a", "task-1")
            store.write_event("type-b", "task-1")
            store.write_event("type-a", "task-1")

            events = store.read_events(event_type="type-a")
            assert all(e["type"] == "type-a" for e in events)

    def test_read_events_filter_by_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LogStore(tmpdir)
            store.write_event("event", "task-x")
            store.write_event("event", "task-y")

            events = store.read_events(task_id="task-x")
            assert all(e["task_id"] == "task-x" for e in events)

    def test_parse_log_line_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LogStore(tmpdir)
            line = "[2026-04-24 12:00:00.000] [INFO    ] Test message"
            parsed = store._parse_log_line(line)

            assert parsed is not None
            assert parsed["timestamp"] == "2026-04-24 12:00:00.000"
            assert parsed["level"] == "INFO"
            assert parsed["message"] == "Test message"

    def test_parse_log_line_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LogStore(tmpdir)
            parsed = store._parse_log_line("not a proper log line")

            assert parsed is not None
            assert parsed["level"] == "UNKNOWN"
            assert "not a proper log line" in parsed["message"]

    def test_export_logs_director(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LogStore(tmpdir)
            store.write_director_log("Export test", "INFO")

            export_path = tmpdir / "exported.log"
            result = store.export_logs(str(export_path))

            assert Path(result).exists()
            content = Path(result).read_text()
            assert "Export test" in content

    def test_export_logs_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LogStore(tmpdir)
            store.write_task_log("export-task", "Task export content", "INFO")

            export_path = tmpdir / "task_export.log"
            result = store.export_logs(str(export_path), task_id="export-task")

            assert Path(result).exists()
            content = Path(result).read_text()
            assert "Task export content" in content
