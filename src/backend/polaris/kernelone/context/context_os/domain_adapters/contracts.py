"""Domain adapter contracts for State-First Context OS."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ..models_v2 import (
        ArtifactRecordV2 as ArtifactRecord,
        EpisodeCardV2 as EpisodeCard,
        PendingFollowUpV2 as PendingFollowUp,
        TranscriptEventV2 as TranscriptEvent,
        WorkingStateV2 as WorkingState,
    )
    from ..policies import StateFirstContextOSPolicy


@dataclass(frozen=True, slots=True)
class DomainRoutingDecision:
    route: str
    confidence: float = 1.0
    reasons: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DomainStatePatchHints:
    goals: tuple[str, ...] = ()
    accepted_plan: tuple[str, ...] = ()
    open_loops: tuple[str, ...] = ()
    blocked_on: tuple[str, ...] = ()
    deliverables: tuple[str, ...] = ()
    preferences: tuple[str, ...] = ()
    style: tuple[str, ...] = ()
    persistent_facts: tuple[str, ...] = ()
    temporal_facts: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()


class ContextDomainAdapter(Protocol):
    """Protocol for domain-specific evidence shaping and signal extraction."""

    adapter_id: str

    def classify_event(
        self,
        event: TranscriptEvent,
        *,
        policy: StateFirstContextOSPolicy,
    ) -> DomainRoutingDecision: ...

    def build_artifact(
        self,
        event: TranscriptEvent,
        *,
        artifact_id: str,
        policy: StateFirstContextOSPolicy,
    ) -> ArtifactRecord | None: ...

    def extract_state_hints(self, event: TranscriptEvent) -> DomainStatePatchHints: ...

    def should_seal_episode(
        self,
        *,
        closed_events: tuple[TranscriptEvent, ...],
        active_window: tuple[TranscriptEvent, ...],
        working_state: WorkingState,
    ) -> bool: ...

    def classify_assistant_followup(
        self,
        event: TranscriptEvent,
        *,
        policy: StateFirstContextOSPolicy,
    ) -> DomainRoutingDecision | None: ...

    # Lifecycle notification methods (optional - adapters may provide empty implementations)
    def on_event_created(self, event: TranscriptEvent) -> None: ...
    def on_pending_followup_resolved(self, followup: PendingFollowUp) -> None: ...
    def on_artifact_built(self, artifact: ArtifactRecord) -> None: ...
    def on_episode_sealed(self, episode: EpisodeCard) -> None: ...


class ContextOSObserver(Protocol):
    """Protocol for observing ContextOS lifecycle events.

    Observers receive notifications when significant ContextOS events occur,
    such as episodes being sealed, artifacts being built, etc.
    All methods have default empty implementations so observers only need
    to implement the methods they care about.
    """

    def on_episode_sealed(self, episode: EpisodeCard) -> None: ...
    def on_pending_followup_resolved(self, followup: PendingFollowUp) -> None: ...
    def on_artifact_built(self, artifact: ArtifactRecord) -> None: ...
    def on_event_created(self, event: TranscriptEvent) -> None: ...


class ContextOSObservable(Protocol):
    """Protocol for observer registration on ContextOS."""

    def add_observer(self, observer: ContextOSObserver) -> None: ...
    def remove_observer(self, observer: ContextOSObserver) -> None: ...
