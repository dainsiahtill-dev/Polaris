"""Tests for ContextOS Observer pattern implementation.

Tests observer registration, removal, and notification for lifecycle events.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from polaris.kernelone.context.context_os.models import (
    ArtifactRecord,
    EpisodeCard,
    PendingFollowUp,
    StateFirstContextOSPolicy,
    TranscriptEvent,
)
from polaris.kernelone.context.context_os.runtime import StateFirstContextOS


class MockObserver:
    """Mock observer that tracks all lifecycle notifications."""

    def __init__(self) -> None:
        self.episodes_sealed: list[EpisodeCard] = []
        self.followups_resolved: list[PendingFollowUp] = []
        self.artifacts_built: list[ArtifactRecord] = []
        self.events_created: list[TranscriptEvent] = []

    def on_episode_sealed(self, episode: EpisodeCard) -> None:
        self.episodes_sealed.append(episode)

    def on_pending_followup_resolved(self, followup: PendingFollowUp) -> None:
        self.followups_resolved.append(followup)

    def on_artifact_built(self, artifact: ArtifactRecord) -> None:
        self.artifacts_built.append(artifact)

    def on_event_created(self, event: TranscriptEvent) -> None:
        self.events_created.append(event)


class PartialObserver:
    """Observer that only implements some methods."""

    def __init__(self) -> None:
        self.artifacts_built: list[ArtifactRecord] = []
        self.events_created: list[TranscriptEvent] = []

    def on_artifact_built(self, artifact: ArtifactRecord) -> None:
        self.artifacts_built.append(artifact)

    def on_event_created(self, event: TranscriptEvent) -> None:
        self.events_created.append(event)


@dataclass
class FailingObserver:
    """Observer that raises exceptions in notification methods."""

    exception_to_raise: RuntimeError = field(default_factory=lambda: RuntimeError("Observer error"))

    def on_episode_sealed(self, episode: EpisodeCard) -> None:
        raise self.exception_to_raise

    def on_pending_followup_resolved(self, followup: PendingFollowUp) -> None:
        raise self.exception_to_raise

    def on_artifact_built(self, artifact: ArtifactRecord) -> None:
        raise self.exception_to_raise

    def on_event_created(self, event: TranscriptEvent) -> None:
        raise self.exception_to_raise


@pytest.fixture
def context_os() -> StateFirstContextOS:
    """Create a StateFirstContextOS instance for testing."""
    return StateFirstContextOS(policy=StateFirstContextOSPolicy())


class TestObserverRegistration:
    """Tests for observer registration and removal."""

    def test_add_observer(self, context_os: StateFirstContextOS) -> None:
        """Test adding an observer."""
        observer = MockObserver()
        context_os.add_observer(observer)
        assert observer in context_os._observers

    def test_add_observer_multiple(self, context_os: StateFirstContextOS) -> None:
        """Test adding multiple observers."""
        observer1 = MockObserver()
        observer2 = MockObserver()
        context_os.add_observer(observer1)
        context_os.add_observer(observer2)
        assert observer1 in context_os._observers
        assert observer2 in context_os._observers
        assert len(context_os._observers) == 2

    def test_add_observer_duplicate_noop(self, context_os: StateFirstContextOS) -> None:
        """Test adding same observer twice is a no-op."""
        observer = MockObserver()
        context_os.add_observer(observer)
        context_os.add_observer(observer)
        assert len(context_os._observers) == 1

    def test_remove_observer(self, context_os: StateFirstContextOS) -> None:
        """Test removing an observer."""
        observer = MockObserver()
        context_os.add_observer(observer)
        context_os.remove_observer(observer)
        assert observer not in context_os._observers

    def test_remove_observer_not_present(self, context_os: StateFirstContextOS) -> None:
        """Test removing observer that was never added is safe."""
        observer = MockObserver()
        # Should not raise
        context_os.remove_observer(observer)
        assert observer not in context_os._observers


class TestObserverNotification:
    """Tests for observer notification during lifecycle events."""

    @pytest.mark.asyncio
    async def test_on_event_created_notification(self, context_os: StateFirstContextOS) -> None:
        """Test that observers are notified when events are created."""
        observer = MockObserver()
        context_os.add_observer(observer)

        messages = [
            {
                "role": "user",
                "content": "Hello",
                "sequence": 0,
            },
        ]
        await context_os.project(messages=messages, existing_snapshot=None)

        assert len(observer.events_created) > 0
        assert any(e.content == "Hello" for e in observer.events_created)


class TestObserverErrorHandling:
    """Tests for observer notification error handling."""

    @pytest.mark.asyncio
    async def test_failing_observer_does_not_break_notification(self, context_os: StateFirstContextOS) -> None:
        """Test that a failing observer doesn't break notification to other observers."""
        failing_observer = FailingObserver()
        good_observer = MockObserver()

        context_os.add_observer(failing_observer)
        context_os.add_observer(good_observer)

        messages = [
            {
                "role": "user",
                "content": "Hello",
                "sequence": 0,
            },
        ]

        # Should not raise even though failing_observer raises
        await context_os.project(messages=messages, existing_snapshot=None)

        # Good observer should still receive notifications
        assert len(good_observer.events_created) > 0

    @pytest.mark.asyncio
    async def test_observer_with_partial_implementation(self, context_os: StateFirstContextOS) -> None:
        """Test observer that only implements some methods."""
        partial = PartialObserver()
        context_os.add_observer(partial)

        messages = [
            {
                "role": "user",
                "content": "Hello",
                "sequence": 0,
            },
        ]

        # Should not raise even though partial observer doesn't implement all methods
        await context_os.project(messages=messages, existing_snapshot=None)

        # Partial observer should receive events_created notification
        assert len(partial.events_created) > 0

    @pytest.mark.asyncio
    async def test_multiple_observers_all_notified(self, context_os: StateFirstContextOS) -> None:
        """Test that all observers receive notifications."""
        observer1 = MockObserver()
        observer2 = MockObserver()
        observer3 = MockObserver()

        context_os.add_observer(observer1)
        context_os.add_observer(observer2)
        context_os.add_observer(observer3)

        messages = [
            {
                "role": "user",
                "content": "Hello world",
                "sequence": 0,
            },
            {
                "role": "assistant",
                "content": "Hi there!",
                "sequence": 1,
            },
        ]

        await context_os.project(messages=messages, existing_snapshot=None)

        # All observers should receive the same events
        assert len(observer1.events_created) == len(observer2.events_created) == len(observer3.events_created)
        assert len(observer1.events_created) > 0
