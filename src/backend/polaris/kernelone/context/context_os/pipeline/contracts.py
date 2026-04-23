"""Pipeline stage contracts: input and output dataclasses for each stage.

All output dataclasses are frozen to ensure immutability and safe chaining
between pipeline stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.context.context_os.models_v2 import (
        ArtifactRecordV2 as ArtifactRecord,
        BudgetPlanV2 as BudgetPlan,
        EpisodeCardV2 as EpisodeCard,
        PendingFollowUpV2 as PendingFollowUp,
        RunCardV2 as RunCard,
        TaskStateViewV2 as TaskStateView,
        TranscriptEventV2 as TranscriptEvent,
        WorkingStateV2 as WorkingState,
    )


@dataclass(frozen=True)
class PipelineInput:
    """Input to the pipeline: new messages plus existing snapshot context."""

    messages: list[dict[str, Any]] | tuple[dict[str, Any], ...]
    existing_snapshot_transcript: tuple[TranscriptEvent, ...] = field(default=())
    existing_snapshot_artifacts: tuple[ArtifactRecord, ...] = field(default=())
    existing_snapshot_episodes: tuple[EpisodeCard, ...] = field(default=())
    current_pending_followup: PendingFollowUp | None = field(default=None)
    recent_window_messages: int = 8
    focus: str = ""


@dataclass(frozen=True)
class TranscriptMergerOutput:
    """Output from Stage 1: merged transcript events."""

    transcript: tuple[TranscriptEvent, ...]


@dataclass(frozen=True)
class CanonicalizerOutput:
    """Output from Stage 2: dialog-act-classified, routed transcript with artifacts.

    Also carries resolved pending follow-up state.
    """

    transcript: tuple[TranscriptEvent, ...]
    artifacts: tuple[ArtifactRecord, ...]
    resolved_followup: PendingFollowUp | None = field(default=None)


@dataclass(frozen=True)
class StatePatcherOutput:
    """Output from Stage 3: built working state from transcript events."""

    working_state: WorkingState


@dataclass(frozen=True)
class BudgetPlannerOutput:
    """Output from Stage 4: computed budget plan with invariants validated."""

    budget_plan: BudgetPlan


@dataclass(frozen=True)
class WindowCollectorOutput:
    """Output from Stage 5: pinned active window of transcript events."""

    active_window: tuple[TranscriptEvent, ...]


@dataclass(frozen=True)
class EpisodeSealerOutput:
    """Output from Stage 6: updated episode store with newly sealed episodes."""

    episode_store: tuple[EpisodeCard, ...]


@dataclass(frozen=True)
class ArtifactSelectorOutput:
    """Output from Stage 7: selected artifact stubs and episode cards for prompt."""

    artifact_stubs: tuple[ArtifactRecord, ...]
    episode_cards: tuple[EpisodeCard, ...]
    head_anchor: str = field(default="")
    tail_anchor: str = field(default="")
    run_card: RunCard = field(default=None)  # type: ignore[assignment]
    context_slice_plan: Any = field(default=None)  # ContextSlicePlan


@dataclass(frozen=True)
class PipelineOutput:
    """Final output of the pipeline: snapshot and projection components."""

    snapshot_transcript: tuple[TranscriptEvent, ...]
    snapshot_working_state: WorkingState
    snapshot_artifacts: tuple[ArtifactRecord, ...]
    snapshot_episodes: tuple[EpisodeCard, ...]
    snapshot_budget_plan: BudgetPlan
    snapshot_pending_followup: PendingFollowUp | None
    active_window: tuple[TranscriptEvent, ...]
    artifact_stubs: tuple[ArtifactRecord, ...]
    episode_cards: tuple[EpisodeCard, ...]
    head_anchor: str
    tail_anchor: str
    run_card: RunCard
    context_slice_plan: Any  # ContextSlicePlan
