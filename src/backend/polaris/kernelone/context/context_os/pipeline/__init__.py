"""State-First Context OS projection pipeline.

This package provides a 7-stage pipeline architecture for projecting
session context. Each stage is a self-contained processor with a single
responsibility, enabling testability and composability.

Stages
======
1. TranscriptMerger   - Merge existing transcript with new messages
2. Canonicalizer      - Dialog act classification, routing, artifact offload
3. StatePatcher       - Extract state hints and build WorkingState
4. BudgetPlanner      - Compute token budgets and validate invariants
5. WindowCollector    - Collect pinned active window events
6. EpisodeSealer     - Seal closed episodes based on active window
7. ArtifactSelector   - Select artifacts and episodes for prompt injection
"""

from __future__ import annotations

from .contracts import (
    ArtifactSelectorOutput,
    BudgetPlannerOutput,
    CanonicalizerOutput,
    EpisodeSealerOutput,
    PipelineInput,
    PipelineOutput,
    StatePatcherOutput,
    TranscriptMergerOutput,
    WindowCollectorOutput,
)
from .runner import PipelineRunner
from .stages import (
    ArtifactSelector,
    BudgetPlanner,
    Canonicalizer,
    EpisodeSealer,
    StatePatcher,
    TranscriptMerger,
    WindowCollector,
)

__all__ = [
    "ArtifactSelector",
    "ArtifactSelectorOutput",
    "BudgetPlanner",
    "BudgetPlannerOutput",
    "Canonicalizer",
    "CanonicalizerOutput",
    "EpisodeSealer",
    "EpisodeSealerOutput",
    "PipelineInput",
    "PipelineOutput",
    "PipelineRunner",
    "StatePatcher",
    "StatePatcherOutput",
    "TranscriptMerger",
    "TranscriptMergerOutput",
    "WindowCollector",
    "WindowCollectorOutput",
]
