"""Public service facade for cognitive.knowledge_distiller."""

from __future__ import annotations

from typing import TYPE_CHECKING

from polaris.cells.cognitive.knowledge_distiller.internal.pattern_analyzer import (
    PatternAnalyzer,
)
from polaris.cells.cognitive.knowledge_distiller.public.contracts import (
    DistilledKnowledgeUnitV1,
    DistillSessionCommandV1,
    KnowledgeRetrievalResultV1,
    RetrieveKnowledgeQueryV1,
    SessionDistillationResultV1,
)

if TYPE_CHECKING:
    from polaris.cells.cognitive.knowledge_distiller.internal.distillation_engine import (
        DistillationEngine,
    )


class KnowledgeDistillerService:
    """Public service for cross-session knowledge distillation.

    Usage::

        service = KnowledgeDistillerService(workspace=".")
        # Distill patterns from a completed session
        result = service.distill_session(DistillSessionCommandV1(
            workspace=".",
            session_id="sess_123",
            structured_findings={"error_summary": "..."},
            outcome="completed",
        ))
        # Retrieve relevant knowledge for a new session
        knowledge = service.retrieve_knowledge(RetrieveKnowledgeQueryV1(
            workspace=".",
            query="login timeout error",
            top_k=5,
        ))
    """

    def __init__(
        self,
        workspace: str = ".",
        *,
        distillation_engine: DistillationEngine | None = None,
    ) -> None:
        self._workspace = str(workspace or ".")
        # Lazy import to avoid circular dependency at runtime
        from polaris.cells.cognitive.knowledge_distiller.internal.distillation_engine import (
            DistillationEngine,
        )

        self._engine = distillation_engine or DistillationEngine(workspace=self._workspace)

    def distill_session(self, command: DistillSessionCommandV1) -> SessionDistillationResultV1:
        """Distill patterns from a completed session.

        Args:
            command: Distillation command with session findings

        Returns:
            Result with knowledge units created
        """
        return self._engine.distill_session(command)

    def retrieve_knowledge(self, query: RetrieveKnowledgeQueryV1) -> KnowledgeRetrievalResultV1:
        """Retrieve relevant knowledge for a query.

        Args:
            query: Retrieval query

        Returns:
            Retrieved knowledge units
        """
        store = self._engine.get_knowledge_store()
        return store.retrieve(query)

    def get_all_knowledge(self) -> list[DistilledKnowledgeUnitV1]:
        """Get all stored knowledge units.

        Returns:
            All knowledge units
        """
        store = self._engine.get_knowledge_store()
        return store.get_all()


__all__ = [
    "DistillSessionCommandV1",
    "DistilledKnowledgeUnitV1",
    "KnowledgeDistillerService",
    "KnowledgeRetrievalResultV1",
    "PatternAnalyzer",
    "RetrieveKnowledgeQueryV1",
    "SessionDistillationResultV1",
]
