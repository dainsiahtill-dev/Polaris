"""Omniscient Audit Query Engine - Partition-aware audit event queries.

This module provides efficient querying over partitioned audit logs stored in:
    {runtime_root}/audit/{workspace}/{date}/{channel_prefix}.{event_type}.jsonl

Features:
    - Partition discovery and enumeration
    - Efficient time-range queries (partition pruning by date)
    - Indexed lookups by trace_id, run_id, task_id (O(1) via in-memory index)
    - Pagination support
    - Partition statistics

Design principles:
    - NOT O(n) full table scans: uses date-based partition pruning
    - UTF-8 encoding for all text operations
    - Compatible with atomic JSONL appends from kernel_runtime_adapter
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

# Partition filename pattern: {prefix}.{event_type}.jsonl
_PARTITION_FILE_RE = re.compile(r"^(?P<prefix>[^.]+)\.(?P<event_type>[^.]+)\.jsonl$")

# Date directory pattern: YYYY-MM-DD
_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ISO timestamp pattern (with Z suffix)
_ISO_TS_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"T(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\.(?P<frac>\d+))?(?P<tz>[Z+-]\d{2}:?\d{2})?$"
)

# =============================================================================
# Data Classes
# =============================================================================


@dataclass(frozen=True)
class PartitionStats:
    """Immutable statistics for a single partition."""

    workspace: str
    date: str  # YYYY-MM-DD
    event_type: str
    file_path: str
    file_size_bytes: int
    event_count: int


@dataclass(frozen=True)
class QueryResult:
    """Paginated query result container."""

    events: tuple[dict[str, Any], ...]
    total: int
    offset: int
    limit: int
    has_more: bool
    partitions_queried: int


# =============================================================================
# In-Memory Index
# =============================================================================


class AuditEventIndex:
    """Thread-safe in-memory index for O(1) event lookups.

    Maintains three indexes:
        - trace_id -> list[(file_path, line_number)]
        - run_id -> list[(file_path, line_number)]
        - task_id -> list[(file_path, line_number)]

    Index is built lazily on first indexed lookup.
    """

    __slots__ = (
        "_built",
        "_by_run",
        "_by_task",
        "_by_trace",
        "_file_to_count",
        "_lock",
    )

    def __init__(self) -> None:
        self._by_trace: dict[str, list[tuple[str, int]]] = {}
        self._by_run: dict[str, list[tuple[str, int]]] = {}
        self._by_task: dict[str, list[tuple[str, int]]] = {}
        self._file_to_count: dict[str, int] = {}
        self._lock = RLock()
        self._built = False

    def add_entry(
        self,
        file_path: str,
        line_number: int,
        trace_id: str | None,
        run_id: str | None,
        task_id: str | None,
    ) -> None:
        """Add an entry to the index."""
        with self._lock:
            if trace_id:
                self._by_trace.setdefault(trace_id, []).append((file_path, line_number))
            if run_id:
                self._by_run.setdefault(run_id, []).append((file_path, line_number))
            if task_id:
                self._by_task.setdefault(task_id, []).append((file_path, line_number))

    def increment_file_count(self, file_path: str) -> None:
        """Increment the event count for a file."""
        with self._lock:
            self._file_to_count[file_path] = self._file_to_count.get(file_path, 0) + 1

    def lookup_trace(self, trace_id: str) -> list[tuple[str, int]]:
        """Look up events by trace_id."""
        with self._lock:
            return list(self._by_trace.get(trace_id, []))

    def lookup_run(self, run_id: str) -> list[tuple[str, int]]:
        """Look up events by run_id."""
        with self._lock:
            return list(self._by_run.get(run_id, []))

    def lookup_task(self, task_id: str) -> list[tuple[str, int]]:
        """Look up events by task_id."""
        with self._lock:
            return list(self._by_task.get(task_id, []))

    def get_file_counts(self) -> dict[str, int]:
        """Get event counts per file."""
        with self._lock:
            return dict(self._file_to_count)

    @property
    def is_built(self) -> bool:
        """Check if index has been built."""
        with self._lock:
            return self._built

    def mark_built(self) -> None:
        """Mark the index as fully built."""
        with self._lock:
            self._built = True


# =============================================================================
# Audit Query Engine
# =============================================================================


class AuditQueryEngine:
    """High-performance query engine for partitioned audit logs.

    Partition structure:
        {runtime_root}/audit/{workspace}/{date}/{prefix}.{event_type}.jsonl

    Example:
        {runtime_root}/audit/default/2026-04-04/audit.LLM_CALL.jsonl
        {runtime_root}/audit/myworkspace/2026-04-04/audit.tool_execution.jsonl

    Query strategies:
        - Time-range: Partition pruning by date directory
        - trace_id/run_id/task_id: O(1) in-memory index lookup
        - Full scan: Only when no better strategy available

    Thread-safe for concurrent reads. Index building is serialized.

    Usage:
        engine = AuditQueryEngine(runtime_root=Path("/path/to/runtime"))
        engine.build_index()

        # Time range query
        result = engine.query_by_time_range(
            start_time=datetime(2026, 4, 1),
            end_time=datetime(2026, 4, 4),
            workspace="myworkspace",
            limit=100,
        )

        # Direct ID lookup
        events = engine.query_by_trace_id("abc123")

        # Pagination
        page1 = engine.query_all(limit=50, offset=0)
        page2 = engine.query_all(limit=50, offset=50)
    """

    def __init__(
        self,
        runtime_root: Path,
        channel_prefix: str = "audit",
    ) -> None:
        """Initialize the query engine.

        Args:
            runtime_root: Root path containing audit partitions.
            channel_prefix: Prefix used in partition filenames (default: "audit").
        """
        self._runtime_root = Path(runtime_root).resolve()
        self._channel_prefix = channel_prefix
        self._index = AuditEventIndex()
        self._index_lock = RLock()
        self._index_building = False

    # -------------------------------------------------------------------------
    # Partition Discovery
    # -------------------------------------------------------------------------

    def discover_partitions(
        self,
        workspace: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[PartitionStats]:
        """Discover all partitions matching criteria.

        Partition pruning: Only reads directory structure, NOT file contents.
        This is O(directories) not O(files).

        Args:
            workspace: Filter by workspace name (None = all workspaces).
            start_date: Filter partitions on/after this date (inclusive).
            end_date: Filter partitions on/before this date (inclusive).

        Returns:
            List of PartitionStats for matching partitions.
        """
        audit_root = self._runtime_root / "audit"
        if not audit_root.exists():
            return []

        partitions: list[PartitionStats] = []

        # Determine which workspace dirs to scan
        if workspace is not None:
            ws_path = audit_root / workspace
            if not ws_path.exists():
                return []
            workspace_dirs = [ws_path]
        else:
            workspace_dirs = [d for d in audit_root.iterdir() if d.is_dir() and not d.name.startswith(".")]

        for ws_dir in workspace_dirs:
            for date_entry in ws_dir.iterdir():
                if not date_entry.is_dir():
                    continue
                if not _DATE_DIR_RE.match(date_entry.name):
                    continue

                # Date filtering
                if start_date is not None:
                    try:
                        part_date = datetime.strptime(date_entry.name, "%Y-%m-%d").date()
                        if part_date < start_date.date():
                            continue
                    except ValueError:
                        continue

                if end_date is not None:
                    try:
                        part_date = datetime.strptime(date_entry.name, "%Y-%m-%d").date()
                        if part_date > end_date.date():
                            continue
                    except ValueError:
                        continue

                # Scan JSONL files in this date directory
                for jsonl_file in date_entry.iterdir():
                    if not jsonl_file.is_file():
                        continue

                    match = _PARTITION_FILE_RE.match(jsonl_file.name)
                    if not match:
                        continue

                    file_prefix = match.group("prefix")
                    if file_prefix != self._channel_prefix:
                        continue

                    event_type = match.group("event_type")
                    try:
                        file_size = jsonl_file.stat().st_size
                    except OSError:
                        file_size = 0

                    partitions.append(
                        PartitionStats(
                            workspace=ws_dir.name,
                            date=date_entry.name,
                            event_type=event_type,
                            file_path=str(jsonl_file),
                            file_size_bytes=file_size,
                            event_count=0,  # Counted during index build
                        )
                    )

        return partitions

    # -------------------------------------------------------------------------
    # Index Building
    # -------------------------------------------------------------------------

    def build_index(self, *, force: bool = False) -> int:
        """Build in-memory index for indexed lookups.

        Scans all partitions and builds O(1) indexes for
        trace_id, run_id, and task_id lookups.

        Args:
            force: Rebuild even if already built.

        Returns:
            Total number of events indexed.
        """
        with self._index_lock:
            if self._index.is_built and not force:
                return 0

            if self._index_building:
                # Another thread is building, wait briefly
                import time

                for _ in range(50):  # Wait up to 5 seconds
                    time.sleep(0.1)
                    if self._index.is_built:
                        return 0
                raise RuntimeError("Index build timed out")

            self._index_building = True

        try:
            total_events = 0
            partitions = self.discover_partitions()

            for part in partitions:
                count = self._index_partition(part)
                total_events += count

            with self._index_lock:
                self._index.mark_built()

            logger.info(
                "[audit_query] Indexed %d events across %d partitions",
                total_events,
                len(partitions),
            )
            return total_events

        finally:
            with self._index_lock:
                self._index_building = False

    def _index_partition(self, partition: PartitionStats) -> int:
        """Index events in a single partition file.

        Args:
            partition: Partition to index.

        Returns:
            Number of events indexed.
        """
        count = 0
        try:
            with open(partition.file_path, encoding="utf-8", newline="\n") as f:
                for line_num, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Extract ID fields
                    context = event.get("context") or {}
                    task = event.get("task") or {}

                    trace_id: str | None = None
                    if isinstance(context, dict):
                        trace_id = str(context.get("trace_id") or "") or None

                    run_id: str | None = None
                    if isinstance(task, dict):
                        run_id = str(task.get("run_id") or "") or None

                    task_id: str | None = None
                    if isinstance(task, dict):
                        task_id = str(task.get("task_id") or "") or None

                    # Add to index
                    self._index.add_entry(
                        file_path=partition.file_path,
                        line_number=line_num,
                        trace_id=trace_id,
                        run_id=run_id,
                        task_id=task_id,
                    )
                    self._index.increment_file_count(partition.file_path)
                    count += 1

        except OSError as exc:
            logger.debug(
                "[audit_query] Failed to index partition %s: %s",
                partition.file_path,
                exc,
            )

        return count

    # -------------------------------------------------------------------------
    # Query Methods
    # -------------------------------------------------------------------------

    def query_by_time_range(
        self,
        start_time: datetime,
        end_time: datetime,
        *,
        workspace: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> QueryResult:
        """Query events within a time range using partition pruning.

        Efficient: Only scans partitions whose dates overlap the range.
        Does NOT build the full index.

        Args:
            start_time: Start of time range (inclusive, UTC).
            end_time: End of time range (inclusive, UTC).
            workspace: Filter by workspace (None = all).
            event_type: Filter by event type (None = all).
            limit: Maximum events to return.
            offset: Number of events to skip for pagination.

        Returns:
            QueryResult with matching events sorted by timestamp.
        """
        # Ensure UTC
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        # Discover partitions in date range
        partitions = self.discover_partitions(
            workspace=workspace,
            start_date=start_time,
            end_date=end_time,
        )

        if event_type:
            partitions = [p for p in partitions if p.event_type == event_type]

        # Read events from matching partitions
        all_events: list[tuple[datetime, dict[str, Any]]] = []
        for part in partitions:
            events = self._read_partition_events(part, start_time, end_time)
            all_events.extend(events)

        # Sort by timestamp
        all_events.sort(key=lambda x: x[0])

        # Apply pagination
        total = len(all_events)
        start_idx = min(offset, total)
        end_idx = min(offset + limit, total)
        page_events = tuple(e[1] for e in all_events[start_idx:end_idx])

        return QueryResult(
            events=page_events,
            total=total,
            offset=offset,
            limit=limit,
            has_more=end_idx < total,
            partitions_queried=len(partitions),
        )

    def query_by_trace_id(
        self,
        trace_id: str,
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Query events by trace_id using O(1) index lookup.

        Requires build_index() to be called first.
        Automatically builds index if not yet built.

        Args:
            trace_id: Trace ID to search for.
            limit: Maximum events to return.

        Returns:
            List of events with matching trace_id.
        """
        self._ensure_index_built()

        locations = self._index.lookup_trace(trace_id)
        return self._fetch_events_at_locations(locations, limit)

    def query_by_run_id(
        self,
        run_id: str,
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Query events by run_id using O(1) index lookup.

        Requires build_index() to be called first.
        Automatically builds index if not yet built.

        Args:
            run_id: Run ID to search for.
            limit: Maximum events to return.

        Returns:
            List of events with matching run_id.
        """
        self._ensure_index_built()

        locations = self._index.lookup_run(run_id)
        return self._fetch_events_at_locations(locations, limit)

    def query_by_task_id(
        self,
        task_id: str,
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Query events by task_id using O(1) index lookup.

        Requires build_index() to be called first.
        Automatically builds index if not yet built.

        Args:
            task_id: Task ID to search for.
            limit: Maximum events to return.

        Returns:
            List of events with matching task_id.
        """
        self._ensure_index_built()

        locations = self._index.lookup_task(task_id)
        return self._fetch_events_at_locations(locations, limit)

    def query_all(
        self,
        *,
        workspace: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> QueryResult:
        """Query all events with optional filters (no time range).

        This is a full scan but supports pagination.

        Args:
            workspace: Filter by workspace (None = all).
            event_type: Filter by event type (None = all).
            limit: Maximum events to return.
            offset: Number of events to skip.

        Returns:
            QueryResult with matching events.
        """
        partitions = self.discover_partitions(workspace=workspace)
        if event_type:
            partitions = [p for p in partitions if p.event_type == event_type]

        all_events: list[tuple[datetime, dict[str, Any]]] = []
        for part in partitions:
            events = self._read_all_partition_events(part)
            all_events.extend(events)

        all_events.sort(key=lambda x: x[0])

        total = len(all_events)
        start_idx = min(offset, total)
        end_idx = min(offset + limit, total)
        page_events = tuple(e[1] for e in all_events[start_idx:end_idx])

        return QueryResult(
            events=page_events,
            total=total,
            offset=offset,
            limit=limit,
            has_more=end_idx < total,
            partitions_queried=len(partitions),
        )

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    def _ensure_index_built(self) -> None:
        """Ensure the index is built, building if necessary."""
        if not self._index.is_built:
            self.build_index()

    def _read_partition_events(
        self,
        partition: PartitionStats,
        start_time: datetime,
        end_time: datetime,
    ) -> list[tuple[datetime, dict[str, Any]]]:
        """Read events from a partition within a time range.

        Args:
            partition: Partition to read.
            start_time: Start of time range (inclusive).
            end_time: End of time range (inclusive).

        Returns:
            List of (timestamp, event) tuples.
        """
        events: list[tuple[datetime, dict[str, Any]]] = []
        try:
            with open(partition.file_path, encoding="utf-8", newline="\n") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    event_time = self._extract_timestamp(event)
                    if event_time is None:
                        continue

                    if start_time <= event_time <= end_time:
                        events.append((event_time, event))

        except OSError as exc:
            logger.debug(
                "[audit_query] Failed to read partition %s: %s",
                partition.file_path,
                exc,
            )

        return events

    def _read_all_partition_events(
        self,
        partition: PartitionStats,
    ) -> list[tuple[datetime, dict[str, Any]]]:
        """Read all events from a partition.

        Args:
            partition: Partition to read.

        Returns:
            List of (timestamp, event) tuples.
        """
        events: list[tuple[datetime, dict[str, Any]]] = []
        try:
            with open(partition.file_path, encoding="utf-8", newline="\n") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    event_time = self._extract_timestamp(event)
                    if event_time is None:
                        # Use epoch for events without timestamp
                        event_time = datetime.min.replace(tzinfo=timezone.utc)

                    events.append((event_time, event))

        except OSError as exc:
            logger.debug(
                "[audit_query] Failed to read partition %s: %s",
                partition.file_path,
                exc,
            )

        return events

    def _fetch_events_at_locations(
        self,
        locations: list[tuple[str, int]],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Fetch events at specific (file_path, line_number) locations.

        Args:
            locations: List of (file_path, line_number) tuples.
            limit: Maximum events to return.

        Returns:
            List of events at the specified locations.
        """
        # Group by file for efficient reading
        file_lines: dict[str, list[int]] = {}
        for file_path, line_num in locations:
            if file_path not in file_lines:
                file_lines[file_path] = []
            file_lines[file_path].append(line_num)

        events: list[dict[str, Any]] = []
        for file_path, line_nums in file_lines.items():
            file_events = self._read_specific_lines(file_path, line_nums)
            events.extend(file_events)
            if len(events) >= limit:
                break

        return events[:limit]

    def _read_specific_lines(
        self,
        file_path: str,
        line_numbers: list[int],
    ) -> list[dict[str, Any]]:
        """Read specific lines from a file.

        Args:
            file_path: Path to the file.
            line_numbers: Line numbers to read (1-indexed).

        Returns:
            List of parsed JSON events.
        """
        if not line_numbers:
            return []

        # Build a set for O(1) lookup
        needed = set(line_numbers)
        max_line = max(line_numbers)

        events: list[dict[str, Any]] = []
        try:
            with open(file_path, encoding="utf-8", newline="\n") as f:
                for current_line, line in enumerate(f, start=1):
                    if current_line > max_line:
                        break
                    if current_line not in needed:
                        continue

                    line = line.strip()
                    if not line:
                        continue

                    try:
                        event = json.loads(line)
                        events.append(event)
                    except json.JSONDecodeError:
                        continue

        except OSError as exc:
            logger.debug(
                "[audit_query] Failed to read %s: %s",
                file_path,
                exc,
            )

        return events

    @staticmethod
    def _extract_timestamp(event: dict[str, Any]) -> datetime | None:
        """Extract datetime from an event's timestamp field.

        Args:
            event: Event dictionary.

        Returns:
            datetime object or None if not parseable.
        """
        ts = event.get("timestamp")
        if ts is None:
            return None

        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                return ts.replace(tzinfo=timezone.utc)
            return ts

        if isinstance(ts, (int, float)):
            try:
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except (ValueError, OSError):
                return None

        if not isinstance(ts, str):
            return None

        # Parse ISO format string
        ts_clean = ts.strip()
        if ts_clean.endswith("Z"):
            ts_clean = ts_clean[:-1] + "+00:00"

        try:
            return datetime.fromisoformat(ts_clean).astimezone(timezone.utc)
        except ValueError:
            pass

        # Try parsing with just the date portion
        try:
            return datetime.strptime(ts_clean[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass

        return None

    # -------------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------------

    def get_partition_stats(self) -> dict[str, Any]:
        """Get comprehensive partition statistics.

        Returns:
            Dictionary with statistics about all partitions.
        """
        partitions = self.discover_partitions()
        file_counts = self._index.get_file_counts() if self._index.is_built else {}

        # Build stats
        total_size = 0
        total_events = 0
        by_workspace: dict[str, dict[str, Any]] = {}
        by_date: dict[str, dict[str, Any]] = {}
        by_event_type: dict[str, dict[str, Any]] = {}

        for part in partitions:
            total_size += part.file_size_bytes
            event_count = file_counts.get(part.file_path, 0)
            total_events += event_count

            # By workspace
            if part.workspace not in by_workspace:
                by_workspace[part.workspace] = {
                    "partition_count": 0,
                    "total_size_bytes": 0,
                    "total_events": 0,
                }
            by_workspace[part.workspace]["partition_count"] += 1
            by_workspace[part.workspace]["total_size_bytes"] += part.file_size_bytes
            by_workspace[part.workspace]["total_events"] += event_count

            # By date
            if part.date not in by_date:
                by_date[part.date] = {
                    "partition_count": 0,
                    "total_size_bytes": 0,
                    "total_events": 0,
                }
            by_date[part.date]["partition_count"] += 1
            by_date[part.date]["total_size_bytes"] += part.file_size_bytes
            by_date[part.date]["total_events"] += event_count

            # By event_type
            if part.event_type not in by_event_type:
                by_event_type[part.event_type] = {
                    "partition_count": 0,
                    "total_size_bytes": 0,
                    "total_events": 0,
                }
            by_event_type[part.event_type]["partition_count"] += 1
            by_event_type[part.event_type]["total_size_bytes"] += part.file_size_bytes
            by_event_type[part.event_type]["total_events"] += event_count

        # Get date range
        dates = sorted(by_date.keys())
        date_range = {"oldest": dates[0] if dates else None, "newest": dates[-1] if dates else None}

        return {
            "summary": {
                "total_partitions": len(partitions),
                "total_size_bytes": total_size,
                "total_events": total_events,
                "index_built": self._index.is_built,
            },
            "date_range": date_range,
            "by_workspace": by_workspace,
            "by_date": by_date,
            "by_event_type": by_event_type,
        }
