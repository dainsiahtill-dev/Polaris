r"""Log file persistence for Director v2.

Stores structured logs for debugging and audit.
Maintains compatibility with original Director log format.

CRITICAL: All logs are stored OUTSIDE the workspace to avoid pollution.
Storage locations (in priority order):
1. Ramdisk (X:\) if available and POLARIS_STATE_TO_RAMDISK is enabled
2. System cache directory (%LOCALAPPDATA%\Polaris\cache or ~/.cache/polaris)
3. Explicit POLARIS_RUNTIME_ROOT

Path structure: {runtime_base}/<metadata_dir>/projects/{workspace_key}/runtime/logs/
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LogStore:
    """File-based log storage for Director v2.

    Log files are stored OUTSIDE workspace to avoid pollution.
    Compatible with original Director log format.
    """

    def __init__(self, runtime_root: str | Path) -> None:
        self.runtime_root = Path(runtime_root).resolve()
        self.logs_dir = self.runtime_root / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _get_task_log_dir(self, task_id: str) -> Path:
        """Get log directory for a task."""
        path = self.logs_dir / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_director_log(self, message: str, level: str = "INFO") -> None:
        """Write entry to main director log.

        Args:
            message: Log message
            level: Log level (DEBUG, INFO, WARNING, ERROR)
        """
        log_file = self.logs_dir / "director.log"
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] [{level:8}] {message}\n"

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line)

    def write_task_log(
        self,
        task_id: str,
        message: str,
        level: str = "INFO",
        source: str = "",
    ) -> None:
        """Write entry to task-specific log.

        Args:
            task_id: Task identifier
            message: Log message
            level: Log level
            source: Log source (e.g., 'planner', 'executor', 'qa')
        """
        task_dir = self._get_task_log_dir(task_id)
        log_file = task_dir / "task.log"

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        source_str = f"[{source}] " if source else ""
        line = f"[{timestamp}] [{level:8}] {source_str}{message}\n"

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line)

    def write_event(
        self,
        event_type: str,
        task_id: str = "",
        run_id: str = "",
        data: dict[str, Any] | None = None,
    ) -> str:
        """Write structured event (JSONL format).

        Args:
            event_type: Type of event
            task_id: Optional task identifier
            run_id: Optional run identifier
            data: Event data

        Returns:
            Path to events file
        """
        events_file = self.logs_dir / "events.jsonl"

        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "task_id": task_id or "",
            "run_id": run_id or "",
            "data": data or {},
        }

        with open(events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")

        return str(events_file)

    def read_director_log(
        self,
        lines: int = 100,
        level_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read main director log.

        Args:
            lines: Number of lines to read (from end)
            level_filter: Filter by log level

        Returns:
            List of parsed log entries
        """
        log_file = self.logs_dir / "director.log"
        if not log_file.exists():
            return []

        # Read all lines
        with open(log_file, encoding="utf-8") as f:
            all_lines = f.readlines()

        # Parse last N lines
        entries = []
        for line in all_lines[-lines:]:
            entry = self._parse_log_line(line)
            if entry:
                if level_filter and entry.get("level") != level_filter:
                    continue
                entries.append(entry)

        return entries

    def read_task_log(
        self,
        task_id: str,
        lines: int = 100,
    ) -> list[dict[str, Any]]:
        """Read task-specific log.

        Args:
            task_id: Task identifier
            lines: Number of lines to read

        Returns:
            List of parsed log entries
        """
        task_dir = self._get_task_log_dir(task_id)
        log_file = task_dir / "task.log"

        if not log_file.exists():
            return []

        with open(log_file, encoding="utf-8") as f:
            all_lines = f.readlines()

        entries = []
        for line in all_lines[-lines:]:
            entry = self._parse_log_line(line)
            if entry:
                entries.append(entry)

        return entries

    def read_events(
        self,
        event_type: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read structured events.

        Args:
            event_type: Filter by event type
            task_id: Filter by task ID
            run_id: Filter by run ID
            limit: Maximum number of events

        Returns:
            List of events
        """
        events_file = self.logs_dir / "events.jsonl"
        if not events_file.exists():
            return []

        events = []
        with open(events_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    # Apply filters
                    if event_type and event.get("type") != event_type:
                        continue
                    if task_id and event.get("task_id") != task_id:
                        continue
                    if run_id and event.get("run_id") != run_id:
                        continue
                    events.append(event)
                except json.JSONDecodeError:
                    continue

        # Return last N events
        return events[-limit:]

    def _parse_log_line(self, line: str) -> dict[str, Any] | None:
        """Parse a log line into structured format.

        Format: [timestamp] [LEVEL] message
        """
        line = line.strip()
        if not line:
            return None

        try:
            # Extract timestamp
            if line.startswith("[") and "]" in line:
                timestamp_end = line.index("]")
                timestamp = line[1:timestamp_end]
                rest = line[timestamp_end + 1 :].strip()

                # Extract level
                if rest.startswith("[") and "]" in rest:
                    level_end = rest.index("]")
                    level = rest[1:level_end].strip()
                    message = rest[level_end + 1 :].strip()

                    return {
                        "timestamp": timestamp,
                        "level": level,
                        "message": message,
                    }
        except (ValueError, IndexError):
            pass

        # Fallback: return raw line
        return {"timestamp": "", "level": "UNKNOWN", "message": line}

    def export_logs(
        self,
        output_path: str,
        task_id: str | None = None,
        since: str | None = None,
    ) -> str:
        """Export logs to file.

        Args:
            output_path: Output file path
            task_id: Optional task filter
            since: Optional timestamp filter (ISO format)

        Returns:
            Path to exported file
        """
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        with open(output, "w", encoding="utf-8") as f:
            if task_id:
                # Export task logs
                entries = self.read_task_log(task_id, lines=10000)
                for entry in entries:
                    if since and entry.get("timestamp", "") < since:
                        continue
                    f.write(f"[{entry['timestamp']}] [{entry['level']}] {entry['message']}\n")
            else:
                # Export director logs
                entries = self.read_director_log(lines=10000)
                for entry in entries:
                    if since and entry.get("timestamp", "") < since:
                        continue
                    f.write(f"[{entry['timestamp']}] [{entry['level']}] {entry['message']}\n")

        return str(output)
