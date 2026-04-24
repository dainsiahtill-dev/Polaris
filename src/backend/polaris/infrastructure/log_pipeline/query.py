"""LogQueryService - Query Service for Log Events.

Provides unified query interface for log events across all channels.
Supports filtering by channel, severity, actor, run_id, and cursor-based pagination.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from polaris.kernelone.storage import resolve_storage_roots

from .canonical_event import (
    CanonicalLogEventV2,
    LogChannel,
    LogSeverity,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
logger = logging.getLogger(__name__)


@dataclass
class LogQuery:
    """Query parameters for log events."""

    channel: LogChannel | None = None
    severity: LogSeverity | None = None
    actor: str | None = None
    source: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    cursor: str | None = None  # event_id for pagination
    limit: int = 100
    include_raw: bool = False
    include_enriched: bool = False
    high_signal_only: bool = False  # Filter out noise


@dataclass
class LogQueryResult:
    """Query result with events and pagination info."""

    events: list[CanonicalLogEventV2]
    next_cursor: str | None
    total_count: int
    has_more: bool


class LogQueryService:
    """Service for querying log events.

    Provides unified interface for querying across all channels
    with support for filtering and pagination.
    """

    def __init__(
        self,
        workspace: str,
        runtime_root: str | None = None,
    ) -> None:
        """Initialize the query service.

        Args:
            workspace: Workspace directory
            runtime_root: Optional runtime root (defaults to unified storage layout)
        """
        self.workspace = os.path.abspath(workspace)
        if runtime_root:
            self.runtime_root = os.path.abspath(runtime_root)
        else:
            roots = resolve_storage_roots(self.workspace)
            self.runtime_root = os.path.abspath(roots.runtime_root)

    def _resolve_run_dir(self, run_id: str) -> str:
        """Resolve the run directory path."""
        return os.path.join(self.runtime_root, "runs", run_id, "logs")

    def _resolve_latest_run_dir(self) -> str | None:
        """Resolve the latest run directory."""
        latest_file = os.path.join(self.runtime_root, "latest_run.json")
        if os.path.exists(latest_file):
            try:
                with open(latest_file, encoding="utf-8") as f:
                    data = json.load(f)
                    run_dir = data.get("path")
                    if run_dir and os.path.isdir(run_dir):
                        return os.path.join(run_dir, "logs")
            except (RuntimeError, ValueError):
                logger.debug("DEBUG: query.py:{93} {exc} (swallowed)")
        return None

    def _iter_events(
        self,
        run_id: str,
        channel: LogChannel | None = None,
        severity: LogSeverity | None = None,
        actor: str | None = None,
        source: str | None = None,
        task_id: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
        high_signal_only: bool = False,
    ) -> Iterator[CanonicalLogEventV2]:
        """Iterate over events matching the query."""
        run_dir = self._resolve_run_dir(run_id)

        # Try norm path first, fallback to raw
        norm_path = os.path.join(run_dir, "journal.norm.jsonl")
        raw_path = os.path.join(run_dir, "journal.raw.jsonl")

        file_path = norm_path if os.path.exists(norm_path) else raw_path
        if not os.path.exists(file_path):
            return

        found_cursor = cursor is None
        count = 0

        with open(file_path, encoding="utf-8") as f:
            for line in f:
                if count >= limit:
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Skip until cursor
                if not found_cursor:
                    if data.get("event_id") == cursor:
                        found_cursor = True
                    continue

                # Parse event
                try:
                    event = CanonicalLogEventV2(**data)
                except (RuntimeError, ValueError):
                    continue

                # Apply filters
                if channel and event.channel != channel:
                    continue
                if severity and event.severity != severity:
                    continue
                if actor and event.actor.lower() != actor.lower():
                    continue
                if source and event.source != source:
                    continue
                if task_id:
                    refs = event.refs or {}
                    if refs.get("task_id") != task_id:
                        continue

                # High signal filter: exclude noise events
                if high_signal_only:
                    if event.enrichment and event.enrichment.noise:
                        continue
                    if event.severity == "debug":
                        continue

                yield event
                count += 1

    def query(
        self,
        query: LogQuery,
    ) -> LogQueryResult:
        """Execute a query and return results.

        Args:
            query: Query parameters

        Returns:
            LogQueryResult with events and pagination info
        """
        # Determine run_id
        run_id = query.run_id
        if not run_id:
            latest_dir = self._resolve_latest_run_dir()
            if latest_dir:
                run_id = os.path.basename(os.path.dirname(os.path.dirname(latest_dir)))

        if not run_id:
            return LogQueryResult(
                events=[],
                next_cursor=None,
                total_count=0,
                has_more=False,
            )

        # Iterate events
        events = list(
            self._iter_events(
                run_id=run_id,
                channel=query.channel,
                severity=query.severity,
                actor=query.actor,
                source=query.source,
                task_id=query.task_id,
                cursor=query.cursor,
                limit=query.limit + 1,  # Fetch one extra to check has_more
                high_signal_only=query.high_signal_only,
            )
        )

        # Check has_more
        has_more = len(events) > query.limit
        if has_more:
            events = events[: query.limit]

        # Get next cursor
        next_cursor = None
        if events and has_more:
            next_cursor = events[-1].event_id

        return LogQueryResult(
            events=events,
            next_cursor=next_cursor,
            total_count=len(events),
            has_more=has_more,
        )

    def query_by_run(
        self,
        run_id: str,
        channel: LogChannel | None = None,
        severity: LogSeverity | None = None,
        limit: int = 100,
    ) -> list[CanonicalLogEventV2]:
        """Query events for a specific run.

        Args:
            run_id: Run identifier
            channel: Optional channel filter
            severity: Optional severity filter
            limit: Maximum events to return

        Returns:
            List of events
        """
        result = self.query(
            LogQuery(
                run_id=run_id,
                channel=channel,
                severity=severity,
                limit=limit,
            )
        )
        return result.events

    def query_latest(
        self,
        channel: LogChannel | None = None,
        limit: int = 100,
    ) -> list[CanonicalLogEventV2]:
        """Query events from the latest run.

        Args:
            channel: Optional channel filter
            limit: Maximum events to return

        Returns:
            List of events
        """
        result = self.query(
            LogQuery(
                channel=channel,
                limit=limit,
            )
        )
        return result.events

    def get_channel_events(
        self,
        run_id: str,
        channel: LogChannel,
        limit: int = 100,
    ) -> list[CanonicalLogEventV2]:
        """Get events for a specific channel.

        Args:
            run_id: Run identifier
            channel: Channel name
            limit: Maximum events

        Returns:
            List of events for the channel
        """
        return self.query_by_run(run_id, channel=channel, limit=limit)

    def get_event_by_id(
        self,
        run_id: str,
        event_id: str,
    ) -> CanonicalLogEventV2 | None:
        """Get a specific event by ID.

        Args:
            run_id: Run identifier
            event_id: Event ID

        Returns:
            The event or None if not found
        """
        run_dir = self._resolve_run_dir(run_id)
        for filename in ["journal.norm.jsonl", "journal.raw.jsonl"]:
            path = os.path.join(run_dir, filename)
            if not os.path.exists(path):
                continue

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("event_id") == event_id:
                            return CanonicalLogEventV2(**data)
                    except (RuntimeError, ValueError):
                        continue

        return None


def get_query_service(workspace: str, runtime_root: str | None = None) -> LogQueryService:
    """Get a LogQueryService instance.

    Args:
        workspace: Workspace directory
        runtime_root: Optional runtime root

    Returns:
        LogQueryService instance
    """
    return LogQueryService(workspace=workspace, runtime_root=runtime_root)
