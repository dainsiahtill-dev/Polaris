"""Error chain tracing system for Polaris.

Provides functionality to search for errors and trace failure chains,
including tool arguments, outputs, and related context.

CRITICAL: All text file I/O must use UTF-8 encoding.
"""

from __future__ import annotations

import json
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Maximum number of files to cache in EventLoader to prevent unbounded memory growth
MAX_EVENT_CACHE_ENTRIES = 100


def _parse_event_datetime(value: Any) -> datetime | None:
    token = str(value or "").strip()
    if not token:
        return None

    normalized = token
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    # datetime.fromisoformat only supports up to microseconds (6 digits).
    match = re.match(
        r"^(?P<prefix>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(?P<fraction>\d+))?(?P<tz>[+-]\d{2}:\d{2})?$",
        normalized,
    )
    if match:
        prefix = match.group("prefix")
        fraction = match.group("fraction") or ""
        tz_part = match.group("tz") or ""
        if len(fraction) > 6:
            fraction = fraction[:6]
        normalized = f"{prefix}.{fraction}{tz_part}" if fraction else f"{prefix}{tz_part}"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass
class ErrorChainLink:
    """A single link in the error chain."""

    event_id: str
    seq: int
    ts: str
    ts_epoch: float
    kind: str  # "action" | "observation" | "factory"
    actor: str
    name: str
    refs: dict[str, Any] = field(default_factory=dict)

    # Action specific
    input: dict[str, Any] | None = None

    # Observation specific
    ok: bool | None = None
    output: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int | None = None

    @classmethod
    def from_event(cls, event: dict[str, Any] | None) -> ErrorChainLink | None:
        """Create ErrorChainLink from event dictionary.

        Handles both runtime events (kind, actor, name) and factory events (type, stage, message).
        """
        if not event:
            return None

        # Handle factory events format
        if "type" in event and "kind" not in event:
            return cls._from_factory_event(event)

        # Handle runtime events format
        return cls._from_runtime_event(event)

    @classmethod
    def _from_runtime_event(cls, event: dict[str, Any]) -> ErrorChainLink:
        """Create from runtime event format."""
        return cls(
            event_id=event.get("event_id", ""),
            seq=event.get("seq", 0),
            ts=event.get("ts", ""),
            ts_epoch=event.get("ts_epoch", 0.0),
            kind=event.get("kind", "unknown"),
            actor=event.get("actor", "unknown"),
            name=event.get("name", ""),
            refs=event.get("refs", {}),
            input=event.get("input"),
            ok=event.get("ok"),
            output=event.get("output"),
            error=event.get("error"),
            duration_ms=event.get("duration_ms"),
        )

    @classmethod
    def _from_factory_event(cls, event: dict[str, Any]) -> ErrorChainLink:
        """Create from factory event format."""
        event_type = event.get("type", "unknown")

        # Map factory event types to kinds
        kind_map = {
            "started": "state",
            "stage_started": "state",
            "stage_heartbeat": "observation",
            "stage_completed": "state",
            "completed": "state",
            "error": "observation",
            "failed": "observation",
        }

        # Determine ok status
        ok = None
        if event_type in ("error", "failed"):
            ok = False
        elif event_type in ("completed", "stage_completed"):
            result = event.get("result", {})
            ok = result.get("status") == "success" if isinstance(result, dict) else True

        # Extract error from message or result
        error = None
        if event_type in ("error", "failed"):
            error = event.get("message", "")
        elif event_type == "stage_completed":
            result = event.get("result", {})
            if isinstance(result, dict) and result.get("status") != "success":
                error = result.get("output", "")

        # Extract timestamp
        ts = event.get("timestamp", "")
        ts_epoch = 0.0
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                ts_epoch = dt.timestamp()
            except (ValueError, TypeError):
                pass

        # Build refs from available info
        refs = {}
        if "_run_id" in event:
            refs["run_id"] = event["_run_id"]
        result = event.get("result", {})
        if isinstance(result, dict) and "stage" in result:
            refs["stage"] = result["stage"]

        return cls(
            event_id=event.get("event_id", ""),
            seq=event.get("seq", 0),  # Factory events may not have seq
            ts=ts,
            ts_epoch=ts_epoch,
            kind=kind_map.get(event_type, "unknown"),
            actor=event.get("stage", "factory"),  # Use stage as actor
            name=event.get("type", ""),
            refs=refs,
            input=None,
            ok=ok,
            output=event.get("result") if isinstance(event.get("result"), dict) else None,
            error=error,
            duration_ms=None,
        )


@dataclass
class ErrorChain:
    """Complete error chain with context."""

    chain_id: str
    failure_event: ErrorChainLink
    related_action: ErrorChainLink | None = None
    context_events: list[ErrorChainLink] = field(default_factory=list)
    timeline: list[ErrorChainLink] = field(default_factory=list)

    # Derived information
    tool_name: str = ""
    tool_args: list[str] = field(default_factory=list)
    failure_reason: str = ""
    stack_trace: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "chain_id": self.chain_id,
            "tool_name": self.tool_name,
            "failure_reason": self.failure_reason,
            "failure_event": self._link_to_dict(self.failure_event),
            "related_action": self._link_to_dict(self.related_action) if self.related_action else None,
            "context_events": [self._link_to_dict(e) for e in self.context_events],
            "timeline": [self._link_to_dict(e) for e in self.timeline],
            "tool_args": self.tool_args,
            "stack_trace": self.stack_trace,
        }

    @staticmethod
    def _link_to_dict(link: ErrorChainLink) -> dict[str, Any]:
        """Convert link to dictionary, excluding None values."""
        result = {
            "event_id": link.event_id,
            "seq": link.seq,
            "ts": link.ts,
            "kind": link.kind,
            "actor": link.actor,
            "name": link.name,
            "refs": link.refs,
        }
        if link.input is not None:
            result["input"] = link.input
        if link.ok is not None:
            result["ok"] = link.ok
        if link.output is not None:
            result["output"] = link.output
        if link.error is not None:
            result["error"] = link.error
        if link.duration_ms is not None:
            result["duration_ms"] = link.duration_ms
        return result


class ErrorMatcher:
    """Pattern matching strategies for error search."""

    @staticmethod
    def create_matcher(pattern: str, strategy: str = "substring") -> Callable[[str], bool]:
        """Create a matcher function based on strategy."""
        if strategy == "exact":
            return lambda text: pattern == text
        elif strategy == "substring":
            return lambda text: pattern in text
        elif strategy == "regex":
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
                return lambda text: bool(compiled.search(text))
            except re.error:
                # Fallback to substring on invalid regex
                return lambda text: pattern in text
        elif strategy == "fuzzy":
            return lambda text: ErrorMatcher._fuzzy_match(pattern, text)
        else:
            return lambda text: pattern in text

    @staticmethod
    def _fuzzy_match(pattern: Any, text: Any, threshold: float = 0.7) -> bool:
        """Simple fuzzy matching based on token overlap."""
        # FIX 5: coerce to str to avoid AttributeError on non-string inputs
        pattern_tokens = set(str(pattern).lower().split())
        text_tokens = set(str(text).lower().split())
        if not pattern_tokens:
            return False
        overlap = len(pattern_tokens & text_tokens)
        return overlap / len(pattern_tokens) >= threshold

    @staticmethod
    def match_event(event: dict[str, Any], matcher: Callable[[str], bool]) -> bool:
        """Check if event matches the pattern.

        Searches in multiple fields to find matches in different event formats.
        """
        # Check error field (runtime events)
        error = event.get("error", "")
        if error and matcher(error):
            return True

        # Check output.error field (runtime events)
        output = event.get("output", {})
        if isinstance(output, dict):
            output_error = output.get("error", "")
            if output_error and matcher(output_error):
                return True

        # Check summary field (runtime events)
        summary = event.get("summary", "")
        if summary and matcher(summary):
            return True

        # Check message field (factory events)
        message = event.get("message", "")
        if message and matcher(message):
            return True

        # Check result.output field (factory events)
        result = event.get("result", {})
        if isinstance(result, dict):
            result_output = result.get("output", "")
            if isinstance(result_output, str) and matcher(result_output):
                return True
            result_status = result.get("status", "")
            if isinstance(result_status, str) and matcher(result_status):
                return True

        # Check traceback field (factory events)
        traceback = event.get("traceback", "")
        if traceback and matcher(traceback):
            return True

        # Check type field (role events like qa, pm, etc.)
        event_type = event.get("type", "")
        if event_type and matcher(event_type):
            return True

        # Check role field
        role = event.get("role", "")
        if role and matcher(role):
            return True

        # Check data.content_preview for role events
        data = event.get("data", {})
        if isinstance(data, dict):
            content_preview = data.get("content_preview", "")
            if isinstance(content_preview, str) and matcher(content_preview):
                return True
            # Also check any string values in data
            for key, value in data.items():
                if isinstance(value, str) and matcher(value):
                    return True
                # Check nested dicts
                if isinstance(value, dict):
                    for k, v in value.items():
                        if isinstance(v, str) and matcher(v):
                            return True

        # Check name field
        name = event.get("name", "")
        if name and matcher(name):
            return True

        # Check actor field
        actor = event.get("actor", "")
        return bool(actor and matcher(actor))


class EventLoader:
    """Load and cache event data from various sources.

    Uses an LRU cache with a maximum size to prevent unbounded memory growth.
    """

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = Path(runtime_root).resolve()
        # Use OrderedDict for LRU cache behavior
        self._cache: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._max_cache_size = MAX_EVENT_CACHE_ENTRIES

    def _get_from_cache(self, cache_key: str) -> list[dict[str, Any]] | None:
        """Get item from cache and move to end (most recently used)."""
        if cache_key in self._cache:
            # Move to end to mark as recently used
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]
        return None

    def _add_to_cache(self, cache_key: str, events: list[dict[str, Any]]) -> None:
        """Add item to cache, evicting oldest if at capacity."""
        if cache_key in self._cache:
            # Update existing entry
            self._cache[cache_key] = events
            self._cache.move_to_end(cache_key)
        else:
            # Evict oldest if at capacity
            if len(self._cache) >= self._max_cache_size:
                self._cache.popitem(last=False)
            self._cache[cache_key] = events

    def get_event_files(self) -> list[Path]:
        """Discover all event files in runtime directory."""
        files = []

        # Runtime events
        runtime_events = self.runtime_root / "events" / "runtime.events.jsonl"
        if runtime_events.exists():
            files.append(runtime_events)

        # Role-specific events
        roles_dir = self.runtime_root / "roles"
        if roles_dir.exists():
            for role_dir in roles_dir.iterdir():
                if role_dir.is_dir():
                    logs_dir = role_dir / "logs"
                    if logs_dir.exists():
                        for event_file in logs_dir.glob("events_*.jsonl"):
                            files.append(event_file)

        # Audit events
        audit_dir = self.runtime_root / "audit"
        if audit_dir.exists():
            for audit_file in audit_dir.glob("audit-*.jsonl"):
                files.append(audit_file)

        return files

    def load_events_from_file(self, file_path: Path) -> list[dict[str, Any]]:
        """Load events from a JSONL file."""
        cache_key = str(file_path)
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        events = []
        try:
            with open(file_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    try:
                        event = json.loads(line)
                        # Add file source for debugging
                        event["_source_file"] = str(file_path)
                        events.append(event)
                    except json.JSONDecodeError:
                        continue
        except OSError as e:
            # Log error but continue
            logger.warning("Warning: Could not read %s: %s", file_path, e)

        self._add_to_cache(cache_key, events)
        return events

    def load_factory_events(self, workspace: Path) -> list[dict[str, Any]]:
        """Load factory run events from workspace."""
        cache_key = f"factory:{workspace}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        events: list[dict[str, Any]] = []
        metadata_dir = get_workspace_metadata_dir_name()
        factory_dir = workspace / metadata_dir / "factory"
        if not factory_dir.exists():
            return events

        for run_dir in factory_dir.iterdir():
            if not run_dir.is_dir() or not run_dir.name.startswith("factory_"):
                continue
            events_file = run_dir / "events" / "events.jsonl"
            if events_file.exists():
                run_events = self.load_events_from_file(events_file)
                # Add run_id to each event
                for event in run_events:
                    event["_run_id"] = run_dir.name
                events.extend(run_events)

        self._add_to_cache(cache_key, events)
        return events

    def load_all_events(self, include_factory: bool = False) -> list[dict[str, Any]]:
        """Load all events from all sources."""
        all_events = []

        # Runtime events
        for event_file in self.get_event_files():
            all_events.extend(self.load_events_from_file(event_file))

        # Factory events (if workspace can be inferred)
        if include_factory:
            workspace = self._infer_workspace()
            if workspace:
                all_events.extend(self.load_factory_events(workspace))

        return all_events

    def _infer_workspace(self) -> Path | None:
        """Infer workspace path from runtime root."""
        # Typical structure: workspace/runtime
        if self.runtime_root.name == "runtime":
            return self.runtime_root.parent

        # Alternative: workspace/projects/proj/runtime
        metadata_dir = get_workspace_metadata_dir_name()
        for parent in self.runtime_root.parents:
            if parent.name in (".polaris", metadata_dir):
                return parent.parent

        return None


class ChainBuilder:
    """Build error chains from events."""

    def __init__(self, event_loader: EventLoader) -> None:
        self.event_loader = event_loader

    def find_related_action(
        self, failed_observation: dict[str, Any], all_events: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Find the corresponding action event for a failed observation.

        Matching strategy:
        1. Same name field (tool name)
        2. Same refs.task_id and refs.run_id
        3. action.seq < observation.seq
        4. Closest action (highest seq less than observation.seq)
        """
        failure_seq = failed_observation.get("seq", 0)
        failure_name = failed_observation.get("name", "")
        failure_refs = failed_observation.get("refs", {})

        candidates = []
        for event in all_events:
            if event.get("kind") != "action":
                continue
            if event.get("name") != failure_name:
                continue

            event_refs = event.get("refs", {})
            if event_refs.get("task_id") != failure_refs.get("task_id"):
                continue
            if event_refs.get("run_id") != failure_refs.get("run_id"):
                continue

            event_seq = event.get("seq", 0)
            if event_seq >= failure_seq:
                continue

            candidates.append(event)

        if not candidates:
            return None

        # Return the most recent action (highest seq)
        candidates.sort(key=lambda e: e.get("seq", 0), reverse=True)
        return candidates[0]

    def build_error_chain(
        self, failed_event: dict[str, Any], all_events: list[dict[str, Any]], context_window: int = 5
    ) -> ErrorChain:
        """Build a complete error chain for a failed event."""

        # FIX 1a: from_event returns None for falsy events; construct a
        # minimal fallback rather than propagating None into the chain.
        failure_link = ErrorChainLink.from_event(failed_event)
        if failure_link is None:
            failure_link = ErrorChainLink(
                event_id=failed_event.get("event_id", ""),
                seq=failed_event.get("seq", 0),
                ts=failed_event.get("ts", ""),
                ts_epoch=failed_event.get("ts_epoch", 0.0),
                kind="unknown",
                actor="unknown",
                name=failed_event.get("name", ""),
            )

        # Find related action
        related_action = self.find_related_action(failed_event, all_events)
        action_link = ErrorChainLink.from_event(related_action) if related_action else None

        # Collect context events
        failure_seq = failed_event.get("seq", 0)
        failure_refs = failed_event.get("refs", {})
        failure_run_id = failure_refs.get("run_id")
        failure_task_id = failure_refs.get("task_id")

        context_events = []
        for event in all_events:
            if event.get("event_id") == failed_event.get("event_id"):
                continue
            if related_action and event.get("event_id") == related_action.get("event_id"):
                continue

            # Check if in context window by seq
            event_seq = event.get("seq", 0)
            if abs(event_seq - failure_seq) <= context_window:
                event_refs = event.get("refs", {})
                # FIX 3: guard against None==None false positives when
                # failure_run_id or failure_task_id is absent.
                run_match = failure_run_id is not None and event_refs.get("run_id") == failure_run_id
                task_match = failure_task_id is not None and event_refs.get("task_id") == failure_task_id
                if run_match or task_match:
                    # FIX 1b: skip None links returned by from_event
                    link = ErrorChainLink.from_event(event)
                    if link is not None:
                        context_events.append(link)

        # Build timeline
        timeline = []
        if action_link:
            timeline.append(action_link)
        timeline.append(failure_link)
        timeline.extend(context_events)
        # FIX 6: filter residual None entries before sorting to prevent
        # AttributeError on .seq when any None slipped through.
        timeline = [x for x in timeline if x is not None]
        timeline.sort(key=lambda x: x.seq)

        # FIX 2: guard against input being a non-dict (e.g. serialised string)
        tool_args: list[str] = []
        if related_action:
            raw_input = related_action.get("input")
            if isinstance(raw_input, dict):
                tool_args = raw_input.get("args", [])

        # Extract failure reason
        failure_reason = self._extract_failure_reason(failed_event)

        return ErrorChain(
            chain_id=failed_event.get("event_id", ""),
            failure_event=failure_link,
            related_action=action_link,
            context_events=context_events,
            timeline=timeline,
            tool_name=failed_event.get("name", ""),
            tool_args=tool_args,
            failure_reason=failure_reason,
        )

    def _extract_failure_reason(self, failed_event: dict[str, Any]) -> str:
        """Extract human-readable failure reason from event.

        Handles both runtime events and factory events.
        For non-error events, returns a descriptive summary.
        """
        # Check error field (runtime events)
        error = failed_event.get("error", "")
        if error:
            return error

        # Check output.error (runtime events)
        output = failed_event.get("output", {})
        if isinstance(output, dict):
            output_error = output.get("error", "")
            if output_error:
                return output_error

        # Check message field (factory events)
        message = failed_event.get("message", "")
        if message:
            return message

        # Check result.output field (factory events)
        result = failed_event.get("result", {})
        if isinstance(result, dict):
            result_output = result.get("output", "")
            if isinstance(result_output, str) and result_output:
                return result_output

        # Check if ok=False
        ok = failed_event.get("ok")
        if ok is False:
            return "Unknown error (ok=False)"

        # For non-error events, provide descriptive summary
        event_type = failed_event.get("type", "")
        if event_type:
            return f"Event: {event_type}"

        name = failed_event.get("name", "")
        if name:
            return f"Event: {name}"

        summary = failed_event.get("summary", "")
        if summary:
            return f"Event: {summary[:100]}"

        return "No error information available"


class ErrorChainSearcher:
    """Main error chain searcher."""

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = Path(runtime_root).resolve()
        self.event_loader = EventLoader(self.runtime_root)
        self.chain_builder = ChainBuilder(self.event_loader)
        self.matcher_factory = ErrorMatcher()
        # Diagnostic stats
        self.last_search_stats: dict[str, Any] = {}

    @staticmethod
    def _classify_event_source(event: dict[str, Any]) -> str:
        """Classify event source for diagnostics."""
        source_file = str(event.get("_source_file", "") or "").replace("\\", "/")

        if "/.polaris/factory/" in source_file or "/.polaris/factory/" in source_file or bool(event.get("_run_id")):
            return "factory"

        if "/roles/" in source_file and "/logs/" in source_file:
            return "role"

        if event.get("kind") in {"action", "observation", "state"}:
            return "runtime"

        # Role event fallback: role + type schema without runtime "kind"
        if "role" in event and "type" in event and "kind" not in event:
            return "role"

        return "runtime"

    def search(
        self,
        pattern: str,
        strategy: str = "substring",
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        context_window: int = 5,
        link_chains: bool = True,
        include_factory: bool = False,
    ) -> list[ErrorChain]:
        """Search for errors and build error chains.

        Args:
            pattern: Error pattern to search for
            strategy: Matching strategy (exact, substring, regex, fuzzy)
            since: Only search events after this time
            until: Only search events before this time
            limit: Maximum number of results
            context_window: Number of context events to include
            link_chains: Whether to link action/observation pairs
            include_factory: Whether to include factory events

        Returns:
            List of ErrorChain objects
        """
        matcher = self.matcher_factory.create_matcher(pattern, strategy)

        # Load all events
        all_events = self.event_loader.load_all_events(include_factory=include_factory)

        # Filter by time if specified
        if since or until:
            all_events = self._filter_by_time(all_events, since, until)

        # Collect diagnostic info
        files_scanned = [str(f) for f in self.event_loader.get_event_files()]
        factory_files = []
        if include_factory:
            workspace = self.event_loader._infer_workspace()
            if workspace:
                metadata_dir = get_workspace_metadata_dir_name()
                factory_dir = workspace / metadata_dir / "factory"
                if factory_dir.exists():
                    factory_files = [str(f) for f in factory_dir.glob("**/events.jsonl")]

        # Count event types
        action_count = sum(1 for e in all_events if e.get("kind") == "action")
        observation_count = sum(1 for e in all_events if e.get("kind") == "observation")
        source_buckets = {"factory": 0, "role": 0, "runtime": 0}
        for event in all_events:
            bucket = self._classify_event_source(event)
            source_buckets[bucket] = source_buckets.get(bucket, 0) + 1

        # Store diagnostic stats
        self.last_search_stats = {
            "files_scanned": files_scanned,
            "factory_files": factory_files,
            "total_events": len(all_events),
            "action_events": action_count,
            "observation_events": observation_count,
            "factory_events": source_buckets.get("factory", 0),
            "role_events": source_buckets.get("role", 0),
            "runtime_events": source_buckets.get("runtime", 0),
            "pattern": pattern,
            "strategy": strategy,
        }

        # Find matching events (observations with errors)
        matching_events = []
        for event in all_events:
            # Skip action events for matching (they don't have errors)
            if event.get("kind") == "action":
                continue

            if self.matcher_factory.match_event(event, matcher):
                matching_events.append(event)

        self.last_search_stats["matching_events"] = len(matching_events)

        # Build error chains
        chains = []
        for event in matching_events[:limit]:
            if link_chains:
                # Need events from same run/task for linking
                refs = event.get("refs", {})
                run_id = refs.get("run_id")
                task_id = refs.get("task_id")

                # Filter relevant events for context
                relevant_events = [
                    e
                    for e in all_events
                    if e.get("refs", {}).get("run_id") == run_id or e.get("refs", {}).get("task_id") == task_id
                ]

                chain = self.chain_builder.build_error_chain(event, relevant_events, context_window)
            else:
                # Simple chain without linking
                chain = self.chain_builder.build_error_chain(event, [], context_window)

            chains.append(chain)

        return chains

    def _filter_by_time(
        self, events: list[dict[str, Any]], since: datetime | None, until: datetime | None
    ) -> list[dict[str, Any]]:
        """Filter events by time range."""
        filtered = []
        normalized_since = since
        normalized_until = until
        if normalized_since is not None and normalized_since.tzinfo is None:
            normalized_since = normalized_since.replace(tzinfo=timezone.utc)
        if normalized_until is not None and normalized_until.tzinfo is None:
            normalized_until = normalized_until.replace(tzinfo=timezone.utc)

        for event in events:
            ts = event.get("ts") or event.get("timestamp") or ""
            event_time = _parse_event_datetime(ts)
            if event_time is None:
                # Keep events with invalid timestamps
                filtered.append(event)
                continue
            if normalized_since and event_time < normalized_since:
                continue
            if normalized_until and event_time > normalized_until:
                continue
            filtered.append(event)
        return filtered


def search_error_chains(
    runtime_root: str | Path,
    pattern: str,
    strategy: str = "substring",
    limit: int = 50,
    context_window: int = 5,
    link_chains: bool = True,
) -> list[ErrorChain]:
    """Convenience function to search for error chains.

    Args:
        runtime_root: Path to runtime directory
        pattern: Error pattern to search for
        strategy: Matching strategy (exact, substring, regex, fuzzy)
        limit: Maximum number of results
        context_window: Number of context events to include
        link_chains: Whether to link action/observation pairs

    Returns:
        List of ErrorChain objects
    """
    searcher = ErrorChainSearcher(Path(runtime_root))
    return searcher.search(
        pattern=pattern, strategy=strategy, limit=limit, context_window=context_window, link_chains=link_chains
    )
