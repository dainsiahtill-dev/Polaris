"""Tests for polaris.infrastructure.audit.stores.log_store."""

from __future__ import annotations

import json
from pathlib import Path

from polaris.infrastructure.audit.stores.log_store import LogStore


class TestLogStoreInit:
    """Tests for LogStore initialization."""

    def test_init_creates_logs_dir(self, tmp_path: Path) -> None:
        """Happy path: initialization creates logs directory."""
        runtime_root = tmp_path / "runtime"
        store = LogStore(runtime_root)

        assert store.runtime_root == runtime_root.resolve()
        assert store.logs_dir.exists()
        assert store.logs_dir == runtime_root.resolve() / "logs"

    def test_init_with_existing_logs_dir(self, tmp_path: Path) -> None:
        """Initialization succeeds when logs directory already exists."""
        runtime_root = tmp_path / "runtime"
        (runtime_root / "logs").mkdir(parents=True)

        store = LogStore(runtime_root)
        assert store.logs_dir.exists()

    def test_init_with_str_path(self, tmp_path: Path) -> None:
        """Initialization accepts string path."""
        runtime_root = str(tmp_path / "runtime")
        store = LogStore(runtime_root)

        assert isinstance(store.runtime_root, Path)
        assert store.logs_dir.exists()

    def test_init_with_relative_path(self, tmp_path: Path) -> None:
        """Initialization resolves relative paths."""
        import os

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            store = LogStore("./runtime")
            assert store.runtime_root.is_absolute()
            assert store.logs_dir.exists()
        finally:
            os.chdir(original_cwd)


class TestGetTaskLogDir:
    """Tests for _get_task_log_dir."""

    def test_creates_task_directory(self, tmp_path: Path) -> None:
        """Happy path: creates task-specific log directory."""
        store = LogStore(tmp_path)
        task_dir = store._get_task_log_dir("task-123")

        assert task_dir.exists()
        assert task_dir == store.logs_dir / "task-123"

    def test_reuses_existing_task_directory(self, tmp_path: Path) -> None:
        """Reuses existing task directory without error."""
        store = LogStore(tmp_path)
        store._get_task_log_dir("task-123")
        task_dir = store._get_task_log_dir("task-123")

        assert task_dir.exists()

    def test_task_id_with_special_characters(self, tmp_path: Path) -> None:
        """Handles task IDs with special characters."""
        store = LogStore(tmp_path)
        task_dir = store._get_task_log_dir("task/with/slashes")

        assert task_dir.exists()
        assert "task" in str(task_dir)


class TestWriteDirectorLog:
    """Tests for write_director_log."""

    def test_writes_single_entry(self, tmp_path: Path) -> None:
        """Happy path: writes a single log entry."""
        store = LogStore(tmp_path)
        store.write_director_log("Test message", level="INFO")

        log_file = store.logs_dir / "director.log"
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "Test message" in content
        assert "INFO" in content

    def test_appends_multiple_entries(self, tmp_path: Path) -> None:
        """Appends multiple entries to the same log file."""
        store = LogStore(tmp_path)
        store.write_director_log("First message")
        store.write_director_log("Second message")

        log_file = store.logs_dir / "director.log"
        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert "First message" in lines[0]
        assert "Second message" in lines[1]

    def test_default_level_is_info(self, tmp_path: Path) -> None:
        """Default log level is INFO."""
        store = LogStore(tmp_path)
        store.write_director_log("Test message")

        log_file = store.logs_dir / "director.log"
        content = log_file.read_text(encoding="utf-8")
        assert "INFO" in content

    def test_different_levels(self, tmp_path: Path) -> None:
        """Writes entries with different log levels."""
        store = LogStore(tmp_path)
        levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
        for level in levels:
            store.write_director_log(f"Message at {level}", level=level)

        log_file = store.logs_dir / "director.log"
        content = log_file.read_text(encoding="utf-8")
        for level in levels:
            assert level in content

    def test_empty_message(self, tmp_path: Path) -> None:
        """Handles empty message."""
        store = LogStore(tmp_path)
        store.write_director_log("")

        log_file = store.logs_dir / "director.log"
        content = log_file.read_text(encoding="utf-8")
        assert content.strip().endswith("]")

    def test_unicode_message(self, tmp_path: Path) -> None:
        """Handles unicode characters in message."""
        store = LogStore(tmp_path)
        store.write_director_log("Unicode: 你好世界 🌍")

        log_file = store.logs_dir / "director.log"
        content = log_file.read_text(encoding="utf-8")
        assert "你好世界 🌍" in content


class TestWriteTaskLog:
    """Tests for write_task_log."""

    def test_writes_task_entry(self, tmp_path: Path) -> None:
        """Happy path: writes a task-specific log entry."""
        store = LogStore(tmp_path)
        store.write_task_log("task-1", "Task message", level="INFO", source="planner")

        log_file = store.logs_dir / "task-1" / "task.log"
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "Task message" in content
        assert "planner" in content

    def test_task_log_without_source(self, tmp_path: Path) -> None:
        """Writes task log without source."""
        store = LogStore(tmp_path)
        store.write_task_log("task-1", "No source message")

        log_file = store.logs_dir / "task-1" / "task.log"
        content = log_file.read_text(encoding="utf-8")
        assert "No source message" in content
        assert "[]" not in content.split("]")[2]  # No source tag

    def test_multiple_tasks_isolated(self, tmp_path: Path) -> None:
        """Multiple tasks have isolated log files."""
        store = LogStore(tmp_path)
        store.write_task_log("task-a", "Message A")
        store.write_task_log("task-b", "Message B")

        log_a = store.logs_dir / "task-a" / "task.log"
        log_b = store.logs_dir / "task-b" / "task.log"

        assert "Message A" in log_a.read_text(encoding="utf-8")
        assert "Message B" in log_b.read_text(encoding="utf-8")

    def test_task_log_appends(self, tmp_path: Path) -> None:
        """Task log entries are appended."""
        store = LogStore(tmp_path)
        store.write_task_log("task-1", "First")
        store.write_task_log("task-1", "Second")

        log_file = store.logs_dir / "task-1" / "task.log"
        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2


class TestWriteEvent:
    """Tests for write_event."""

    def test_writes_structured_event(self, tmp_path: Path) -> None:
        """Happy path: writes a structured event."""
        store = LogStore(tmp_path)
        path = store.write_event("test_event", task_id="t1", run_id="r1", data={"key": "value"})

        events_file = store.logs_dir / "events.jsonl"
        assert events_file.exists()
        assert path == str(events_file)

        content = events_file.read_text(encoding="utf-8").strip()
        event = json.loads(content)
        assert event["type"] == "test_event"
        assert event["task_id"] == "t1"
        assert event["run_id"] == "r1"
        assert event["data"] == {"key": "value"}

    def test_default_empty_values(self, tmp_path: Path) -> None:
        """Event with default empty values."""
        store = LogStore(tmp_path)
        store.write_event("minimal_event")

        events_file = store.logs_dir / "events.jsonl"
        content = events_file.read_text(encoding="utf-8").strip()
        event = json.loads(content)
        assert event["type"] == "minimal_event"
        assert event["task_id"] == ""
        assert event["run_id"] == ""
        assert event["data"] == {}

    def test_multiple_events_jsonl(self, tmp_path: Path) -> None:
        """Multiple events are written as JSON Lines."""
        store = LogStore(tmp_path)
        store.write_event("event_1")
        store.write_event("event_2")

        events_file = store.logs_dir / "events.jsonl"
        lines = events_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["type"] == "event_1"
        assert json.loads(lines[1])["type"] == "event_2"

    def test_event_with_complex_data(self, tmp_path: Path) -> None:
        """Event with nested complex data."""
        store = LogStore(tmp_path)
        complex_data = {"nested": {"list": [1, 2, 3], "bool": True, "none": None}}
        store.write_event("complex", data=complex_data)

        events_file = store.logs_dir / "events.jsonl"
        event = json.loads(events_file.read_text(encoding="utf-8").strip())
        assert event["data"]["nested"]["list"] == [1, 2, 3]

    def test_event_timestamp_isoformat(self, tmp_path: Path) -> None:
        """Event timestamp is in ISO format."""
        store = LogStore(tmp_path)
        store.write_event("timed_event")

        events_file = store.logs_dir / "events.jsonl"
        event = json.loads(events_file.read_text(encoding="utf-8").strip())
        assert "T" in event["ts"]
        assert event["ts"].endswith("+00:00")


class TestReadDirectorLog:
    """Tests for read_director_log."""

    def test_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        """Returns empty list when log file doesn't exist."""
        store = LogStore(tmp_path)
        entries = store.read_director_log()

        assert entries == []

    def test_reads_written_entries(self, tmp_path: Path) -> None:
        """Reads entries that were written."""
        store = LogStore(tmp_path)
        store.write_director_log("Message 1")
        store.write_director_log("Message 2")

        entries = store.read_director_log()
        assert len(entries) == 2
        assert entries[0]["message"] == "Message 1"
        assert entries[1]["message"] == "Message 2"

    def test_respects_lines_limit(self, tmp_path: Path) -> None:
        """Respects the lines parameter."""
        store = LogStore(tmp_path)
        for i in range(10):
            store.write_director_log(f"Message {i}")

        entries = store.read_director_log(lines=3)
        assert len(entries) == 3
        assert entries[0]["message"] == "Message 7"

    def test_level_filter(self, tmp_path: Path) -> None:
        """Filters by log level."""
        store = LogStore(tmp_path)
        store.write_director_log("Info message", level="INFO")
        store.write_director_log("Error message", level="ERROR")
        store.write_director_log("Another info", level="INFO")

        entries = store.read_director_log(level_filter="ERROR")
        assert len(entries) == 1
        assert entries[0]["message"] == "Error message"

    def test_level_filter_no_matches(self, tmp_path: Path) -> None:
        """Level filter with no matches returns empty list."""
        store = LogStore(tmp_path)
        store.write_director_log("Info message", level="INFO")

        entries = store.read_director_log(level_filter="DEBUG")
        assert entries == []

    def test_lines_zero(self, tmp_path: Path) -> None:
        """lines=0 returns all lines (Python slice behavior: [-0:] == [0:])."""
        store = LogStore(tmp_path)
        store.write_director_log("Message")

        entries = store.read_director_log(lines=0)
        assert len(entries) == 1

    def test_reads_corrupt_lines(self, tmp_path: Path) -> None:
        """Handles corrupt log lines gracefully."""
        store = LogStore(tmp_path)
        log_file = store.logs_dir / "director.log"
        log_file.write_text("corrupt line without brackets\n", encoding="utf-8")

        entries = store.read_director_log()
        assert len(entries) == 1
        assert entries[0]["level"] == "UNKNOWN"
        assert entries[0]["message"] == "corrupt line without brackets"


class TestReadTaskLog:
    """Tests for read_task_log."""

    def test_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        """Returns empty list when task log doesn't exist."""
        store = LogStore(tmp_path)
        entries = store.read_task_log("nonexistent-task")

        assert entries == []

    def test_reads_task_entries(self, tmp_path: Path) -> None:
        """Reads task-specific entries."""
        store = LogStore(tmp_path)
        store.write_task_log("task-1", "Task message")

        entries = store.read_task_log("task-1")
        assert len(entries) == 1
        assert entries[0]["message"] == "Task message"

    def test_task_log_isolated(self, tmp_path: Path) -> None:
        """Task logs are isolated from director logs."""
        store = LogStore(tmp_path)
        store.write_director_log("Director message")
        store.write_task_log("task-1", "Task message")

        director_entries = store.read_director_log()
        task_entries = store.read_task_log("task-1")

        assert len(director_entries) == 1
        assert len(task_entries) == 1
        assert director_entries[0]["message"] == "Director message"
        assert task_entries[0]["message"] == "Task message"

    def test_reads_last_n_lines(self, tmp_path: Path) -> None:
        """Reads last N lines from task log."""
        store = LogStore(tmp_path)
        for i in range(5):
            store.write_task_log("task-1", f"Message {i}")

        entries = store.read_task_log("task-1", lines=2)
        assert len(entries) == 2
        assert entries[0]["message"] == "Message 3"


class TestReadEvents:
    """Tests for read_events."""

    def test_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        """Returns empty list when events file doesn't exist."""
        store = LogStore(tmp_path)
        events = store.read_events()

        assert events == []

    def test_reads_all_events(self, tmp_path: Path) -> None:
        """Reads all events."""
        store = LogStore(tmp_path)
        store.write_event("event_a")
        store.write_event("event_b")

        events = store.read_events()
        assert len(events) == 2
        assert events[0]["type"] == "event_a"
        assert events[1]["type"] == "event_b"

    def test_filter_by_event_type(self, tmp_path: Path) -> None:
        """Filters events by type."""
        store = LogStore(tmp_path)
        store.write_event("type_a")
        store.write_event("type_b")
        store.write_event("type_a")

        events = store.read_events(event_type="type_a")
        assert len(events) == 2
        for event in events:
            assert event["type"] == "type_a"

    def test_filter_by_task_id(self, tmp_path: Path) -> None:
        """Filters events by task ID."""
        store = LogStore(tmp_path)
        store.write_event("ev", task_id="task-1")
        store.write_event("ev", task_id="task-2")

        events = store.read_events(task_id="task-1")
        assert len(events) == 1
        assert events[0]["task_id"] == "task-1"

    def test_filter_by_run_id(self, tmp_path: Path) -> None:
        """Filters events by run ID."""
        store = LogStore(tmp_path)
        store.write_event("ev", run_id="run-1")
        store.write_event("ev", run_id="run-2")

        events = store.read_events(run_id="run-1")
        assert len(events) == 1
        assert events[0]["run_id"] == "run-1"

    def test_respects_limit(self, tmp_path: Path) -> None:
        """Respects the limit parameter."""
        store = LogStore(tmp_path)
        for i in range(10):
            store.write_event(f"event_{i}")

        events = store.read_events(limit=3)
        assert len(events) == 3
        assert events[-1]["type"] == "event_9"

    def test_combined_filters(self, tmp_path: Path) -> None:
        """Applies multiple filters."""
        store = LogStore(tmp_path)
        store.write_event("type_a", task_id="task-1", run_id="run-1")
        store.write_event("type_a", task_id="task-1", run_id="run-2")
        store.write_event("type_b", task_id="task-1", run_id="run-1")

        events = store.read_events(event_type="type_a", task_id="task-1", run_id="run-1")
        assert len(events) == 1
        assert events[0]["type"] == "type_a"

    def test_skips_corrupt_json_lines(self, tmp_path: Path) -> None:
        """Skips corrupt JSON lines."""
        store = LogStore(tmp_path)
        events_file = store.logs_dir / "events.jsonl"
        events_file.write_text(
            '{"type": "valid"}\nnot valid json\n{"type": "also_valid"}\n',
            encoding="utf-8",
        )

        events = store.read_events()
        assert len(events) == 2
        assert events[0]["type"] == "valid"
        assert events[1]["type"] == "also_valid"

    def test_skips_empty_lines(self, tmp_path: Path) -> None:
        """Skips empty lines in events file."""
        store = LogStore(tmp_path)
        events_file = store.logs_dir / "events.jsonl"
        events_file.write_text(
            '{"type": "first"}\n\n\n{"type": "second"}\n',
            encoding="utf-8",
        )

        events = store.read_events()
        assert len(events) == 2


class TestParseLogLine:
    """Tests for _parse_log_line."""

    def test_parses_standard_format(self, tmp_path: Path) -> None:
        """Parses standard log format."""
        store = LogStore(tmp_path)
        line = "[2024-01-01 12:00:00.000] [INFO    ] Test message"
        entry = store._parse_log_line(line)

        assert entry is not None
        assert entry["timestamp"] == "2024-01-01 12:00:00.000"
        assert entry["level"] == "INFO"
        assert entry["message"] == "Test message"

    def test_parses_with_source(self, tmp_path: Path) -> None:
        """Parses log line with source prefix."""
        store = LogStore(tmp_path)
        line = "[2024-01-01 12:00:00.000] [INFO    ] [planner] Test message"
        entry = store._parse_log_line(line)

        assert entry is not None
        assert "[planner] Test message" in entry["message"]

    def test_empty_line_returns_none(self, tmp_path: Path) -> None:
        """Empty line returns None."""
        store = LogStore(tmp_path)
        entry = store._parse_log_line("")

        assert entry is None

    def test_whitespace_only_returns_none(self, tmp_path: Path) -> None:
        """Whitespace-only line returns None."""
        store = LogStore(tmp_path)
        entry = store._parse_log_line("   \n")

        assert entry is None

    def test_corrupt_line_fallback(self, tmp_path: Path) -> None:
        """Corrupt line falls back to UNKNOWN level."""
        store = LogStore(tmp_path)
        entry = store._parse_log_line("totally invalid")

        assert entry is not None
        assert entry["level"] == "UNKNOWN"
        assert entry["message"] == "totally invalid"

    def test_line_with_brackets_but_no_level(self, tmp_path: Path) -> None:
        """Line with timestamp but no proper level."""
        store = LogStore(tmp_path)
        line = "[2024-01-01] something"
        entry = store._parse_log_line(line)

        assert entry is not None
        assert entry["level"] == "UNKNOWN"


class TestExportLogs:
    """Tests for export_logs."""

    def test_export_director_logs(self, tmp_path: Path) -> None:
        """Happy path: exports director logs."""
        store = LogStore(tmp_path)
        store.write_director_log("Message 1")
        store.write_director_log("Message 2")

        output = tmp_path / "export.log"
        result = store.export_logs(str(output))

        assert result == str(output)
        assert output.exists()
        content = output.read_text(encoding="utf-8")
        assert "Message 1" in content
        assert "Message 2" in content

    def test_export_task_logs(self, tmp_path: Path) -> None:
        """Exports task-specific logs."""
        store = LogStore(tmp_path)
        store.write_task_log("task-1", "Task message")

        output = tmp_path / "export.log"
        store.export_logs(str(output), task_id="task-1")

        content = output.read_text(encoding="utf-8")
        assert "Task message" in content

    def test_export_with_since_filter(self, tmp_path: Path) -> None:
        """Exports only logs after since timestamp."""
        store = LogStore(tmp_path)
        store.write_director_log("Old message")

        output = tmp_path / "export.log"
        store.export_logs(str(output), since="9999-01-01")

        content = output.read_text(encoding="utf-8")
        assert content == ""

    def test_export_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Creates parent directories for output file."""
        store = LogStore(tmp_path)
        store.write_director_log("Message")

        output = tmp_path / "nested" / "dir" / "export.log"
        store.export_logs(str(output))

        assert output.exists()

    def test_export_empty_logs(self, tmp_path: Path) -> None:
        """Exports empty logs creates empty file."""
        store = LogStore(tmp_path)

        output = tmp_path / "export.log"
        store.export_logs(str(output))

        assert output.exists()
        assert output.read_text(encoding="utf-8") == ""
