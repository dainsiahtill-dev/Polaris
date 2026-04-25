"""State-First Context OS runtime."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from polaris.kernelone.context.projection_engine import ProjectionEngine
from polaris.kernelone.context.receipt_store import ReceiptStore
from polaris.kernelone.context.truth_log_service import TruthLogService
from polaris.kernelone.context.working_state_manager import WorkingStateManager
from polaris.kernelone.errors import StateNotFoundError, ValidationError
from polaris.kernelone.telemetry.debug_stream import emit_debug_event

from .bounded_cache import BoundedCache, LRUBoundedCache
from .classifier import DialogActClassifier
from .domain_adapters import (
    ContextDomainAdapter,
    ContextOSObserver,
    DomainStatePatchHints,
    get_context_domain_adapter,
)
from .helpers import (
    _artifact_id,
    _clamp_confidence,
    _dedupe_state_entries,
    _estimate_tokens,
    _event_id,
    _normalize_text,
    _slug,
    _StateAccumulator,
    _trim_text,
    _utc_now_iso,
    get_metadata_value,
)
from .memory_search import _search_memory_impl
from .model_utils import validated_replace
from .models_v2 import (
    ArtifactRecordV2 as ArtifactRecord,
    BudgetPlanV2 as BudgetPlan,
    ContextOSProjectionV2 as ContextOSProjection,
    ContextOSSnapshotV2 as ContextOSSnapshot,
    ContextSlicePlanV2 as ContextSlicePlan,
    ContextSliceSelectionV2 as ContextSliceSelection,
    DecisionEntryV2 as DecisionEntry,
    DialogAct,
    DialogActResultV2 as DialogActResult,
    EpisodeCardV2 as EpisodeCard,
    PendingFollowUpV2 as PendingFollowUp,
    RoutingClassEnum as RoutingClass,
    RunCardV2 as RunCard,
    StateEntryV2 as StateEntry,
    TaskStateViewV2 as TaskStateView,
    TranscriptEventV2 as TranscriptEvent,
    UserProfileStateV2 as UserProfileState,
    WorkingStateV2 as WorkingState,
)
from .patterns import (
    _ASSISTANT_FOLLOWUP_PATTERNS,
    _CONSTRAINT_PREFIX_RE,
    _NEGATIVE_RESPONSE_PATTERNS,
)
from .pipeline import PipelineInput, PipelineRunner
from .policies import StateFirstContextOSPolicy
from .snapshot import ImmutableSnapshot, SnapshotStore

logger = logging.getLogger(__name__)

# Artifact offloading constants
MAX_INLINE_CHARS: int = 500  # Artifacts larger than this use stubs
MAX_STUB_CHARS: int = 200  # Stub content is capped

if TYPE_CHECKING:
    pass


def _extract_assistant_followup_action(text: str) -> str:
    content = _normalize_text(text)
    if not content:
        return ""
    for pattern in _ASSISTANT_FOLLOWUP_PATTERNS:
        match = pattern.search(content)
        if match is None:
            continue
        action = _normalize_text(match.group("action"))
        if not action:
            continue
        action = re.sub(r"^[,\uFF0C\u3002:\uFF1A;\-\s]+", "", action).strip()
        action = re.sub(r"[?\uFF1F!\uFF01\u3002]+$", "", action).strip()
        if action:
            return _trim_text(action, max_chars=220)
    return ""


def _is_negative_response(text: str) -> bool:
    content = _normalize_text(text)
    if not content:
        return False
    return any(pattern.fullmatch(content) for pattern in _NEGATIVE_RESPONSE_PATTERNS)


def _decision_kind(summary: str) -> str:
    lowered = _normalize_text(summary).lower()
    if not lowered:
        return "decision"
    if any(token in lowered for token in ("plan", "blueprint", "方案", "计划", "蓝图")):
        return "accepted_plan"
    if any(token in lowered for token in ("must", "must not", "do not", "禁止", "不要", "必须")):
        return "constraint"
    if any(token in lowered for token in ("blocked", "阻塞", "等待", "依赖")):
        return "blocked_on"
    return "decision"


def _extract_hard_constraints(working_state: WorkingState) -> tuple[str, ...]:
    values: list[str] = []
    for collection in (
        working_state.user_profile.preferences,
        working_state.user_profile.persistent_facts,
        working_state.task_state.blocked_on,
    ):
        for item in collection:
            if _CONSTRAINT_PREFIX_RE.search(item.value):
                values.append(item.value)
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return tuple(deduped[:6])


class StateFirstContextOS:
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
        self._executor = ThreadPoolExecutor(
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
            from .content_store import ContentStore

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
                self._executor = None  # type: ignore[assignment]

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
        transcript = tuple(
            self._truth_log._normalize_entry(entry) for entry in raw_entries
        )

        # working_state is already deep-copied by WorkingStateManager.current()
        working_state = self._working_state_manager.current()

        # GAP-2 Fix: Deep copy cache entries to prevent shared mutable state
        # The shallow dict() copy leaves values as shared references.
        raw_cache = getattr(self._content_store_cache, "_cache", {})
        cache_snapshot: dict[str, Any] = {
            key: deepcopy(value) for key, value in raw_cache.items()
        }

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

        projection = pipeline_runner.project(inp, adapter_id=self.domain_adapter.adapter_id)

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
        target_episode: EpisodeCard | None = None
        updated_episodes: list[EpisodeCard] = []
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

    def _rebuild_prompt_view(
        self,
        snapshot: ContextOSSnapshot,
    ) -> dict[str, Any]:
        budget_plan = snapshot.budget_plan or self._plan_budget(snapshot.transcript_log, snapshot.artifact_store)
        active_window = self._collect_active_window(
            transcript=snapshot.transcript_log,
            working_state=snapshot.working_state,
            recent_window_messages=self.policy.default_history_window_messages,
            budget_plan=budget_plan,
        )
        artifact_stubs = self._select_artifacts_for_prompt(
            artifacts=snapshot.artifact_store,
            working_state=snapshot.working_state,
        )
        episode_cards = self._select_episodes_for_prompt(
            episodes=snapshot.episode_store,
            working_state=snapshot.working_state,
            focus="",
        )
        run_card = self._build_run_card(working_state=snapshot.working_state)
        context_slice_plan = self._build_context_slice_plan(
            transcript=snapshot.transcript_log,
            active_window=active_window,
            working_state=snapshot.working_state,
            artifact_stubs=artifact_stubs,
            episode_cards=episode_cards,
            budget_plan=budget_plan,
        )
        return {
            "budget_plan": budget_plan,
            "active_window": active_window,
            "artifact_stubs": artifact_stubs,
            "episode_cards": episode_cards,
            "run_card": run_card,
            "context_slice_plan": context_slice_plan,
        }

    def _merge_transcript(
        self,
        *,
        existing: tuple[TranscriptEvent, ...],
        messages: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> tuple[TranscriptEvent, ...]:
        logger.debug(
            "[DEBUG][ContextOS] _merge_transcript start: existing=%d incoming_msgs=%d",
            len(existing),
            len(messages) if messages else 0,
        )
        merged: dict[str, TranscriptEvent] = {item.event_id: item for item in existing}
        next_sequence = max((item.sequence for item in existing), default=-1) + 1

        # First pass: collect all tool_calls from message metadata to emit
        # tool_call events BEFORE the main message processing loop.
        # This ensures tool_call events appear before tool_result events in
        # the transcript, preserving causal ordering.
        pending_tool_calls: list[tuple[int, dict[str, Any], str]] = []  # (sequence, tool_call, source_event_id)
        for _fallback, raw in enumerate(messages or ()):
            if not isinstance(raw, dict):
                continue
            metadata: dict[str, Any] = dict(raw.get("metadata") or {})
            tool_calls = metadata.get("tool_calls")
            if not isinstance(tool_calls, (list, tuple)) or not tool_calls:
                continue

            # Determine sequence for this message's tool calls
            sequence_token = str(raw.get("sequence") or "").strip()
            seq = int(sequence_token) if sequence_token.isdigit() else next_sequence

            source_event_id = str(raw.get("event_id") or "").strip()
            if not source_event_id:
                source_event_id = _event_id(seq, "assistant", "tool_call_batch")

            # Collect tool calls to emit after main loop
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                pending_tool_calls.append((seq, tc, source_event_id))

        # Emit pending tool_call events with sequential integer sequences to maintain ordering.
        # BUG FIX: Previously used float sub-indices (seq + 0.01*idx) which were truncated
        # to int by TranscriptEvent.sequence (int field), making all tool_calls share the
        # same sequence. Now use monotonically increasing integers from next_sequence.
        for _idx, (_seq, tool_call, source_event_id) in enumerate(pending_tool_calls):
            call_sequence = next_sequence
            next_sequence += 1

            tool_name = str(tool_call.get("name") or tool_call.get("tool") or "unknown").strip()
            call_id = str(
                tool_call.get("id") or tool_call.get("call_id") or _event_id(int(call_sequence), "tool_call", tool_name)
            ).strip()
            args = tool_call.get("arguments") or tool_call.get("args") or {}
            if not isinstance(args, dict):
                args = {"raw": str(args)}

            # Create metadata for tool_call event with full tool metadata
            tc_metadata: dict[str, Any] = {
                "tool_name": tool_name,
                "tool_call_id": call_id,
                "tool_args": args,
                "source_event_id": source_event_id,
                "event_kind": "tool_call",
            }
            # Preserve any additional metadata from the original tool call dict
            for key, value in tool_call.items():
                if key not in ("name", "tool", "id", "call_id", "arguments", "args"):
                    tc_metadata[key] = value

            event_id = _event_id(int(call_sequence), "tool_call", f"{tool_name}:{call_id}")
            tc_content = f"tool_call: {tool_name}({args})"
            tc_content_ref = None
            with contextlib.suppress(Exception):
                tc_content_ref = self._get_content_store().intern(tc_content)
            event = TranscriptEvent(
                event_id=event_id,
                sequence=int(call_sequence),
                role="assistant",
                kind="tool_call",
                route="",
                content=tc_content,
                source_turns=(f"t{int(call_sequence)}",),
                artifact_id=None,
                created_at=_utc_now_iso(),
                metadata=tc_metadata,  # type: ignore[arg-type]
                content_ref_hash=tc_content_ref.hash if tc_content_ref else "",
                content_ref_size=int(tc_content_ref.size) if tc_content_ref else 0,
                content_ref_mime=tc_content_ref.mime if tc_content_ref else "",
            )
            merged[event.event_id] = event
            # === Lifecycle: on_event_created ===
            notify = getattr(self.domain_adapter, "on_event_created", None)
            if callable(notify):
                notify(event)
            self._notify_observers("on_event_created", event)

        # Second pass: process all messages (including tool_result from role=tool)
        # BUG FIX: Track which assistant messages already got tool_call events
        # in the first pass to avoid creating duplicate assistant_turn events.
        _assistant_source_ids_with_tool_calls: set[str] = set()
        for _, _, src_id in pending_tool_calls:
            if src_id:
                _assistant_source_ids_with_tool_calls.add(src_id)

        for _fallback, raw in enumerate(messages or ()):
            if not isinstance(raw, dict):
                continue
            role = str(raw.get("role") or "").strip().lower()
            content = _normalize_text(raw.get("content") or raw.get("message") or "")

            # Extract tool_calls metadata before potentially skipping due to empty content
            metadata = dict(raw.get("metadata") or {})
            tool_calls_in_msg = metadata.get("tool_calls")

            # Skip message if no role, no content, AND no tool_calls to emit
            # (tool_call events were already emitted in first pass above)
            if not role or (not content and not tool_calls_in_msg):
                continue

            # BUG FIX: For assistant messages that already had tool_call events
            # emitted in the first pass, only create an event if there is actual
            # text content. Skip bare tool-call wrappers with no meaningful text.
            # This prevents duplicate transcript entries for the same turn.
            if role == "assistant" and tool_calls_in_msg:
                raw_event_id = str(raw.get("event_id") or "").strip()
                source_event_id = raw_event_id or _event_id(
                    int(str(raw.get("sequence") or "0").strip() or "0"),
                    "assistant",
                    "tool_call_batch",
                )
                if source_event_id in _assistant_source_ids_with_tool_calls and not content.strip():
                    continue  # Already emitted as tool_call event, no text content to add

            sequence_token = str(raw.get("sequence") or "").strip()
            if sequence_token.isdigit():
                sequence = int(sequence_token)
                next_sequence = max(next_sequence, sequence + 1)
            else:
                # Keep sequence monotonic across snapshot continuation.
                # Falling back to enumerate() resets to zero and breaks turn recency.
                sequence = next_sequence
                next_sequence += 1

            # SSOT: Extract all metadata fields from incoming event.
            # Preserves kind, route, dialog_act, source_turns, artifact_id, created_at
            # for complete event provenance and ContextOS event sourcing compliance.
            event_id = str(raw.get("event_id") or "").strip() or _event_id(sequence, role, content)
            kind = str(raw.get("kind") or "").strip() or ("tool_result" if role == "tool" else f"{role}_turn")
            route = str(raw.get("route") or "").strip()
            source_turns_raw = raw.get("source_turns")
            if isinstance(source_turns_raw, (list, tuple)) and source_turns_raw:
                source_turns = tuple(str(s) for s in source_turns_raw)
            else:
                source_turns = (f"t{sequence}",)
            artifact_id = raw.get("artifact_id")
            if artifact_id and isinstance(artifact_id, str) and artifact_id.strip():
                artifact_id = artifact_id.strip()
            else:
                artifact_id = None
            created_at = str(raw.get("created_at") or "").strip() or _utc_now_iso()

            # v2.1 dual-write: intern content
            content_ref = None
            if content:
                with contextlib.suppress(Exception):
                    content_ref = self._get_content_store().intern(content)

            event = TranscriptEvent(
                event_id=event_id,
                sequence=sequence,
                role=role,
                kind=kind,
                route=route,
                content=content,
                source_turns=source_turns,
                artifact_id=artifact_id,
                created_at=created_at,
                metadata=metadata,  # type: ignore[arg-type]
                content_ref_hash=content_ref.hash if content_ref else "",
                content_ref_size=int(content_ref.size) if content_ref else 0,
                content_ref_mime=content_ref.mime if content_ref else "",
            )
            merged[event.event_id] = event
            # === Lifecycle: on_event_created ===
            notify = getattr(self.domain_adapter, "on_event_created", None)
            if callable(notify):
                notify(event)
            self._notify_observers("on_event_created", event)
        result = tuple(sorted(merged.values(), key=lambda item: (item.sequence, item.event_id)))
        _role_counts: dict[str, int] = {}
        for item in result:
            _role_counts[item.role] = _role_counts.get(item.role, 0) + 1
        logger.debug(
            "[DEBUG][ContextOS] _merge_transcript end: merged_total=%d roles=%s next_sequence=%d",
            len(result),
            _role_counts,
            next_sequence,
        )
        return result

    def _canonicalize_and_offload(
        self,
        transcript: tuple[TranscriptEvent, ...],
        *,
        existing_artifacts: tuple[ArtifactRecord, ...],
        current_pending_followup: PendingFollowUp | None = None,
    ) -> tuple[
        tuple[TranscriptEvent, ...],
        tuple[ArtifactRecord, ...],
        PendingFollowUp | None,
        dict[str, DomainStatePatchHints],
    ]:
        """Canonicalize transcript events and extract state hints in a single pass.

        Returns:
            Tuple of (updated_transcript, artifacts, pending_followup, state_hints_by_event_id)
            The state_hints_by_event_id maps event_id -> DomainStatePatchHints for efficient
            _patch_working_state by avoiding redundant extract_state_hints calls.
        """
        artifact_by_id = {item.artifact_id: item for item in existing_artifacts}
        updated_events: list[TranscriptEvent] = []
        # OPTIMIZATION: Pre-extract state hints during canonicalization to avoid
        # a second full traversal in _patch_working_state
        state_hints_by_event_id: dict[str, DomainStatePatchHints] = {}

        # Track pending follow-up state
        # IMPORTANT: Only track UNRESOLVED pending follow-ups to prevent
        # resolved follow-ups from continuing to occupy attention
        pending_followup: PendingFollowUp | None = None
        pending_followup_action = ""
        pending_followup_event_id = ""
        pending_followup_sequence = 0

        # Only inherit unresolved pending follow-up from existing snapshot
        if (
            current_pending_followup
            and current_pending_followup.action
            and current_pending_followup.status == "pending"
        ):
            # Only track pending (unresolved) follow-ups
            pending_followup = current_pending_followup
            pending_followup_action = current_pending_followup.action
            pending_followup_event_id = current_pending_followup.source_event_id
            pending_followup_sequence = current_pending_followup.source_sequence
        # else: Resolved follow-ups (confirmed/denied/paused) are NOT tracked

        for item in transcript:
            # Classify dialog act for user and assistant messages
            dialog_act_result: DialogActResult | None = None
            if item.role in ("user", "assistant") and self._dialog_act_classifier is not None:
                dialog_act_result = self._dialog_act_classifier.classify(item.content, role=item.role)

            if item.role == "assistant":
                # Extract follow-up action using generic patterns
                inferred_action = _extract_assistant_followup_action(item.content)

                # === A7: Code-domain follow-up enhancement ===
                # Also check domain adapter for code-specific follow-up classification
                if not inferred_action and hasattr(self.domain_adapter, "classify_assistant_followup"):
                    domain_decision = self.domain_adapter.classify_assistant_followup(
                        item,
                        policy=self.policy,
                    )
                    if domain_decision and domain_decision.reasons:
                        # Extract action from domain-specific reasons
                        for reason in domain_decision.reasons:
                            if reason.startswith("code_followup_"):
                                inferred_action = reason.replace("code_followup_", "")
                                break

                if inferred_action:
                    pending_followup_action = inferred_action
                    pending_followup_event_id = item.event_id
                    pending_followup_sequence = item.sequence

            followup_metadata: dict[str, Any] = {}
            followup_confirmed = False
            dialog_act_resolved = False
            dialog_act: str = DialogAct.UNKNOWN
            resolved_followup_status: str | None = None

            if item.role == "user" and pending_followup_action:
                # Use dialog act classification result
                if dialog_act_result:
                    dialog_act = dialog_act_result.act
                    if dialog_act == DialogAct.AFFIRM:
                        followup_confirmed = True
                        dialog_act_resolved = True
                        resolved_followup_status = "confirmed"
                    elif dialog_act == DialogAct.DENY:
                        dialog_act_resolved = True
                        resolved_followup_status = "denied"
                    elif dialog_act == DialogAct.PAUSE:
                        dialog_act_resolved = True
                        resolved_followup_status = "paused"
                    elif dialog_act == DialogAct.REDIRECT:
                        dialog_act_resolved = True
                        resolved_followup_status = "redirected"

                # Fallback to pattern matching if dialog act not available
                if not dialog_act_resolved:
                    if _is_affirmative_response(item.content):
                        followup_confirmed = True
                        dialog_act = DialogAct.AFFIRM
                        dialog_act_resolved = True
                        resolved_followup_status = "confirmed"
                    elif _is_negative_response(item.content):
                        dialog_act = DialogAct.DENY
                        dialog_act_resolved = True
                        resolved_followup_status = "denied"

                if dialog_act_resolved:
                    followup_metadata = {
                        "followup_action": pending_followup_action,
                        "followup_confirmed": str(followup_confirmed).lower(),
                        "followup_source_sequence": str(pending_followup_sequence),
                        "dialog_act": dialog_act,
                    }
                    # Update pending follow-up with resolution
                    # Use validated status (only valid values: pending|confirmed|denied|paused|redirected|expired)
                    final_status = (
                        resolved_followup_status
                        if resolved_followup_status in ("confirmed", "denied", "paused", "redirected", "expired")
                        else "expired"
                    )
                    pending_followup = PendingFollowUp(
                        action=pending_followup_action,
                        source_event_id=pending_followup_event_id,
                        source_sequence=pending_followup_sequence,
                        status=final_status,
                        updated_at=_utc_now_iso(),
                    )
                    # === Lifecycle: on_pending_followup_resolved ===
                    notify = getattr(self.domain_adapter, "on_pending_followup_resolved", None)
                    if callable(notify):
                        notify(pending_followup)
                    self._notify_observers("on_pending_followup_resolved", pending_followup)
                    # Clear local variables after successful resolution
                    pending_followup_action = ""
                    pending_followup_event_id = ""
                    pending_followup_sequence = 0
                else:
                    # User responded but dialog act was not recognized - mark as expired
                    # to prevent deadlock (pending follow-up never getting resolved)
                    if pending_followup and pending_followup.status == "pending":
                        pending_followup = PendingFollowUp(
                            action=pending_followup.action,
                            source_event_id=pending_followup.source_event_id,
                            source_sequence=pending_followup.source_sequence,
                            status="expired",
                            updated_at=_utc_now_iso(),
                        )
                        # === Lifecycle: on_pending_followup_resolved ===
                        notify = getattr(self.domain_adapter, "on_pending_followup_resolved", None)
                        if callable(notify):
                            notify(pending_followup)
                        self._notify_observers("on_pending_followup_resolved", pending_followup)
                    # Clear local variables
                    pending_followup_action = ""
                    pending_followup_event_id = ""
                    pending_followup_sequence = 0

            forced_route = str(get_metadata_value(item.metadata, "forced_route") or "").strip().lower()
            if forced_route:
                route = forced_route
                decision_metadata = {}
                routing_confidence = _clamp_confidence(
                    get_metadata_value(item.metadata, "routing_confidence"),
                    default=1.0,
                )
                routing_reasons = tuple(
                    str(value).strip()
                    for value in (get_metadata_value(item.metadata, "routing_reasons") or [])
                    if str(value).strip()
                ) or ("manual_reclassification",)
            elif followup_confirmed:
                route = RoutingClass.PATCH
                decision_metadata = dict(followup_metadata)
                routing_confidence = 0.94
                routing_reasons = ("assistant_followup_confirmation",)
            else:
                decision = self.domain_adapter.classify_event(item, policy=self.policy)
                route = decision.route or RoutingClass.SUMMARIZE
                decision_metadata = {
                    **dict(decision.metadata),
                    **followup_metadata,
                }
                routing_confidence = _clamp_confidence(decision.confidence, default=0.5)
                routing_reasons = tuple(decision.reasons or ())
            artifact_id = item.artifact_id if route == RoutingClass.ARCHIVE else None
            if route == RoutingClass.ARCHIVE:
                artifact_id = artifact_id or _artifact_id(item.content)
                existing_artifact = artifact_by_id.get(artifact_id)
                if existing_artifact is None or (not existing_artifact.content and item.content):
                    artifact = self.domain_adapter.build_artifact(
                        item,
                        artifact_id=artifact_id,
                        policy=self.policy,
                    )
                    if artifact is not None:
                        artifact_by_id[artifact_id] = artifact
                        # === Lifecycle: on_artifact_built ===
                        notify = getattr(self.domain_adapter, "on_artifact_built", None)
                        if callable(notify):
                            notify(artifact)
                        self._notify_observers("on_artifact_built", artifact)
            # Build dialog act metadata if available
            dialog_act_metadata: dict[str, Any] = {}
            if dialog_act_result is not None:
                dialog_act_metadata = {
                    "dialog_act": dialog_act_result.act,
                    "dialog_act_confidence": dialog_act_result.confidence,
                    "dialog_act_triggers": list(dialog_act_result.triggers),
                    "dialog_act_is_high_priority": DialogAct.is_high_priority(dialog_act_result.act),
                }
            updated_events.append(
                validated_replace(
                    item,
                    route=route,
                    artifact_id=artifact_id,
                    metadata={
                        **dict(item.metadata),
                        **decision_metadata,
                        **dialog_act_metadata,
                        "routing_confidence": routing_confidence,
                        "routing_reasons": list(routing_reasons),
                        "routing_adapter_id": self.domain_adapter.adapter_id,
                    },
                )
            )

            # OPTIMIZATION: Pre-extract state hints for non-CLEAR events.
            # This avoids a second full transcript traversal in _patch_working_state.
            # Skip CLEAR events (they don't contribute to working state per _patch_working_state).
            if route != RoutingClass.CLEAR:
                hints = self.domain_adapter.extract_state_hints(updated_events[-1])
                if hints is not None:
                    state_hints_by_event_id[updated_events[-1].event_id] = hints

        # Handle unresolved pending follow-up (created but not yet responded)
        # If we have a pending action but it wasn't resolved in this turn
        if pending_followup_action and not pending_followup:
            pending_followup = PendingFollowUp(
                action=pending_followup_action,
                source_event_id=pending_followup_event_id,
                source_sequence=pending_followup_sequence,
                status="pending",
                updated_at=_utc_now_iso(),
            )

        artifacts = tuple(sorted(artifact_by_id.values(), key=lambda item: item.artifact_id))
        _route_dist: dict[str, int] = {}
        for evt in updated_events:
            _route_dist[evt.route] = _route_dist.get(evt.route, 0) + 1
        logger.debug(
            "[DEBUG][ContextOS] _canonicalize_and_offload end: events=%d artifacts=%d pending=%s routes=%s hints=%d",
            len(updated_events),
            len(artifacts),
            pending_followup.status if pending_followup else "none",
            _route_dist,
            len(state_hints_by_event_id),
        )
        return tuple(updated_events), artifacts, pending_followup, state_hints_by_event_id

    def _patch_working_state(
        self,
        transcript: tuple[TranscriptEvent, ...],
        artifacts: tuple[ArtifactRecord, ...],
        precomputed_hints: dict[str, DomainStatePatchHints] | None = None,
    ) -> WorkingState:
        """Build WorkingState from transcript.

        Args:
            transcript: Canonicalized transcript events.
            artifacts: Artifact records from canonicalization.
            precomputed_hints: Optional pre-extracted state hints from _canonicalize_and_offload.
                If provided, skips redundant extract_state_hints calls for O(n) -> O(1) lookup per event.
        """
        acc = _StateAccumulator()
        decisions: list[DecisionEntry] = []
        last_decision_by_kind: dict[str, DecisionEntry] = {}
        current_goal_candidates: list[StateEntry] = []
        accepted_plan: list[StateEntry] = []
        open_loops: list[StateEntry] = []
        blocked_on: list[StateEntry] = []
        deliverables: list[StateEntry] = []
        preferences: list[StateEntry] = []
        style: list[StateEntry] = []
        persistent_facts: list[StateEntry] = []
        temporal_facts: list[StateEntry] = []
        active_entities: list[StateEntry] = []

        for item in transcript:
            if item.route == RoutingClass.CLEAR:
                continue
            turns = item.source_turns or (f"t{item.sequence}",)
            # OPTIMIZATION: Use pre-computed hints if available (from _canonicalize_and_offload)
            # to avoid redundant extract_state_hints calls
            if precomputed_hints is not None and item.event_id in precomputed_hints:
                hints = precomputed_hints[item.event_id]
            else:
                hints = self.domain_adapter.extract_state_hints(item)
            for value in hints.goals:
                entry = acc.add(path="task_state.current_goal", value=value, source_turns=turns, confidence=0.96)
                if entry is not None:
                    current_goal_candidates.append(entry)
            for value in hints.accepted_plan:
                entry = acc.add(
                    path=f"task_state.accepted_plan::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.90,
                )
                if entry is not None:
                    accepted_plan.append(entry)
            for value in hints.open_loops:
                entry = acc.add(
                    path=f"task_state.open_loops::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.88,
                )
                if entry is not None:
                    open_loops.append(entry)
            for value in hints.blocked_on:
                entry = acc.add(
                    path=f"task_state.blocked_on::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.84,
                )
                if entry is not None:
                    blocked_on.append(entry)
            for value in hints.deliverables:
                entry = acc.add(
                    path=f"task_state.deliverables::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.82,
                )
                if entry is not None:
                    deliverables.append(entry)
            for value in hints.preferences:
                entry = acc.add(
                    path=f"user_profile.preferences::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.86,
                )
                if entry is not None:
                    preferences.append(entry)
            for value in hints.style:
                entry = acc.add(
                    path=f"user_profile.style::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.86,
                )
                if entry is not None:
                    style.append(entry)
            for value in hints.temporal_facts:
                entry = acc.add(
                    path=f"temporal_facts::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.80,
                )
                if entry is not None:
                    temporal_facts.append(entry)
            for value in hints.entities:
                entry = acc.add(
                    path=f"active_entities::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.78,
                )
                if entry is not None:
                    active_entities.append(entry)
            for value in hints.persistent_facts:
                entry = acc.add(
                    path=f"user_profile.persistent_facts::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.80,
                )
                if entry is not None:
                    persistent_facts.append(entry)
            for summary in hints.decisions:
                key = summary.lower()
                if not any(existing.summary.lower() == key for existing in decisions):
                    kind = _decision_kind(summary)
                    previous = last_decision_by_kind.get(kind)
                    decision = DecisionEntry(
                        decision_id=f"dec_{len(decisions) + 1}",
                        summary=summary,
                        source_turns=turns,
                        updated_at=_utc_now_iso(),
                        kind=kind,
                        supersedes=previous.decision_id if previous is not None else None,
                        basis_refs=tuple(item.value for item in active_entities[-2:]),
                    )
                    decisions.append(decision)
                    last_decision_by_kind[kind] = decision

        active_artifacts = tuple(item.artifact_id for item in artifacts[-self.policy.max_artifact_stubs :])

        # Build deduped active lists first so we can compute active_entry_ids
        deduped_preferences = _dedupe_state_entries(preferences, limit=self.policy.max_open_loops)
        deduped_style = _dedupe_state_entries(style, limit=self.policy.max_open_loops)
        deduped_persistent_facts = _dedupe_state_entries(persistent_facts, limit=self.policy.max_stable_facts)
        deduped_accepted_plan = _dedupe_state_entries(accepted_plan, limit=self.policy.max_open_loops)
        deduped_open_loops = _dedupe_state_entries(open_loops, limit=self.policy.max_open_loops)
        deduped_blocked_on = _dedupe_state_entries(blocked_on, limit=self.policy.max_open_loops)
        deduped_deliverables = _dedupe_state_entries(deliverables, limit=self.policy.max_open_loops)
        deduped_active_entities = _dedupe_state_entries(active_entities, limit=self.policy.max_stable_facts)
        deduped_temporal_facts = _dedupe_state_entries(temporal_facts, limit=self.policy.max_stable_facts)

        # Collect IDs of all entries currently in active lists
        active_entry_ids: set[str] = set()
        for entry in (
            deduped_preferences
            + deduped_style
            + deduped_persistent_facts
            + deduped_accepted_plan
            + deduped_open_loops
            + deduped_blocked_on
            + deduped_deliverables
            + deduped_active_entities
            + deduped_temporal_facts
        ):
            if hasattr(entry, "entry_id"):
                active_entry_ids.add(entry.entry_id)
        if current_goal_candidates:
            goal = current_goal_candidates[-1]
            if hasattr(goal, "entry_id"):
                active_entry_ids.add(goal.entry_id)

        # state_history: only keep superseded entries NOT in active lists
        # (avoids duplicating all active entries in history)
        state_history = tuple(
            e
            for e in acc.entries
            if getattr(e, "entry_id", "") not in active_entry_ids and getattr(e, "supersedes", None) is not None
        )

        working_state = WorkingState(
            user_profile=UserProfileState(
                preferences=deduped_preferences,
                style=deduped_style,
                persistent_facts=deduped_persistent_facts,
            ),
            task_state=TaskStateView(
                current_goal=current_goal_candidates[-1] if current_goal_candidates else None,
                accepted_plan=deduped_accepted_plan,
                open_loops=deduped_open_loops,
                blocked_on=deduped_blocked_on,
                deliverables=deduped_deliverables,
            ),
            decision_log=tuple(decisions[-self.policy.max_decisions :]),
            active_entities=deduped_active_entities,
            active_artifacts=active_artifacts,
            temporal_facts=deduped_temporal_facts,
            state_history=state_history,
        )
        logger.debug(
            "[DEBUG][ContextOS] _patch_working_state: goal=%r open_loops=%d blocked=%d decisions=%d active_entities=%d artifacts=%d",
            working_state.task_state.current_goal.value if working_state.task_state.current_goal else "<none>",
            len(working_state.task_state.open_loops),
            len(working_state.task_state.blocked_on),
            len(working_state.decision_log),
            len(working_state.active_entities),
            len(working_state.active_artifacts),
        )

        # === Hook: on_context_patched ===
        # Call registered hooks after working state is patched
        try:
            hook_manager = self._get_hook_manager()
            hook_manager.on_context_patched(
                working_state=working_state,
                transcript=transcript,
            )
        except (RuntimeError, ValueError) as e:
            logger.debug("Hook on_context_patched raised exception (ignored): %s", e)

        return working_state

    def _plan_budget(
        self,
        transcript: tuple[TranscriptEvent, ...],
        artifacts: tuple[ArtifactRecord, ...],
    ) -> BudgetPlan:
        # Use resolved context window (LLM Config > Hard-coded Table > Policy Default)
        window = max(4096, self.resolved_context_window)
        # Claude Code formula: output_reserve = max(max_expected_output, 0.18C)
        # output_reserve_min serves as max_expected_output (configurable floor)
        # ratio_based is 0.18*C as ceiling
        ratio_based = int(window * self.policy.output_reserve_ratio)  # 0.18 * C
        output_reserve = max(self.policy.output_reserve_min, ratio_based)
        tool_reserve = max(
            self.policy.tool_reserve_min,
            int(window * self.policy.tool_reserve_ratio),
        )
        # Claude Code formula: safety_margin = max(2048, 0.05C)
        safety_margin = max(self.policy.safety_margin_min, int(window * self.policy.safety_margin_ratio))
        input_budget = max(1024, window - output_reserve - tool_reserve - safety_margin)
        retrieval_budget = min(
            max(256, int(input_budget * self.policy.retrieval_ratio)),
            max(256, int(self.policy.planned_retrieval_tokens)),
        )
        current_input_tokens = sum(_estimate_tokens(item.content) for item in transcript)
        current_input_tokens += sum(min(item.token_count, 128) for item in artifacts)
        expected_next_input_tokens = (
            current_input_tokens + int(self.policy.p95_tool_result_tokens) + retrieval_budget + output_reserve
        )
        # A11 Fix: Validate expected_next_input_tokens doesn't exceed model_context_window
        validation_error = ""
        if expected_next_input_tokens > window:
            overrun = expected_next_input_tokens - window
            validation_error = (
                f"BudgetPlan invariant violated: expected_next_input_tokens "
                f"({expected_next_input_tokens}) exceeds model_context_window "
                f"({window}) by {overrun} tokens"
            )
        plan = BudgetPlan(
            model_context_window=window,
            output_reserve=output_reserve,
            tool_reserve=tool_reserve,
            safety_margin=safety_margin,
            input_budget=input_budget,
            retrieval_budget=retrieval_budget,
            soft_limit=max(512, int(input_budget * 0.55)),
            hard_limit=max(768, int(input_budget * 0.72)),
            emergency_limit=max(1024, int(input_budget * 0.85)),
            current_input_tokens=current_input_tokens,
            expected_next_input_tokens=expected_next_input_tokens,
            p95_tool_result_tokens=int(self.policy.p95_tool_result_tokens),
            planned_retrieval_tokens=int(self.policy.planned_retrieval_tokens),
            validation_error=validation_error,
        )
        logger.debug(
            "[DEBUG][ContextOS] _plan_budget: window=%d input=%d soft=%d hard=%d emergency=%d expected=%d current=%d",
            plan.model_context_window,
            plan.input_budget,
            plan.soft_limit,
            plan.hard_limit,
            plan.emergency_limit,
            plan.expected_next_input_tokens,
            plan.current_input_tokens,
        )
        return plan

    def _collect_active_window(
        self,
        *,
        transcript: tuple[TranscriptEvent, ...],
        working_state: WorkingState,
        recent_window_messages: int,
        budget_plan: BudgetPlan,
    ) -> tuple[TranscriptEvent, ...]:
        if not transcript:
            return ()
        min_recent_floor = max(1, int(self.policy.min_recent_messages_pinned or 1))
        min_recent_floor = min(self.policy.max_active_window_messages, min_recent_floor)
        recent_limit = max(min_recent_floor, int(recent_window_messages or 1))
        recent_limit = max(1, min(self.policy.max_active_window_messages, recent_limit))
        recent_candidates = list(transcript[-recent_limit:])
        forced_recent_ids = {item.event_id for item in transcript[-min_recent_floor:]}
        pinned_sequences: set[int] = {item.sequence for item in recent_candidates}
        for entry in (
            [working_state.task_state.current_goal] if working_state.task_state.current_goal is not None else []
        ):
            pinned_sequences.update(self._sequences_from_turns(entry.source_turns))
        for collection in (
            working_state.task_state.accepted_plan,
            working_state.task_state.open_loops,
            working_state.task_state.blocked_on,
            working_state.task_state.deliverables,
            working_state.active_entities,
        ):
            for entry in collection:
                pinned_sequences.update(self._sequences_from_turns(entry.source_turns))
        active_artifact_ids = set(working_state.active_artifacts)
        pinned_events: dict[str, TranscriptEvent] = {}
        # Use policy-based allocation ratio instead of hard-coded 0.45 (T3-6)
        active_window_ratio = getattr(self.policy, "active_window_budget_ratio", 0.45)
        token_budget = max(512, min(budget_plan.soft_limit, int(budget_plan.input_budget * active_window_ratio)))
        token_count = 0
        for item in reversed(transcript):
            if item.route == RoutingClass.CLEAR and item.event_id not in forced_recent_ids:
                continue
            is_reopened = bool(str(get_metadata_value(item.metadata, "reopen_hold") or "").strip())
            is_root = (
                item.sequence in pinned_sequences
                or (item.artifact_id in active_artifact_ids)
                or is_reopened
                or item.event_id in forced_recent_ids
            )
            can_add = is_root or len(pinned_events) < self.policy.max_active_window_messages
            if not can_add:
                continue
            item_content = item.content
            estimated = _estimate_tokens(item_content)
            # Root items that exceed budget: truncate content instead of skipping
            if token_count + estimated > token_budget and is_root:
                # Truncate to fit within remaining budget using token-consistent formula.
                # Use iterative truncation: start with ASCII-friendly estimate and verify
                # against _estimate_tokens (ascii_chars/4 + cjk_chars*1.5) until token count fits.
                remaining_budget = token_budget - token_count
                remaining_chars = max(512, remaining_budget * 4)  # ASCII: 1 token ≈ 4 chars
                if remaining_chars < len(item_content):
                    item_content = _trim_text(item_content, max_chars=remaining_chars)
                    truncated_tokens = _estimate_tokens(item_content)
                    # Iterate if token estimate still exceeds budget (handles CJK text)
                    while truncated_tokens > remaining_budget and remaining_chars > 128:
                        remaining_chars = int(remaining_chars * 0.8)
                        item_content = _trim_text(item_content, max_chars=remaining_chars)
                        truncated_tokens = _estimate_tokens(item_content)
                    logger.warning(
                        "Root event content truncated due to token budget: event_id=%s, "
                        "original_tokens=%d, truncated_to=%d, token_budget=%d",
                        item.event_id,
                        estimated,
                        truncated_tokens,
                        token_budget,
                    )
                estimated = _estimate_tokens(item_content)
            if token_count + estimated > token_budget:
                if is_root:
                    # Root items still over budget even after truncation - log warning and add anyway
                    logger.warning(
                        "Token budget exceeded for root event (after truncation): event_id=%s, "
                        "sequence=%d, estimated_tokens=%d, current_tokens=%d, token_budget=%d",
                        item.event_id,
                        item.sequence,
                        estimated,
                        token_count,
                        token_budget,
                    )
                else:
                    # Non-root events are skipped when over budget
                    logger.debug(
                        "Skipping non-root event due to token budget: event_id=%s, sequence=%d, "
                        "estimated_tokens=%d, current_tokens=%d, token_budget=%d",
                        item.event_id,
                        item.sequence,
                        estimated,
                        token_count,
                        token_budget,
                    )
                    continue
            if item.event_id in pinned_events:
                continue

            # Use truncated content if applicable
            pinned_item = replace(item, content=item_content) if item_content != item.content else item
            pinned_events[item.event_id] = pinned_item
            token_count += estimated
        result = tuple(sorted(pinned_events.values(), key=lambda item: (item.sequence, item.event_id)))
        logger.debug(
            "[DEBUG][ContextOS] _collect_active_window: recent_limit=%d pinned=%d token_count=%d/%d budget=%s",
            recent_limit,
            len(result),
            token_count,
            token_budget,
            budget_plan.input_budget if budget_plan else 0,
        )
        return result

    def _seal_closed_episodes(
        self,
        *,
        transcript: tuple[TranscriptEvent, ...],
        active_window: tuple[TranscriptEvent, ...],
        artifacts: tuple[ArtifactRecord, ...],
        working_state: WorkingState,
        existing_episodes: tuple[EpisodeCard, ...],
        pending_followup: PendingFollowUp | None = None,
    ) -> tuple[EpisodeCard, ...]:
        active_ids = {item.event_id for item in active_window}
        last_sealed_sequence = max(
            (item.to_sequence for item in existing_episodes if item.status == "sealed"),
            default=-1,
        )
        closed_events = tuple(
            item
            for item in transcript
            if item.route != RoutingClass.CLEAR
            and item.sequence > last_sealed_sequence
            and item.event_id not in active_ids
            and not str(get_metadata_value(item.metadata, "reopen_hold") or "").strip()
        )

        # === Seal Guard: Block sealing if pending follow-up exists ===
        if (
            self.policy.enable_seal_guard
            and self.policy.prevent_seal_on_pending
            and pending_followup
            and pending_followup.status == "pending"
        ):
            # Always emit seal_blocked event - this is a critical security guard behavior
            # It should NOT be gated by enable_attention_trace
            emit_debug_event(
                category="attention",
                label="seal_blocked",
                source="context_os.runtime",
                payload={
                    "reason": "pending_followup_unresolved",
                    "pending_action": pending_followup.action,
                    "pending_status": pending_followup.status,
                },
            )
            return existing_episodes

        if not self.domain_adapter.should_seal_episode(
            closed_events=closed_events,
            active_window=active_window,
            working_state=working_state,
        ):
            return existing_episodes
        if not closed_events:
            return existing_episodes

        # === Hook: on_before_episode_sealed ===
        # Call registered hooks before episode is sealed
        try:
            hook_manager = self._get_hook_manager()
            hook_results = hook_manager.on_before_episode_sealed(
                episode_events=closed_events,
                working_state=working_state,
            )
            # Check if any hook vetoed the sealing
            for result in hook_results:
                if isinstance(result, dict) and result.get("should_veto"):
                    logger.debug(
                        "Episode sealing vetoed by hook: %s",
                        result.get("veto_reason", "unknown reason"),
                    )
                    return existing_episodes
        except (RuntimeError, ValueError) as e:
            logger.debug("Hook on_before_episode_sealed raised exception (ignored): %s", e)
        artifact_ids = tuple(
            item.artifact_id
            for item in closed_events
            if item.artifact_id and any(artifact.artifact_id == item.artifact_id for artifact in artifacts)
        )
        combined = "\n".join(item.content for item in closed_events)
        intent = (
            working_state.task_state.current_goal.value
            if working_state.task_state.current_goal is not None
            else _trim_text(closed_events[0].content, max_chars=96)
        )
        outcome = (
            working_state.decision_log[-1].summary
            if working_state.decision_log
            else _trim_text(closed_events[-1].content, max_chars=160)
        )
        episode = EpisodeCard(
            episode_id=f"ep_{len(existing_episodes) + 1}",
            from_sequence=closed_events[0].sequence,
            to_sequence=closed_events[-1].sequence,
            intent=intent,
            outcome=outcome,
            decisions=tuple(item.summary for item in working_state.decision_log[-self.policy.max_decisions :]),
            facts=tuple(
                item.value for item in working_state.user_profile.persistent_facts[-self.policy.max_stable_facts :]
            ),
            artifact_refs=tuple(dict.fromkeys(artifact_ids)),
            entities=tuple(item.value for item in working_state.active_entities[-self.policy.max_stable_facts :]),
            reopen_conditions=tuple(
                item.value for item in working_state.task_state.open_loops[-self.policy.max_open_loops :]
            ),
            source_spans=(f"t{closed_events[0].sequence}:t{closed_events[-1].sequence}",),
            digest_64=_trim_text(combined, max_chars=64),
            digest_256=_trim_text(combined, max_chars=256),
            digest_1k=_trim_text(combined, max_chars=1000),
            sealed_at=time.time(),
            status="sealed",
        )
        # === Lifecycle: on_episode_sealed ===
        notify = getattr(self.domain_adapter, "on_episode_sealed", None)
        if callable(notify):
            notify(episode)
        self._notify_observers("on_episode_sealed", episode)
        return (*tuple(existing_episodes), episode)

    def _truncate_artifact_if_needed(self, artifact: ArtifactRecord) -> ArtifactRecord:
        """Truncate artifact content if it exceeds MAX_INLINE_CHARS.

        Args:
            artifact: The artifact record to potentially truncate.

        Returns:
            The original artifact if small enough, or a new artifact with
            truncated content and updated metadata.
        """
        if len(artifact.content) <= MAX_INLINE_CHARS:
            return artifact
        stub_content = (
            artifact.content[:MAX_STUB_CHARS] + f"\n...[truncated, full content at {artifact.artifact_id}]..."
        )
        return replace(
            artifact,
            content=stub_content,
            metadata={**dict(artifact.metadata or {}), "truncated": True, "full_id": artifact.artifact_id},  # type: ignore[arg-type]
        )

    def _select_artifacts_for_prompt(
        self,
        *,
        artifacts: tuple[ArtifactRecord, ...],
        working_state: WorkingState,
    ) -> tuple[ArtifactRecord, ...]:
        """Select artifacts for prompt injection.

        Implements offloading: large artifacts (>MAX_INLINE_CHARS) are replaced
        with stubs that reference external storage.
        """
        if not artifacts:
            return ()
        active_ids = set(working_state.active_artifacts)
        ordered: list[ArtifactRecord] = []
        seen: set[str] = set()

        # First add active artifacts (always include)
        for artifact_id in active_ids:
            artifact = next((item for item in artifacts if item.artifact_id == artifact_id), None)
            if artifact is not None and artifact.artifact_id not in seen:
                seen.add(artifact.artifact_id)
                ordered.append(self._truncate_artifact_if_needed(artifact))

        # Add remaining artifacts up to max limit
        for artifact in reversed(artifacts):
            if len(ordered) >= self.policy.max_artifact_stubs:
                break
            if artifact.artifact_id in seen:
                continue
            seen.add(artifact.artifact_id)
            ordered.append(self._truncate_artifact_if_needed(artifact))

        # Add remaining artifacts up to max limit
        for artifact in reversed(artifacts):
            if len(ordered) >= self.policy.max_artifact_stubs:
                break
            if artifact.artifact_id in seen:
                continue
            seen.add(artifact.artifact_id)
            ordered.append(self._truncate_artifact_if_needed(artifact))

        return tuple(ordered[: self.policy.max_artifact_stubs])

    def _select_episodes_for_prompt(
        self,
        *,
        episodes: tuple[EpisodeCard, ...],
        working_state: WorkingState,
        focus: str,
    ) -> tuple[EpisodeCard, ...]:
        if not episodes:
            return ()
        focus_terms = {
            token.lower() for token in re.findall(r"[A-Za-z0-9_.:/\\-]+", _normalize_text(focus)) if len(token) >= 2
        }
        ranked: list[tuple[float, EpisodeCard]] = []

        # T6-6 Fix: Use max sequence in the entire episode store as denominator
        # This fixes recency calculation for reopened episodes where episodes[-1].to_sequence
        # might not be the true maximum (reopened episodes have higher sequence numbers)
        max_seq = max((ep.to_sequence for ep in episodes), default=1)

        for episode in episodes:
            if episode.status != "sealed":
                continue
            text = " ".join((episode.intent, episode.outcome, episode.digest_256)).lower()
            lexical = 0.0
            if focus_terms:
                lexical = sum(1 for term in focus_terms if term in text) / max(1, len(focus_terms))
            # Use global max_seq instead of episodes[-1].to_sequence
            recency = max(0.0, episode.to_sequence / max(1, max_seq))
            open_loop_bonus = (
                0.25 if any(loop.value.lower() in text for loop in working_state.task_state.open_loops) else 0.0
            )
            ranked.append((lexical + recency + open_loop_bonus, episode))
        ranked.sort(key=lambda item: (item[0], item[1].to_sequence), reverse=True)
        return tuple(item[1] for item in ranked[: self.policy.max_episode_cards])

    def _build_run_card(
        self,
        *,
        working_state: WorkingState,
        transcript: tuple[TranscriptEvent, ...] | None = None,
        pending_followup: PendingFollowUp | None = None,
    ) -> RunCard:
        current_goal = (
            working_state.task_state.current_goal.value if working_state.task_state.current_goal is not None else ""
        )
        open_loops = tuple(item.value for item in working_state.task_state.open_loops[-self.policy.max_open_loops :])
        active_entities = tuple(item.value for item in working_state.active_entities[: self.policy.max_stable_facts])
        recent_decisions = tuple(item.summary for item in working_state.decision_log[-self.policy.max_decisions :])
        next_action_hint = ""
        if open_loops:
            next_action_hint = open_loops[-1]
        elif working_state.task_state.deliverables:
            next_action_hint = working_state.task_state.deliverables[0].value

        # === Run Card v2: Extract attention runtime fields ===
        latest_user_intent = ""
        last_turn_outcome = ""
        latest_user_event: TranscriptEvent | None = None
        if transcript:
            ordered_transcript = sorted(
                transcript,
                key=lambda item: (item.sequence, item.created_at, item.event_id),
            )
            # Find the latest user turn for intent
            for event in reversed(ordered_transcript):
                if event.role == "user":
                    latest_user_event = event
                    latest_user_intent = event.content
                    break
            # Determine last_turn_outcome from the most recent meaningful event
            # (assistant response, tool execution, or user dialog act), not just
            # the user turn. Fixes the "unknown" freeze for open-ended tasks.
            for event in reversed(ordered_transcript):
                if event.role == "assistant":
                    last_turn_outcome = str(get_metadata_value(event.metadata, "dialog_act") or "assistant_response")
                    break
                elif event.role == "tool":
                    last_turn_outcome = "tool_execution"
                    break
                elif event.role == "user":
                    last_turn_outcome = str(get_metadata_value(event.metadata, "dialog_act") or DialogAct.UNKNOWN)
                    break

        visible_followup: PendingFollowUp | None = pending_followup
        if visible_followup is not None and visible_followup.status != "pending":
            # Keep resolved follow-up visible only on the exact resolving turn.
            # Subsequent turns should not keep stale follow-up fields in run card.
            latest_resolved_now = bool(
                latest_user_event
                and str(get_metadata_value(latest_user_event.metadata, "followup_action") or "").strip()
            )
            if not latest_resolved_now:
                visible_followup = None

        run_card = RunCard(
            current_goal=current_goal,
            hard_constraints=_extract_hard_constraints(working_state),
            open_loops=open_loops,
            active_entities=active_entities,
            active_artifacts=tuple(working_state.active_artifacts),
            recent_decisions=recent_decisions,
            next_action_hint=next_action_hint,
            # Run Card v2 fields
            latest_user_intent=latest_user_intent,
            pending_followup_action=visible_followup.action if visible_followup else "",
            pending_followup_status=visible_followup.status if visible_followup else "",
            last_turn_outcome=last_turn_outcome,
        )
        logger.debug(
            "[DEBUG][ContextOS] _build_run_card: goal=%r open_loops=%d decisions=%d last_outcome=%r pending=%s",
            current_goal,
            len(open_loops),
            len(recent_decisions),
            last_turn_outcome,
            visible_followup.status if visible_followup else "none",
        )
        return run_card

    def _build_context_slice_plan(
        self,
        *,
        transcript: tuple[TranscriptEvent, ...],
        active_window: tuple[TranscriptEvent, ...],
        working_state: WorkingState,
        artifact_stubs: tuple[ArtifactRecord, ...],
        episode_cards: tuple[EpisodeCard, ...],
        budget_plan: BudgetPlan,
    ) -> ContextSlicePlan:
        included: list[ContextSliceSelection] = []
        excluded: list[ContextSliceSelection] = []
        roots = [
            "latest_user_turn",
            "current_goal",
            "open_loops",
        ]
        if _extract_hard_constraints(working_state):
            roots.append("hard_constraints")
        if artifact_stubs:
            roots.append("active_artifacts")

        if working_state.task_state.current_goal is not None:
            included.append(
                ContextSliceSelection(
                    selection_type="state",
                    ref="task_state.current_goal",
                    reason="root",
                )
            )
        for open_loop_entry in working_state.task_state.open_loops[-self.policy.max_open_loops :]:
            included.append(
                ContextSliceSelection(
                    selection_type="state",
                    ref=open_loop_entry.path,
                    reason="open_loop",
                )
            )
        for artifact_stub in artifact_stubs:
            included.append(
                ContextSliceSelection(
                    selection_type="artifact",
                    ref=artifact_stub.artifact_id,
                    reason="active_artifact"
                    if artifact_stub.artifact_id in working_state.active_artifacts
                    else "recent_artifact",
                )
            )
        for episode_card in episode_cards:
            included.append(
                ContextSliceSelection(
                    selection_type="episode",
                    ref=episode_card.episode_id,
                    reason="episode_recall",
                )
            )
        for window_event in active_window:
            reason = "recent_window"
            if window_event.sequence == max((event.sequence for event in active_window), default=window_event.sequence):
                reason = "latest_turn"
            elif (
                working_state.task_state.current_goal is not None
                and window_event.sequence
                in self._sequences_from_turns(working_state.task_state.current_goal.source_turns)
            ):
                reason = "goal_root"
            included.append(
                ContextSliceSelection(
                    selection_type="event",
                    ref=window_event.event_id,
                    reason=reason,
                )
            )

        active_ids = {window_event.event_id for window_event in active_window}
        included_refs = {item.ref for item in included}
        for transcript_event in transcript:
            if transcript_event.event_id in active_ids:
                continue
            if transcript_event.route == RoutingClass.CLEAR:
                excluded.append(
                    ContextSliceSelection(
                        selection_type="event",
                        ref=transcript_event.event_id,
                        reason="low_signal",
                    )
                )
                continue
            if transcript_event.artifact_id and transcript_event.artifact_id not in included_refs:
                excluded.append(
                    ContextSliceSelection(
                        selection_type="artifact",
                        ref=transcript_event.artifact_id,
                        reason="closed_and_unreachable",
                    )
                )
            else:
                excluded.append(
                    ContextSliceSelection(
                        selection_type="event",
                        ref=transcript_event.event_id,
                        reason="inactive_history",
                    )
                )

        pressure_level = "normal"
        if budget_plan.expected_next_input_tokens >= budget_plan.emergency_limit:
            pressure_level = "emergency"
        elif budget_plan.expected_next_input_tokens >= budget_plan.hard_limit:
            pressure_level = "hard"
        elif budget_plan.expected_next_input_tokens >= budget_plan.soft_limit:
            pressure_level = "soft"

        plan = ContextSlicePlan(
            plan_id=f"slice_{hashlib.sha256('|'.join(included_refs or {'empty'}).encode('utf-8')).hexdigest()[:10]}",
            budget_tokens=budget_plan.input_budget,
            roots=tuple(roots),
            included=tuple(included),
            excluded=tuple(excluded[: max(12, self.policy.max_active_window_messages)]),
            pressure_level=pressure_level,
        )
        logger.debug(
            "[DEBUG][ContextOS] _build_context_slice_plan: included=%d excluded=%d roots=%s pressure=%s",
            len(plan.included),
            len(plan.excluded),
            plan.roots,
            plan.pressure_level,
        )
        return plan

    def _build_head_anchor(
        self,
        *,
        working_state: WorkingState,
        artifact_stubs: tuple[ArtifactRecord, ...],
        episode_cards: tuple[EpisodeCard, ...],
    ) -> str:
        lines: list[str] = []
        goal = working_state.task_state.current_goal.value if working_state.task_state.current_goal is not None else ""
        if goal:
            lines.append(f"Current goal: {goal}")
        loops = [item.value for item in working_state.task_state.open_loops[-self.policy.max_open_loops :]]
        if loops:
            lines.append("Open loops: " + "; ".join(loops))
        blocked = [item.value for item in working_state.task_state.blocked_on[: self.policy.max_open_loops]]
        if blocked:
            lines.append("Blocked on: " + "; ".join(blocked))
        decisions = [item.summary for item in working_state.decision_log[: self.policy.max_decisions]]
        if decisions:
            lines.append("Recent decisions: " + "; ".join(decisions))
        entities = [item.value for item in working_state.active_entities[: self.policy.max_stable_facts]]
        if entities:
            lines.append("Active entities: " + "; ".join(entities))
        if artifact_stubs:
            lines.append(
                "Artifacts: "
                + "; ".join(f"{item.artifact_id}<{item.artifact_type}> {item.peek}" for item in artifact_stubs)
            )
        if episode_cards:
            lines.append("Recent episodes: " + "; ".join(item.digest_64 for item in episode_cards))
        return "\n".join(lines).strip()

    def _build_tail_anchor(
        self,
        *,
        active_window: tuple[TranscriptEvent, ...],
        working_state: WorkingState,
    ) -> str:
        if not active_window:
            return ""
        last_event = active_window[-1]
        parts = [f"Last event: {last_event.role} -> {_trim_text(last_event.content, max_chars=180)}"]
        if working_state.task_state.open_loops:
            parts.append(f"Next focus: {working_state.task_state.open_loops[-1].value}")
        elif working_state.task_state.deliverables:
            parts.append(f"Next deliverable: {working_state.task_state.deliverables[0].value}")
        return "\n".join(parts).strip()

    @staticmethod
    def _sequences_from_turns(turns: tuple[str, ...]) -> set[int]:
        result: set[int] = set()
        for turn in turns:
            token = str(turn).strip().lower()
            if token.startswith("t") and token[1:].isdigit():
                result.add(int(token[1:]))
        return result


def _is_affirmative_response(text: str) -> bool:
    """Check if text is an affirmative response (imported from classifier patterns)."""
    from .patterns import _AFFIRMATIVE_RESPONSE_PATTERNS

    content = _normalize_text(text)
    if not content:
        return False
    return any(pattern.fullmatch(content) for pattern in _AFFIRMATIVE_RESPONSE_PATTERNS)
