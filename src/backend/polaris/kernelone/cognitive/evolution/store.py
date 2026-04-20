"""Evolution Store - Persistence layer for belief tracking."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name
from polaris.kernelone.cognitive.evolution.integrity import EvolutionIntegrityGuard
from polaris.kernelone.cognitive.evolution.models import (
    Belief,
    EvolutionRecord,
    EvolutionState,
    TriggerType,
)


class EvolutionStore:
    """
    Stores and manages belief evolution over time.

    Storage layout:
    runtime/evolution/
        evolution_state.jsonl     # Current state
        beliefs.jsonl              # All belief items
        history.jsonl             # Evolution records (append-only)
    """

    def __init__(
        self,
        workspace: str,
        integrity: EvolutionIntegrityGuard | None = None,
    ) -> None:
        self._workspace = workspace
        self._state: EvolutionState | None = None
        self._initialized = False
        self._integrity = integrity

    def _get_state_path(self) -> Path:
        """Get path to evolution state file."""
        # Lazy import to avoid circular dependency
        try:
            from polaris.kernelone.storage import resolve_runtime_path

            path = resolve_runtime_path(self._workspace, "runtime/evolution/evolution_state.jsonl")
            return Path(path)
        except (RuntimeError, ValueError):
            # Fallback for when storage is not available
            metadata_dir = get_workspace_metadata_dir_name()
            return Path(self._workspace) / metadata_dir / "runtime" / "evolution" / "evolution_state.jsonl"

    def _ensure_initialized(self) -> None:
        """Lazily initialize state."""
        if self._initialized:
            return

        state_path = self._get_state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)

        if state_path.exists():
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
                self._state = EvolutionState(
                    evolution_id=data["evolution_id"],
                    beliefs=tuple(Belief(**b) for b in data.get("beliefs", [])),
                    update_history=tuple(EvolutionRecord(**r) for r in data.get("update_history", [])),
                    calibration_score=data.get("calibration_score", 1.0),
                    knowledge_gaps=tuple(data.get("knowledge_gaps", [])),
                    version=data.get("version", 1),
                )
            except (RuntimeError, ValueError):
                self._state = self._create_new_state()
        else:
            self._state = self._create_new_state()

        self._initialized = True

    def _create_new_state(self) -> EvolutionState:
        """Create new evolution state."""
        return EvolutionState(
            evolution_id=f"evo_{uuid.uuid4().hex[:16]}",
            beliefs=(),
            update_history=(),
            calibration_score=1.0,
            knowledge_gaps=(),
            version=1,
        )

    def _persist_state(self) -> None:
        """Persist state to disk."""
        if self._state is None:
            return

        state_path = self._get_state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "evolution_id": self._state.evolution_id,
            "beliefs": [
                {
                    "belief_id": b.belief_id,
                    "content": b.content,
                    "source": b.source,
                    "source_session": b.source_session,
                    "confidence": b.confidence,
                    "importance": b.importance,
                    "created_at": b.created_at,
                    "verified_at": b.verified_at,
                    "falsified_at": b.falsified_at,
                    "supersedes": b.supersedes,
                    "related_rules": list(b.related_rules),
                }
                for b in self._state.beliefs
            ],
            "update_history": [
                {
                    "record_id": r.record_id,
                    "timestamp": r.timestamp,
                    "trigger_type": r.trigger_type.value,
                    "previous_belief_id": r.previous_belief_id,
                    "previous_confidence": r.previous_confidence,
                    "new_belief_id": r.new_belief_id,
                    "new_confidence": r.new_confidence,
                    "context": r.context,
                    "rationale": r.rationale,
                    "verification_needed": r.verification_needed,
                }
                for r in self._state.update_history
            ],
            "calibration_score": self._state.calibration_score,
            "knowledge_gaps": list(self._state.knowledge_gaps),
            "version": self._state.version,
        }

        state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    async def record_evolution(
        self,
        trigger_type: TriggerType,
        content: str,
        previous_belief_id: str | None = None,
        previous_confidence: float | None = None,
        context: str = "",
        rationale: str = "",
    ) -> EvolutionRecord:
        """
        Record a new evolution event.

        This is called when beliefs change due to:
        - User corrections
        - Prediction mismatches
        - New information
        - Self-reflection
        """
        self._ensure_initialized()
        # mypy doesn't know _ensure_initialized() sets _state
        assert self._state is not None

        # Create new belief
        new_belief = Belief(
            belief_id=f"belief_{uuid.uuid4().hex[:16]}",
            content=content,
            source=trigger_type.value,
            source_session=None,
            confidence=previous_confidence or 0.5,
            importance=5,
            created_at=datetime.now(timezone.utc).isoformat(),
            verified_at=None,
            falsified_at=None,
            supersedes=previous_belief_id,
            related_rules=(),
        )

        # Create evolution record
        record = EvolutionRecord(
            record_id=f"evo_{uuid.uuid4().hex[:16]}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            trigger_type=trigger_type,
            previous_belief_id=previous_belief_id,
            previous_confidence=previous_confidence,
            new_belief_id=new_belief.belief_id,
            new_confidence=new_belief.confidence,
            context=context,
            rationale=rationale,
            verification_needed=trigger_type in (TriggerType.PREDICTION_MISMATCH, TriggerType.HYPOTHESIS_FALSIFIED),
        )

        # Sign the record if integrity guard is configured.
        # The signature is computed over a version with an empty context,
        # because the context field will be overwritten to hold the signature.
        if self._integrity is not None:
            signable = EvolutionRecord(
                record_id=record.record_id,
                timestamp=record.timestamp,
                trigger_type=record.trigger_type,
                previous_belief_id=record.previous_belief_id,
                previous_confidence=record.previous_confidence,
                new_belief_id=record.new_belief_id,
                new_confidence=record.new_confidence,
                context="",
                rationale=record.rationale,
                verification_needed=record.verification_needed,
            )
            sig = self._integrity.sign_record(signable)
            record = EvolutionRecord(
                record_id=record.record_id,
                timestamp=record.timestamp,
                trigger_type=record.trigger_type,
                previous_belief_id=record.previous_belief_id,
                previous_confidence=record.previous_confidence,
                new_belief_id=record.new_belief_id,
                new_confidence=record.new_confidence,
                context=f"chain_sig:{sig}",
                rationale=record.rationale,
                verification_needed=record.verification_needed,
            )

        # Update state - create new instance since EvolutionState is frozen
        self._state = EvolutionState(
            evolution_id=self._state.evolution_id,
            calibration_score=self._state.calibration_score,
            beliefs=self._state.beliefs + (new_belief,),
            update_history=self._state.update_history + (record,),
            knowledge_gaps=self._state.knowledge_gaps,
            version=self._state.version + 1,
        )

        # Persist
        self._persist_state()

        return record

    async def get_belief(self, belief_id: str) -> Belief | None:
        """Retrieve a belief by ID."""
        self._ensure_initialized()
        if self._state is None:
            return None

        for belief in self._state.beliefs:
            if belief.belief_id == belief_id:
                return belief
        return None

    async def get_recent_evolution(self, limit: int = 50) -> tuple[EvolutionRecord, ...]:
        """Get recent evolution records."""
        self._ensure_initialized()
        if self._state is None:
            return ()

        history = self._state.update_history
        return history[-limit:] if len(history) > limit else history

    async def search_beliefs(self, query: str, top_k: int = 10) -> list[Belief]:
        """Search beliefs by content similarity (simple substring match for v1.0)."""
        self._ensure_initialized()
        if self._state is None:
            return []

        query_lower = query.lower()
        matches = [b for b in self._state.beliefs if query_lower in b.content.lower()]
        return matches[:top_k]

    async def update_belief(
        self,
        belief_id: str,
        new_confidence: float | None = None,
        new_content: str | None = None,
        new_importance: int | None = None,
        rationale: str = "",
    ) -> Belief | None:
        """Update an existing belief's properties.

        Args:
            belief_id: ID of the belief to update
            new_confidence: New confidence value (0.0-1.0)
            new_content: New content text
            new_importance: New importance value (1-10)
            rationale: Reason for the update (for audit trail)

        Returns:
            Updated Belief if found, None otherwise
        """
        self._ensure_initialized()
        if self._state is None:
            return None

        now = datetime.now(timezone.utc).isoformat()

        for i, belief in enumerate(self._state.beliefs):
            if belief.belief_id == belief_id:
                # Build updated belief
                from polaris.kernelone.cognitive.evolution.models import Belief as BeliefModel

                updated = BeliefModel(
                    belief_id=belief.belief_id,
                    content=new_content if new_content is not None else belief.content,
                    source=belief.source,
                    source_session=belief.source_session,
                    confidence=new_confidence if new_confidence is not None else belief.confidence,
                    importance=new_importance if new_importance is not None else belief.importance,
                    created_at=belief.created_at,
                    verified_at=now
                    if new_confidence is not None and new_confidence > belief.confidence
                    else belief.verified_at,
                    falsified_at=now if new_confidence is not None and new_confidence <= 0.0 else belief.falsified_at,
                    supersedes=belief.supersedes,
                    related_rules=belief.related_rules,
                )

                # Create evolution record for audit trail
                record = EvolutionRecord(
                    record_id=f"evo_{uuid.uuid4().hex[:16]}",
                    timestamp=now,
                    trigger_type=TriggerType.SELF_REFLECTION,
                    previous_belief_id=belief_id,
                    previous_confidence=belief.confidence,
                    new_belief_id=updated.belief_id,
                    new_confidence=updated.confidence,
                    context=f"update_belief: {rationale}" if rationale else "belief update",
                    rationale=rationale or "Belief property updated",
                    verification_needed=False,
                )

                # Update state with new belief and record
                new_beliefs = list(self._state.beliefs)
                new_beliefs[i] = updated

                self._state = EvolutionState(
                    evolution_id=self._state.evolution_id,
                    beliefs=tuple(new_beliefs),
                    update_history=self._state.update_history + (record,),
                    calibration_score=self._state.calibration_score,
                    knowledge_gaps=self._state.knowledge_gaps,
                    version=self._state.version + 1,
                )

                self._persist_state()
                return updated

        return None

    async def delete_belief(self, belief_id: str) -> bool:
        """Mark a belief as falsified (soft delete).

        Beliefs are never truly deleted to maintain audit trail.

        Args:
            belief_id: ID of the belief to falsify

        Returns:
            True if belief was found and falsified, False otherwise
        """
        # Use update_belief to set confidence to 0 (falsified)
        result = await self.update_belief(belief_id, new_confidence=0.0)
        return result is not None

    async def query_beliefs(
        self,
        min_confidence: float | None = None,
        max_confidence: float | None = None,
        source: str | None = None,
        verified: bool | None = None,
        limit: int = 100,
    ) -> list[Belief]:
        """Query beliefs by various filters.

        Args:
            min_confidence: Minimum confidence threshold
            max_confidence: Maximum confidence threshold
            source: Filter by source (trigger type)
            verified: If True, only return verified beliefs; if False, only unverified
            limit: Maximum number of results to return

        Returns:
            List of matching Belief objects
        """
        self._ensure_initialized()
        if self._state is None:
            return []

        results = list(self._state.beliefs)

        # Apply filters
        if min_confidence is not None:
            results = [b for b in results if b.confidence >= min_confidence]
        if max_confidence is not None:
            results = [b for b in results if b.confidence <= max_confidence]
        if source is not None:
            results = [b for b in results if b.source == source]
        if verified is not None:
            if verified:
                results = [b for b in results if b.verified_at is not None]
            else:
                results = [b for b in results if b.verified_at is None]

        return results[:limit]

    async def get_statistics(self) -> dict[str, Any]:
        """Get belief system statistics.

        Returns:
            Dictionary containing:
            - total_beliefs: Total number of beliefs
            - verified_count: Number of verified beliefs
            - falsified_count: Number of falsified beliefs
            - average_confidence: Mean confidence across all beliefs
            - high_confidence_count: Beliefs with confidence >= 0.8
            - low_confidence_count: Beliefs with confidence < 0.4
            - source_distribution: Count of beliefs by source
            - evolution_records: Number of evolution records
        """
        self._ensure_initialized()
        if self._state is None:
            return {}

        beliefs = self._state.beliefs

        if not beliefs:
            return {
                "total_beliefs": 0,
                "verified_count": 0,
                "falsified_count": 0,
                "average_confidence": 0.0,
                "high_confidence_count": 0,
                "low_confidence_count": 0,
                "source_distribution": {},
                "evolution_records": len(self._state.update_history),
            }

        # Count sources
        source_counts: dict[str, int] = {}
        for b in beliefs:
            source_counts[b.source] = source_counts.get(b.source, 0) + 1

        return {
            "total_beliefs": len(beliefs),
            "verified_count": sum(1 for b in beliefs if b.verified_at is not None),
            "falsified_count": sum(1 for b in beliefs if b.falsified_at is not None),
            "average_confidence": sum(b.confidence for b in beliefs) / len(beliefs),
            "high_confidence_count": sum(1 for b in beliefs if b.confidence >= 0.8),
            "low_confidence_count": sum(1 for b in beliefs if b.confidence < 0.4),
            "source_distribution": source_counts,
            "evolution_records": len(self._state.update_history),
        }

    async def verify_integrity(self) -> list[str]:
        """Verify the integrity of all evolution records.

        Requires that the store was initialised with an
        ``EvolutionIntegrityGuard``.

        Returns:
            List of ``record_id`` values whose HMAC signature is invalid.
            An empty list means all records are intact.
        """
        if self._integrity is None:
            return []

        self._ensure_initialized()
        if self._state is None:
            return []

        return self._integrity.verify_chain(list(self._state.update_history))
