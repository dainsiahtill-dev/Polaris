"""cognitive.knowledge_distiller - Cross-session knowledge distillation."""

from polaris.cells.cognitive.knowledge_distiller.internal.distillation_engine import (
    DistillationEngine,
)
from polaris.cells.cognitive.knowledge_distiller.internal.knowledge_store import (
    KnowledgeStore,
)
from polaris.cells.cognitive.knowledge_distiller.internal.pattern_analyzer import (
    PatternAnalyzer,
)
from polaris.cells.cognitive.knowledge_distiller.public.contracts import (
    DistilledKnowledgeUnitV1,
    DistillSessionCommandV1,
    KnowledgeDistillerError,
    KnowledgeRetrievalResultV1,
    RetrieveKnowledgeQueryV1,
    SessionDistillationResultV1,
)
from polaris.cells.cognitive.knowledge_distiller.public.service import (
    KnowledgeDistillerService,
)

__all__ = [
    "DistillSessionCommandV1",
    "DistillationEngine",
    "DistilledKnowledgeUnitV1",
    "KnowledgeDistillerError",
    "KnowledgeDistillerService",
    "KnowledgeRetrievalResultV1",
    "KnowledgeStore",
    "PatternAnalyzer",
    "RetrieveKnowledgeQueryV1",
    "SessionDistillationResultV1",
]
