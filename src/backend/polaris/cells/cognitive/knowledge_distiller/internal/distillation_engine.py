"""Distillation Engine - coordinates pattern analysis and knowledge storage."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from ..public.contracts import (
    DistilledKnowledgeUnitV1,
    DistillSessionCommandV1,
    SessionDistillationResultV1,
)
from .knowledge_store import KnowledgeStore
from .pattern_analyzer import ExtractedPattern, PatternAnalyzer

logger = logging.getLogger(__name__)


class DistillationEngine:
    """Coordinates the distillation of session findings into reusable knowledge.

    Flow:
    1. Analyze session structured_findings to extract patterns
    2. Convert patterns to knowledge units
    3. Store in knowledge store with deduplication
    """

    def __init__(
        self,
        workspace: str = ".",
        *,
        knowledge_store: KnowledgeStore | None = None,
        pattern_analyzer: PatternAnalyzer | None = None,
    ) -> None:
        self._workspace = str(workspace or ".")
        self._knowledge_store = knowledge_store or KnowledgeStore(workspace=self._workspace)
        self._pattern_analyzer = pattern_analyzer or PatternAnalyzer()

    def distill_session(
        self,
        command: DistillSessionCommandV1,
    ) -> SessionDistillationResultV1:
        """Distill patterns from a completed session.

        Args:
            command: Distillation command with session findings

        Returns:
            Result with knowledge units created
        """
        logger.info("Distilling session %s (outcome: %s)", command.session_id, command.outcome)

        # Step 1: Analyze structured_findings to extract patterns
        patterns = self._pattern_analyzer.analyze(
            structured_findings=command.structured_findings,
            session_id=command.session_id,
            outcome=command.outcome,
        )

        if not patterns:
            logger.debug("No patterns extracted from session %s", command.session_id)
            return SessionDistillationResultV1(
                session_id=command.session_id,
                knowledge_units_created=0,
                patterns_extracted=[],
                knowledge_ids=[],
            )

        # Step 2: Convert patterns to knowledge units
        knowledge_ids: list[str] = []
        for pattern in patterns:
            unit = self._convert_pattern_to_knowledge_unit(
                pattern=pattern,
                session_id=command.session_id,
                metadata=command.metadata,
            )

            # Step 3: Store in knowledge store
            self._knowledge_store.store(unit)
            knowledge_ids.append(unit.knowledge_id)

        logger.info(
            "Distilled %d patterns from session %s, created %d knowledge units",
            len(patterns),
            command.session_id,
            len(knowledge_ids),
        )

        return SessionDistillationResultV1(
            session_id=command.session_id,
            knowledge_units_created=len(knowledge_ids),
            patterns_extracted=[p.pattern_type for p in patterns],
            knowledge_ids=knowledge_ids,
        )

    def _convert_pattern_to_knowledge_unit(
        self,
        pattern: ExtractedPattern,
        session_id: str,
        metadata: dict[str, Any],
    ) -> DistilledKnowledgeUnitV1:
        """Convert an extracted pattern to a knowledge unit."""
        # Generate stable knowledge ID based on pattern hash
        pattern_hash = hash((pattern.pattern_type, pattern.summary.lower().strip()))
        knowledge_id = f"kn_{abs(pattern_hash) % 100000:05d}_{uuid.uuid4().hex[:8]}"

        # Build metadata
        unit_metadata: dict[str, Any] = {
            "session_id": session_id,
            **metadata,
        }

        # Add role if present in metadata
        if "role" in metadata:
            unit_metadata["role"] = metadata["role"]

        return DistilledKnowledgeUnitV1(
            knowledge_id=knowledge_id,
            knowledge_type=pattern.pattern_type,
            pattern_summary=pattern.summary,
            confidence=pattern.confidence,
            occurrence_count=1,
            related_findings=[session_id],
            extracted_insight=pattern.insight,
            prevention_hint=pattern.prevention_hint,
            created_at=datetime.now(timezone.utc),
            metadata=unit_metadata,
        )

    def get_knowledge_store(self) -> KnowledgeStore:
        """Get the underlying knowledge store."""
        return self._knowledge_store
