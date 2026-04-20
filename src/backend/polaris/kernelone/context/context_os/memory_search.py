"""Memory search implementation for Context OS.

This module provides the memory search logic for querying state history,
artifacts, and episodes with relevance scoring.

Phase 5-6 Enhancements:
- T6-1: HybridMemory integration (optional semantic search backend)
- T6-2: O(n²) artifact recency calculation fixed to O(1) with pre-built index
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from .helpers import _normalize_text

if TYPE_CHECKING:
    from .models_v2 import ContextOSSnapshotV2 as ContextOSSnapshot

logger = logging.getLogger(__name__)


def _search_memory_impl(
    snapshot: ContextOSSnapshot | dict[str, Any],
    query: str,
    *,
    kind: str | None = None,
    entity: str | None = None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Search memory with relevance scoring.

    Phase 5-6 enhancements:
    1. Pre-build event_id -> sequence mapping for O(1) lookup
    2. Better semantic signal: check for entity mentions, not just goal terms
    3. O(n²) artifact recency calculation fixed to O(1)

    Args:
        snapshot: ContextOS snapshot to search (dataclass or dict)
        query: Search query text
        kind: Filter by candidate kind ("state", "artifact", "episode")
        entity: Entity filter token
        limit: Maximum results to return

    Returns:
        List of candidate dicts with score, text, why, and metadata.
    """
    try:
        return _search_memory_impl_inner(snapshot, query, kind=kind, entity=entity, limit=limit)
    except (RuntimeError, ValueError):
        logger.warning("memory_search failed, returning empty results", exc_info=True)
        return []


def _search_memory_impl_inner(
    snapshot: ContextOSSnapshot | dict[str, Any],
    query: str,
    *,
    kind: str | None = None,
    entity: str | None = None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Inner implementation of memory search without exception handling.

    This function contains the actual search logic and should not be called directly.
    Use _search_memory_impl() which wraps this with exception protection.
    """
    query_terms = {
        token for token in re.findall(r"[a-zA-Z0-9_.:/\\-]+", _normalize_text(query).lower()) if len(token) >= 2
    }

    # Extract snapshot data - handle both dataclass and dict
    if isinstance(snapshot, dict):
        transcript = snapshot.get("transcript_log", [])
        working_state = snapshot.get("working_state", {})
        state_history = working_state.get("state_history", [])
        task_state = working_state.get("task_state", {})
        current_goal = task_state.get("current_goal", {})
        current_goal_value = current_goal.get("value", "") if isinstance(current_goal, dict) else ""
        active_entities_list = working_state.get("active_entities", [])
        active_artifacts_list = working_state.get("active_artifacts", [])
        artifact_store = snapshot.get("artifact_store", [])
        episode_store = snapshot.get("episode_store", [])
    else:
        transcript = snapshot.transcript_log
        working_state = snapshot.working_state
        state_history = working_state.state_history
        task_state = working_state.task_state
        current_goal = task_state.current_goal
        current_goal_value = current_goal.value if current_goal is not None else ""
        active_entities_list = working_state.active_entities
        active_artifacts_list = working_state.active_artifacts
        artifact_store = snapshot.artifact_store
        episode_store = snapshot.episode_store

    entity_token = _normalize_text(entity).lower()
    goal_terms = {
        token
        for token in re.findall(
            r"[a-zA-Z0-9_.:/\\-]+",
            _normalize_text(current_goal_value).lower(),
        )
        if len(token) >= 2
    }
    active_entities = {
        item.value.lower() if hasattr(item, "value") else str(item).lower() for item in active_entities_list
    }
    active_artifacts = {item.lower() if isinstance(item, str) else str(item).lower() for item in active_artifacts_list}

    # T6-2 Fix: Pre-build event_id -> sequence index for O(1) lookup
    # This eliminates the O(n) lookup per artifact that caused O(n²) behavior
    event_index: dict[str, int] = {}
    for event in transcript:
        event_id = event.event_id if hasattr(event, "event_id") else event.get("event_id", "")
        event_seq = event.sequence if hasattr(event, "sequence") else event.get("sequence", 0)
        if event_id:
            event_index[event_id] = event_seq

    max_sequence = max(
        (event.sequence if hasattr(event, "sequence") else event.get("sequence", 0) for event in transcript), default=1
    )

    # T6-6 Fix: Compute max episode sequence separately for episode recency
    # This fixes recency calculation for reopened episodes
    max_episode_sequence = max(
        (ep.to_sequence if hasattr(ep, "to_sequence") else ep.get("to_sequence", 0) for ep in episode_store),
        default=1,
    )

    candidates: list[dict[str, Any]] = []

    def add_candidate(
        *,
        candidate_kind: str,
        candidate_id: str,
        text: str,
        recency_ref: int,
        metadata: dict[str, Any] | None = None,
        recency_denominator: int | None = None,
    ) -> None:
        if kind and candidate_kind != kind:
            return
        token = _normalize_text(text)
        lowered = token.lower()
        matched_terms = tuple(sorted(item for item in query_terms if item in lowered))
        lexical = len(matched_terms) / max(1, len(query_terms)) if query_terms else 0.0
        semantic_terms = tuple(sorted(item for item in goal_terms if item in lowered))
        entity_match = entity_token and entity_token in lowered
        entity_hits = tuple(sorted(item for item in active_entities if item and item in lowered))
        artifact_hits = tuple(sorted(item for item in active_artifacts if item and item in lowered))
        if query_terms and not matched_terms and not entity_match and not entity_hits and not artifact_hits:
            return
        semantic = len(semantic_terms) / max(1, len(goal_terms)) if goal_terms else lexical
        entity_time = 1.0 if entity_match else min(1.0, (len(entity_hits) + len(artifact_hits)) / 2.0)
        dependency = 1.0 if artifact_hits else 0.5 if entity_hits else 0.0
        # Use recency_denominator if provided (for episodes), otherwise use max_sequence (for states/artifacts)
        denominator = recency_denominator if recency_denominator is not None else max_sequence
        recency = max(0.0, min(1.0, recency_ref / max(1, denominator)))
        score = (0.35 * semantic) + (0.20 * lexical) + (0.20 * entity_time) + (0.15 * dependency) + (0.10 * recency)
        if score <= 0.0 and query_terms:
            return
        reasons: list[str] = []
        if matched_terms:
            reasons.append("lexical_match")
        if semantic_terms:
            reasons.append("goal_overlap")
        if entity_match or entity_hits:
            reasons.append("entity_match")
        if artifact_hits:
            reasons.append("dependency_reachability")
        if recency >= 0.5:
            reasons.append("recent")
        candidates.append(
            {
                "kind": candidate_kind,
                "id": candidate_id,
                "score": round(score, 4),
                "text": token,
                "why": reasons or ["low_confidence_match"],
                "score_breakdown": {
                    "semantic": round(semantic, 4),
                    "lexical": round(lexical, 4),
                    "entity_time_match": round(entity_time, 4),
                    "dependency_reachability": round(dependency, 4),
                    "recency": round(recency, 4),
                },
                "metadata": {
                    **dict(metadata or {}),
                    "matched_terms": list(matched_terms),
                    "matched_goal_terms": list(semantic_terms),
                    "matched_entities": list(entity_hits),
                    "matched_artifacts": list(artifact_hits),
                },
            }
        )

    # Process state entries
    for state_entry in state_history:
        entry_id = state_entry.entry_id if hasattr(state_entry, "entry_id") else state_entry.get("entry_id", "")
        entry_value = state_entry.value if hasattr(state_entry, "value") else state_entry.get("value", "")
        entry_path = state_entry.path if hasattr(state_entry, "path") else state_entry.get("path", "")
        source_turns = (
            list(state_entry.source_turns)
            if hasattr(state_entry, "source_turns")
            else state_entry.get("source_turns", [])
        )
        add_candidate(
            candidate_kind="state",
            candidate_id=entry_id,
            text=entry_value,
            recency_ref=max((int(turn[1:]) for turn in source_turns if turn.startswith("t")), default=0),
            metadata={"path": entry_path, "source_turns": source_turns},
        )

    # T6-2 Fix: Use pre-built event_index for O(1) artifact recency lookup
    for artifact in artifact_store:
        artifact_id = artifact.artifact_id if hasattr(artifact, "artifact_id") else artifact.get("artifact_id", "")
        artifact_peek = artifact.peek if hasattr(artifact, "peek") else artifact.get("peek", "")
        artifact_keys = list(artifact.keys) if hasattr(artifact, "keys") else artifact.get("keys", [])
        source_event_ids = (
            artifact.source_event_ids if hasattr(artifact, "source_event_ids") else artifact.get("source_event_ids", [])
        )

        # O(1) lookup using pre-built event_index instead of O(n) nested loop
        recency_ref = max(
            (event_index.get(aid, 0) for aid in source_event_ids),
            default=0,
        )

        artifact_stub = artifact.to_stub() if hasattr(artifact, "to_stub") else artifact
        add_candidate(
            candidate_kind="artifact",
            candidate_id=artifact_id,
            text=f"{artifact_peek} {' '.join(artifact_keys)}",
            recency_ref=recency_ref,
            metadata=artifact_stub,
        )

    # Process active entities as direct candidates
    for idx, entity in enumerate(active_entities_list):
        entity_value = entity.value if hasattr(entity, "value") else str(entity)
        add_candidate(
            candidate_kind="entity",
            candidate_id=f"ent_{idx}",
            text=entity_value,
            recency_ref=max_sequence,
        )

    # Process current goal as direct candidate
    if current_goal_value:
        add_candidate(
            candidate_kind="goal",
            candidate_id="goal_current",
            text=current_goal_value,
            recency_ref=max_sequence,
        )

    # Process episodes
    for episode in episode_store:
        episode_id = episode.episode_id if hasattr(episode, "episode_id") else episode.get("episode_id", "")
        episode_intent = episode.intent if hasattr(episode, "intent") else episode.get("intent", "")
        episode_outcome = episode.outcome if hasattr(episode, "outcome") else episode.get("outcome", "")
        episode_digest = episode.digest_256 if hasattr(episode, "digest_256") else episode.get("digest_256", "")
        episode_to_seq = episode.to_sequence if hasattr(episode, "to_sequence") else episode.get("to_sequence", 0)
        episode_from_seq = (
            episode.from_sequence if hasattr(episode, "from_sequence") else episode.get("from_sequence", 0)
        )
        add_candidate(
            candidate_kind="episode",
            candidate_id=episode_id,
            text=f"{episode_intent} {episode_outcome} {episode_digest}",
            recency_ref=episode_to_seq,
            recency_denominator=max_episode_sequence,  # Use episode-specific denominator
            metadata={"from_sequence": episode_from_seq, "to_sequence": episode_to_seq},
        )
    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates[: max(1, limit)]
