"""Main runtime execution engine for State-First Context OS.

This module defines `StateFirstContextOS`, the canonical session-level context
engine. It orchestrates projection, lifecycle management, and public API surface.
State-building and scheduling responsibilities are delegated to mixin classes.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from polaris.kernelone.context.projection_engine import ProjectionEngine
from polaris.kernelone.context.receipt_store import ReceiptStore
from polaris.kernelone.context.truth_log_service import TruthLogService
from polaris.kernelone.context.working_state_manager import WorkingStateManager
from polaris.kernelone.errors import StateNotFoundError, ValidationError

from ..bounded_cache import BoundedCache, LRUBoundedCache
from ..classifier import DialogActClassifier
from ..domain_adapters import (
    ContextDomainAdapter,
    ContextOSObserver,
    get_context_domain_adapter,
)
from ..helpers import _clamp_confidence, _normalize_text, _utc_now_iso, get_metadata_value
from ..model_utils import validated_replace
from ..models_v2 import (
    ContextOSProjectionV2 as ContextOSProjection,
    ContextOSSnapshotV2 as ContextOSSnapshot,
    RoutingClassEnum as RoutingClass,
    TranscriptEventV2 as TranscriptEvent,
)
from ..pipeline import PipelineInput, PipelineRunner
from ..policies import StateFirstContextOSPolicy
from ..snapshot import ImmutableSnapshot, SnapshotStore
from .scheduler import _ContextOSSchedulerMixin
from .state import _ContextOSStateMixin

logger = logging.getLogger(__name__)

class StateFirstContextOS(_ContextOSStateMixin, _ContextOSSchedulerMixin):
    """Canonical state-first session context engine.

    This class is **async-safe** for concurrent project() calls via asyncio.Lock.
    The project() method is async and must be awaited.
    For async contexts, use cleanup() or close() to release resources.
    For sync contexts, use the context manager protocol (__enter__/__exit__).
    """

    def __init__(
        self,
        policy: StateFirstContextOSPolicy | None = None,
        *,
        domain_adapter: ContextDomainAdapter | None = None,
        domain: str | None = None,
        provider_id: str | None = None,
        model: str | None = None,
        workspace: str | None = None,
    ) -> None:
        self.policy = policy or StateFirstContextOSPolicy()
        self.domain_adapter = domain_adapter or get_context_domain_adapter(domain)

        # LLM Provider configuration for context window resolution
        self._provider_id = str(provider_id or "").strip()
        self._model = str(model or "").strip()
        self._workspace = str(workspace or ".").strip()
        self._resolved_context_window: int | None = None

        # Initialize dialog act classifier if enabled
        self._dialog_act_classifier: DialogActClassifier | None = None
        if self.policy.enable_dialog_act:
            self._dialog_act_classifier = DialogActClassifier()

        # Async lock for thread-safety in async contexts (lazy initialization)
        self._cleanup_lock: asyncio.Lock | None = None

        # Phase 1 Fix: Async lock for project() concurrency safety
        # asyncio.Lock is awaitable and does not block the event loop.
        # Eagerly initialized to avoid thread-safety issues with lazy init
        # in concurrent async contexts.
        self._project_lock: asyncio.Lock = asyncio.Lock()

        # Observer registry for lifecycle events
        self._observers: list[ContextOSObserver] = []

        # Hook manager for plugin system (lazy initialization)
        self._hook_manager: Any | None = None

        # Pipeline runner (lazy initialization)
        self._pipeline_runner: PipelineRunner | None = None

        # Immutable snapshot store for context projection audit/replay
        # Resolve via Workspace persistent layer: <workspace>/.polaris/meta/context_snapshots
        self._snapshot_store: SnapshotStore | None = None

        # v2.1: Content store for content-addressable deduplication (lazy init)
        self._content_store: Any | None = None
        self._content_store_cache: BoundedCache[str, Any] = LRUBoundedCache(
            max_entries=128,
            max_bytes=500_000_000,  # 500MB
        )

        # Thread pool executor for offloading CPU-intensive work from event loop
        self._executor: ThreadPoolExecutor | None = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="context_os_",
        )

        # Four-layer ContextOS split components
        self._truth_log = TruthLogService()
        self._working_state_manager = WorkingStateManager(workspace=self._workspace)
        self._receipt_store = ReceiptStore(workspace=self._workspace)
        self._projection_engine = ProjectionEngine()

    def project_messages(self, projection: ContextOSProjection) -> list[dict[str, Any]]:
        """Generate LLM-ready messages from a ContextOSProjection using ProjectionEngine.

        Large outputs are referenced via ReceiptStore rather than inlined.
        """
        payload = self._projection_engine.build_payload(
            active_window=projection.active_window,
            receipt_store=self._receipt_store,
            head_anchor=projection.head_anchor,
            tail_anchor=projection.tail_anchor,
            run_card=projection.run_card,
            structured_findings=projection.structured_findings,
        )
        return self._projection_engine.project(payload, self._receipt_store)

    def _get_pipeline_runner(self) -> PipelineRunner:
        """Get or create the pipeline runner (lazy initialization)."""
        if self._pipeline_runner is None:
            self._pipeline_runner = PipelineRunner(
                policy=self.policy,
                domain_adapter=self.domain_adapter,
                resolved_context_window=self.resolved_context_window,
            )
        return self._pipeline_runner

    def _get_hook_manager(self) -> Any:
        """Get or create the hook manager (lazy initialization to avoid circular imports)."""
        if self._hook_manager is None:
            from polaris.kernelone.cognitive.hooks import get_hook_manager

            self._hook_manager = get_hook_manager()
        return self._hook_manager

    def _get_snapshot_store(self) -> SnapshotStore:
        """Get or create the snapshot store (lazy initialization).

        Resolves the storage path through the Workspace persistent layer
        to ensure snapshots survive across restarts.
        """
        if self._snapshot_store is None:
            from pathlib import Path

            from polaris.kernelone.storage.layout import resolve_workspace_persistent_path

            workspace = self._workspace or "."
            snapshot_dir = resolve_workspace_persistent_path(workspace, "workspace/meta/context_snapshots")
            self._snapshot_store = SnapshotStore(Path(snapshot_dir))
        return self._snapshot_store

    def _get_content_store(self) -> Any:
        """Get or create the ContentStore for content-addressable dedup (per workspace)."""
        workspace = self._workspace or "."
        store = self._content_store_cache.get(workspace)
        if store is None:
            from ..content_store import ContentStore

            store = ContentStore(workspace=workspace)
            self._content_store_cache.put(workspace, store)
        return store

    def _get_cleanup_lock(self) -> asyncio.Lock:
        """Get or create the cleanup lock (lazy initialization)."""
        if self._cleanup_lock is None:
            self._cleanup_lock = asyncio.Lock()
        return self._cleanup_lock

    def _get_project_lock(self) -> asyncio.Lock:
        """Get the project lock.

        Lock is eagerly initialized in __init__ to ensure thread-safety
        in concurrent async contexts.
        """
        return self._project_lock

    def _create_snapshot(
        self,
        projection: ContextOSProjection,
        messages: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> None:
        """Create immutable snapshot for audit/replay and consistency measurement.

        Computes input and output hashes, then saves the snapshot to the store.
        Snapshots are write-once to ensure immutability.

        Args:
            projection: The context projection result.
            messages: The input messages used for projection.
        """
        # Compute input hash from messages
        input_content = json.dumps(messages, sort_keys=True, default=str)
        input_hash = hashlib.sha256(input_content.encode("utf-8")).hexdigest()[:16]

        # Compute output hash from projection summary
        output_summary: dict[str, Any] = {
            "head_anchor": projection.head_anchor[:100] if projection.head_anchor else "",
            "tail_anchor": projection.tail_anchor[:100] if projection.tail_anchor else "",
            "num_events": len(projection.active_window) if projection.active_window else 0,
            "num_artifacts": len(projection.artifact_stubs) if projection.artifact_stubs else 0,
            "num_episodes": len(projection.episode_cards) if projection.episode_cards else 0,
        }
        output_content = json.dumps(output_summary, sort_keys=True)
        output_hash = hashlib.sha256(output_content.encode("utf-8")).hexdigest()[:16]

        snapshot = ImmutableSnapshot(
            version="2.1.0",
            timestamp=datetime.now(timezone.utc).isoformat(),
            input_hash=input_hash,
            output_hash=output_hash,
            projection_summary=output_summary,
        )

        with contextlib.suppress(FileExistsError):
            self._get_snapshot_store().save(snapshot)

    async def cleanup(self) -> None:
        """Clean up resources, releasing DialogActClassifier and other held references.

        This method is async to support async cleanup patterns. After calling
        cleanup(), the instance should not be used for projection operations.

        Returns:
            None
        """
        async with self._get_cleanup_lock():
            # Release dialog act classifier
            self._dialog_act_classifier = None

            # Release pipeline runner and snapshot store
            self._pipeline_runner = None
            self._snapshot_store = None

            # Clear content store cache
            self._content_store_cache.clear()

            # Shutdown thread pool executor
            if self._executor is not None:
                self._executor.shutdown(wait=True)
                self._executor = None

            # Notify service components to release resources
            if self._receipt_store is not None and hasattr(self._receipt_store, "close"):
                await self._receipt_store.close()
            if self._working_state_manager is not None and hasattr(self._working_state_manager, "close"):
                await self._working_state_manager.close()
            if self._projection_engine is not None and hasattr(self._projection_engine, "close"):
                await self._projection_engine.close()

            logger.debug("StateFirstContextOS cleanup completed")

    async def close(self) -> None:
        """Async close method, calls cleanup() to release resources.

        This is the preferred async cleanup method. Equivalent to cleanup().

        Returns:
            None
        """
        await self.cleanup()

    def __enter__(self) -> StateFirstContextOS:
        """Sync context manager entry (no-op, returns self).

        Returns:
            self: The instance itself for context manager usage.
        """
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Sync context manager exit (releases resources).

        Args:
            exc_type: Exception type if an exception was raised, None otherwise.
            exc_val: Exception value if an exception was raised, None otherwise.
            exc_tb: Traceback if an exception was raised, None otherwise.

        Returns:
            None
        """
        # Synchronously release dialog act classifier
        self._dialog_act_classifier = None
        logger.debug("StateFirstContextOS context manager exit completed")

    def add_observer(self, observer: ContextOSObserver) -> None:
        """Add an observer to receive ContextOS lifecycle notifications.

        Args:
            observer: An object implementing ContextOSObserver protocol.
        """
        if observer not in self._observers:
            self._observers.append(observer)

    def remove_observer(self, observer: ContextOSObserver) -> None:
        """Remove an observer from ContextOS lifecycle notifications.

        Args:
            observer: The observer to remove.
        """
        if observer in self._observers:
            self._observers.remove(observer)

    def _notify_observers(
        self,
        method_name: str,
        *args: Any,
    ) -> None:
        """Notify all observers of a lifecycle event.

        Args:
            method_name: Name of the observer method to call.
            *args: Arguments to pass to the observer method.
        """
        for observer in self._observers:
            method = getattr(observer, method_name, None)
            if callable(method):
                try:
                    method(*args)
                except (RuntimeError, ValueError) as e:
                    logger.exception("Observer %s.%s raised: %s", type(observer).__name__, method_name, e)

    def _notify_projection_lifecycle_deltas(
        self,
        previous_snapshot: ContextOSSnapshot | None,
        projection: ContextOSProjection,
    ) -> None:
        """Replay projection lifecycle deltas to observers for pipeline mode.

        The monolithic runtime notifies observers inline while building events,
        artifacts, pending follow-ups, and episodes. Pipeline mode builds those
        objects inside stage processors, so we reconstruct the lifecycle delta
        from the previous snapshot and emit the same observer notifications here.
        """
        prev_event_ids = {event.event_id for event in previous_snapshot.transcript_log} if previous_snapshot else set()
        for event in projection.snapshot.transcript_log:
            if event.event_id not in prev_event_ids:
                self._notify_observers("on_event_created", event)

        prev_artifact_ids = (
            {artifact.artifact_id for artifact in previous_snapshot.artifact_store} if previous_snapshot else set()
        )
        for artifact in projection.snapshot.artifact_store:
            if artifact.artifact_id not in prev_artifact_ids:
                self._notify_observers("on_artifact_built", artifact)

        prev_episode_ids = (
            {episode.episode_id for episode in previous_snapshot.episode_store} if previous_snapshot else set()
        )
        for episode in projection.snapshot.episode_store:
            if episode.episode_id not in prev_episode_ids:
                self._notify_observers("on_episode_sealed", episode)

        current_followup = projection.snapshot.pending_followup
        previous_followup = previous_snapshot.pending_followup if previous_snapshot is not None else None
        if current_followup is None or current_followup.status == "pending":
            return
        if previous_followup is None or (
            current_followup.status != previous_followup.status
            or current_followup.updated_at != previous_followup.updated_at
            or current_followup.source_event_id != previous_followup.source_event_id
        ):
            self._notify_observers("on_pending_followup_resolved", current_followup)

    @property
    def resolved_context_window(self) -> int:
        """Resolve context window with priority: LLM Config > Hard-coded Table > Policy Default.

        Resolution order:
            1. LLM Provider Config Table (ModelCatalog.resolve)
            2. Hard-coded model windows table (_KNOWN_MODEL_WINDOWS)
            3. StateFirstContextOSPolicy.model_context_window (env var overridable)
        """
        if self._resolved_context_window is not None:
            return self._resolved_context_window

        # Try to resolve from LLM Config Table via ModelCatalog
        if self._provider_id and self._model:
            try:
                from polaris.kernelone.llm.engine.model_catalog import ModelCatalog

                spec = ModelCatalog(workspace=self._workspace).resolve(
                    self._provider_id,
                    self._model,
                )
                if spec.max_context_tokens > 0:
                    self._resolved_context_window = spec.max_context_tokens
                    return self._resolved_context_window
            except (RuntimeError, ValueError) as e:
                logger.debug("Could not resolve context window from catalog: %s", e)

        # Fall back to hard-coded table
        from polaris.kernelone.context.budget_gate import _resolve_model_window_from_spec

        if self._provider_id and self._model:
            window = _resolve_model_window_from_spec(self._provider_id, self._model)
            if window > 0:
                self._resolved_context_window = window
                return self._resolved_context_window

        # Final fallback to policy default (env var overridable)
        self._resolved_context_window = self.policy.model_context_window
        return self._resolved_context_window

    @property
    def dialog_act_classifier(self) -> DialogActClassifier:
        """Get or create dialog act classifier."""
        if self._dialog_act_classifier is None:
            self._dialog_act_classifier = DialogActClassifier()
        return self._dialog_act_classifier

    async def project(
        self,
        *,
        messages: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        existing_snapshot: ContextOSSnapshot | dict[str, Any] | None = None,
        recent_window_messages: int = 8,
        focus: str = "",
    ) -> ContextOSProjection:
        # Phase 2 Fix: Snapshot -> Compute -> Validate/Commit paradigm
        # 1. Take snapshot under lock (minimal critical section)
        async with self._get_project_lock():
            snapshot = self._take_snapshot()
            pipeline_runner = self._get_pipeline_runner()
            content_store = self._get_content_store()

        # 2. Compute projection outside lock (CPU-intensive, thread-safe)
        loop = asyncio.get_event_loop()
        projection = await loop.run_in_executor(
            self._executor,
            self._project_via_pipeline_sync,
            snapshot,
            messages,
            existing_snapshot,
            recent_window_messages,
            focus,
            pipeline_runner,
            content_store,
        )

        # 3. Commit under lock (CAS check + write)
        async with self._get_project_lock():
            if self._validate_projection(projection):
                self._commit_projection(projection)

        return projection

    def _take_snapshot(self) -> tuple[tuple[Any, ...], Any, dict[str, Any]]:
        """Capture minimal immutable snapshot of current state for thread-safe compute.

        Returns:
            Tuple of (transcript_log, working_state, content_store_cache).
        """
        from copy import deepcopy

        # GAP-2 Fix: Deep copy entries to prevent state pollution
        # _entries contains mutable dict[str, Any] - must deep copy at snapshot time
        # to ensure snapshot isolation even if original dicts are mutated later.
        raw_entries = getattr(self._truth_log, "_entries", ())
        transcript = tuple(self._truth_log._normalize_entry(entry) for entry in raw_entries)

        # working_state is already deep-copied by WorkingStateManager.current()
        working_state = self._working_state_manager.current()

        # GAP-2 Fix: Deep copy cache entries to prevent shared mutable state
        # The shallow dict() copy leaves values as shared references.
        # NOTE: ContentStore contains threading.Lock which is non-picklable.
        # We must deep-copy only the serializable subset to avoid pickle errors.
        raw_cache = getattr(self._content_store_cache, "_cache", {})
        cache_snapshot: dict[str, Any] = {}
        for key, value in raw_cache.items():
            if hasattr(value, "_lock") or hasattr(value, "_async_lock"):
                # ContentStore — extract serializable data only
                cache_snapshot[key] = {
                    "store_keys": list(getattr(value, "_store", {}).keys()),
                    "key_index_keys": list(getattr(value, "_key_index", {}).keys()),
                    "current_bytes": getattr(value, "_current_bytes", 0),
                    "hits": getattr(value, "_hits", 0),
                    "misses": getattr(value, "_misses", 0),
                    "evict_count": getattr(value, "_evict_count", 0),
                }
            else:
                try:
                    cache_snapshot[key] = deepcopy(value)
                except TypeError:
                    # Non-deep-copyable value (e.g. Lock, file handle) — store lightweight repr
                    cache_snapshot[key] = repr(value)

        return transcript, working_state, cache_snapshot

    def _validate_projection(self, projection: ContextOSProjection) -> bool:
        """Validate projection before commit (CAS check).

        Args:
            projection: The computed projection to validate.

        Returns:
            True if projection is valid and can be committed.
        """
        return projection is not None and projection.snapshot is not None

    def _commit_projection(self, projection: ContextOSProjection) -> None:
        """Commit validated projection to shared mutable state.

        Must be called under project_lock.

        Args:
            projection: The validated projection to commit.
        """
        # Authoritative service writeback from canonical projection snapshot.
        self._truth_log.replace(projection.snapshot.transcript_log)
        self._working_state_manager.replace(projection.snapshot.working_state)
        self._working_state_manager.update(
            "transcript_log", [item.to_dict() for item in projection.snapshot.transcript_log]
        )
        self._working_state_manager.update("artifact_store", projection.snapshot.artifact_store)
        self._working_state_manager.update("episode_store", projection.snapshot.episode_store)
        self._working_state_manager.update("budget_plan", projection.snapshot.budget_plan)
        self._working_state_manager.update("pending_followup", projection.snapshot.pending_followup)

        # Prime receipt refs for oversized content through ProjectionEngine policy.
        self._projection_engine.build_turns(projection.active_window, self._receipt_store)

    def _project_via_pipeline_sync(
        self,
        snapshot: tuple[tuple[Any, ...], Any, dict[str, Any]],
        messages: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        existing_snapshot: ContextOSSnapshot | dict[str, Any] | None,
        recent_window_messages: int,
        focus: str,
        pipeline_runner: PipelineRunner,
        content_store: Any,
    ) -> ContextOSProjection:
        """Project context via the 7-stage pipeline architecture (pure function).

        This method MUST NOT modify shared mutable state. It operates on
        copies/snapshots passed as arguments and returns a new projection.

        Integrates the four-layer ContextOS split:
        - TruthLogService: append incoming messages to the canonical log.
        - WorkingStateManager: holds active mutable state.
        - ReceiptStore: large outputs are referenced, not duplicated.
        - ProjectionEngine: available for prompt generation by consumers.
        """
        ctx_snapshot = (
            existing_snapshot
            if isinstance(existing_snapshot, ContextOSSnapshot)
            else ContextOSSnapshot.from_mapping(existing_snapshot)
        )
        _has_snapshot = ctx_snapshot is not None
        _existing_tx_len = len(ctx_snapshot.transcript_log) if ctx_snapshot is not None else 0
        logger.debug(
            "[DEBUG][ContextOS] _project_via_pipeline_sync start: has_snapshot=%s existing_tx=%d incoming_msgs=%d recent_window=%d focus=%r",
            _has_snapshot,
            _existing_tx_len,
            len(messages) if messages else 0,
            recent_window_messages,
            focus,
        )

        # Build pipeline input from snapshot (no shared state mutation)
        inp = PipelineInput(
            messages=messages,
            existing_snapshot_transcript=ctx_snapshot.transcript_log if ctx_snapshot is not None else (),
            existing_snapshot_artifacts=ctx_snapshot.artifact_store if ctx_snapshot is not None else (),
            existing_snapshot_episodes=ctx_snapshot.episode_store if ctx_snapshot is not None else (),
            current_pending_followup=ctx_snapshot.pending_followup if ctx_snapshot is not None else None,
            recent_window_messages=recent_window_messages,
            focus=focus,
        )

        projection, _report = pipeline_runner.project(inp, adapter_id=self.domain_adapter.adapter_id)

        logger.debug(
            "[DEBUG][ContextOS] _project_via_pipeline_sync end: tx_events=%d active_window=%d artifacts=%d episodes=%d run_card_goal=%r",
            len(projection.snapshot.transcript_log),
            len(projection.active_window),
            len(getattr(projection.snapshot, "artifact_store", ())),
            len(getattr(projection.snapshot, "episode_store", ())),
            projection.run_card.current_goal if projection.run_card else "<none>",
        )

        return projection

    async def reclassify_event(
        self,
        snapshot: ContextOSSnapshot | dict[str, Any] | None,
        *,
        event_id: str,
        new_route: str,
        reason: str,
        confidence: float = 1.0,
        recent_window_messages: int = 8,
        focus: str = "",
    ) -> ContextOSProjection:
        context = snapshot if isinstance(snapshot, ContextOSSnapshot) else ContextOSSnapshot.from_mapping(snapshot)
        if context is None:
            raise ValidationError("snapshot is required", field="snapshot")
        target_event_id = str(event_id or "").strip()
        # Handle both string and enum inputs for new_route
        if isinstance(new_route, RoutingClass):
            normalized_route = new_route.value
        else:
            normalized_route = str(new_route or "").strip().lower()
        normalized_reason = _normalize_text(reason)
        if normalized_route not in {"clear", "patch", "archive", "summarize"}:
            raise ValidationError(
                "new_route must be one of clear/patch/archive/summarize",
                field="new_route",
                constraint="routing_class",
            )
        if not target_event_id:
            raise ValidationError("event_id is required", field="event_id")
        if not normalized_reason:
            raise ValidationError("reason is required", field="reason")

        now = _utc_now_iso()
        found = False
        transcript: list[TranscriptEvent] = []
        for item in context.transcript_log:
            if item.event_id != target_event_id:
                transcript.append(item)
                continue
            found = True
            route_history = list(get_metadata_value(item.metadata, "route_history") or [])
            route_history.append(
                {
                    "from": item.route,
                    "to": normalized_route,
                    "reason": normalized_reason,
                    "at": now,
                }
            )
            metadata = {
                **dict(item.metadata),
                "forced_route": normalized_route,
                "routing_status": "reclassified",
                "routing_confidence": _clamp_confidence(confidence, default=1.0),
                "routing_reasons": [normalized_reason],
                "route_history": route_history,
                "reclassified_at": now,
            }
            if item.artifact_id and normalized_route != RoutingClass.ARCHIVE:
                metadata["archived_artifact_id"] = item.artifact_id
            # Use validated_replace() for safe model mutation with field validation
            replaced = validated_replace(item, route=normalized_route, metadata=metadata)
            transcript.append(replaced)
        if not found:
            raise StateNotFoundError(
                f"event not found: {target_event_id}",
                resource_type="transcript_event",
                resource_id=target_event_id,
            )
        return await self.project(
            messages=(),
            existing_snapshot=validated_replace(context, transcript_log=tuple(transcript)),
            recent_window_messages=recent_window_messages,
            focus=focus,
        )

    async def reopen_episode(
        self,
        snapshot: ContextOSSnapshot | dict[str, Any] | None,
        *,
        episode_id: str,
        reason: str,
        recent_window_messages: int = 8,
        focus: str = "",
    ) -> ContextOSProjection:
        context = snapshot if isinstance(snapshot, ContextOSSnapshot) else ContextOSSnapshot.from_mapping(snapshot)
        if context is None:
            raise ValidationError("snapshot is required", field="snapshot")
        target_episode_id = str(episode_id or "").strip()
        normalized_reason = _normalize_text(reason)
        if not target_episode_id:
            raise ValidationError("episode_id is required", field="episode_id")
        if not normalized_reason:
            raise ValidationError("reason is required", field="reason")

        now = _utc_now_iso()
        target_episode: Any | None = None
        updated_episodes: list[Any] = []
        for ep in context.episode_store:
            if ep.episode_id != target_episode_id:
                updated_episodes.append(ep)
                continue
            target_episode = ep
            updated_episodes.append(
                validated_replace(ep, status="reopened", reopened_at=now, reopen_reason=normalized_reason)
            )
        if target_episode is None:
            raise StateNotFoundError(
                f"episode not found: {target_episode_id}",
                resource_type="episode_card",
                resource_id=target_episode_id,
            )

        transcript: list[TranscriptEvent] = []
        for evt in context.transcript_log:
            if not (target_episode.from_sequence <= evt.sequence <= target_episode.to_sequence):
                transcript.append(evt)
                continue
            metadata = {
                **dict(evt.metadata),
                "reopen_hold": target_episode_id,
                "reopen_reason": normalized_reason,
                "reopened_at": now,
            }
            transcript.append(validated_replace(evt, metadata=metadata))

        return await self.project(
            messages=(),
            existing_snapshot=validated_replace(
                context, transcript_log=tuple(transcript), episode_store=tuple(updated_episodes)
            ),
            recent_window_messages=recent_window_messages,
            focus=focus or normalized_reason,
        )

    def search_memory(
        self,
        snapshot: ContextOSSnapshot | dict[str, Any] | None,
        query: str,
        *,
        kind: str | None = None,
        entity: str | None = None,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        context = snapshot if isinstance(snapshot, ContextOSSnapshot) else ContextOSSnapshot.from_mapping(snapshot)
        if context is None:
            return []
        from ..memory_search import _search_memory_impl

        return _search_memory_impl(
            context,
            query,
            kind=kind,
            entity=entity,
            limit=limit,
        )

    def read_artifact(
        self,
        snapshot: ContextOSSnapshot | dict[str, Any] | None,
        artifact_id: str,
        *,
        span: tuple[int, int] | None = None,
    ) -> dict[str, Any] | None:
        context = snapshot if isinstance(snapshot, ContextOSSnapshot) else ContextOSSnapshot.from_mapping(snapshot)
        if context is None:
            return None
        target = str(artifact_id or "").strip()
        if not target:
            return None
        for item in context.artifact_store:
            if item.artifact_id != target:
                continue
            content = item.content
            if span is not None:
                start, end = span
                lines = content.splitlines()
                start_idx = max(0, int(start) - 1)
                end_idx = max(start_idx, int(end))
                content = "\n".join(lines[start_idx:end_idx])
            payload = item.to_dict()
            payload["content"] = content
            return payload
        return None

    def read_episode(
        self,
        snapshot: ContextOSSnapshot | dict[str, Any] | None,
        episode_id: str,
    ) -> dict[str, Any] | None:
        context = snapshot if isinstance(snapshot, ContextOSSnapshot) else ContextOSSnapshot.from_mapping(snapshot)
        if context is None:
            return None
        target = str(episode_id or "").strip()
        if not target:
            return None
        for item in context.episode_store:
            if item.episode_id == target:
                return item.to_dict()
        return None

    def get_state(
        self,
        snapshot: ContextOSSnapshot | dict[str, Any] | None,
        path: str,
    ) -> Any:
        context = snapshot if isinstance(snapshot, ContextOSSnapshot) else ContextOSSnapshot.from_mapping(snapshot)
        if context is None:
            return None
        token = str(path or "").strip()
        if not token:
            return None
        prompt_view = self._rebuild_prompt_view(context)
        if token == "run_card":
            return prompt_view["run_card"].to_dict()
        if token == "context_slice_plan":
            return prompt_view["context_slice_plan"].to_dict()
        if token == "budget_plan":
            return prompt_view["budget_plan"].to_dict()
        if token == "task_state.current_goal":
            entry = context.working_state.task_state.current_goal
            return entry.to_dict() if entry is not None else None
        if token == "task_state.open_loops":
            return [item.to_dict() for item in context.working_state.task_state.open_loops]
        if token == "task_state.accepted_plan":
            return [item.to_dict() for item in context.working_state.task_state.accepted_plan]
        if token == "decision_log":
            return [item.to_dict() for item in context.working_state.decision_log]
        if token == "active_artifacts":
            return list(context.working_state.active_artifacts)
        for entry in context.working_state.state_history:
            if entry.path == token:
                return entry.to_dict()
        return None

    @staticmethod
    def _sequences_from_turns(turns: tuple[str, ...]) -> set[int]:
        result: set[int] = set()
        for turn in turns:
            token = str(turn).strip().lower()
            if token.startswith("t") and token[1:].isdigit():
                result.add(int(token[1:]))
        return result
