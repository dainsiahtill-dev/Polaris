"""Correlation ID propagation tests for turn event system.

Covers:
1. SessionStartedEvent / SessionCompletedEvent / SessionWaitingHumanEvent
   accept turn_request_id, span_id, parent_span_id correlation fields.
2. TurnPhaseEvent, ContentChunkEvent, ErrorEvent accept correlation fields.
3. Correlation fields default to None across all event types.
4. TurnPhaseEvent preserves correlation through construction.
5. TurnTransactionController._attach_event_correlation populates fields.
"""

from __future__ import annotations

from polaris.cells.roles.kernel.public.turn_events import (
    ContentChunkEvent,
    ErrorEvent,
    SessionCompletedEvent,
    SessionStartedEvent,
    SessionWaitingHumanEvent,
    TurnPhaseEvent,
)

# ---------------------------------------------------------------------------
# Session-level events -- correlation fields accepted and default to None
# ---------------------------------------------------------------------------


def test_session_started_event_has_correlation_fields() -> None:
    """SessionStartedEvent accepts turn_request_id, span_id, parent_span_id."""
    event = SessionStartedEvent(
        session_id="sess_001",
        turn_request_id="turnreq_sess_001",
        span_id="span_sess_001",
        parent_span_id="span_parent_sess_001",
    )
    assert event.session_id == "sess_001"
    assert event.turn_request_id == "turnreq_sess_001"
    assert event.span_id == "span_sess_001"
    assert event.parent_span_id == "span_parent_sess_001"


def test_session_completed_event_has_correlation_fields() -> None:
    """SessionCompletedEvent accepts turn_request_id, span_id, parent_span_id."""
    event = SessionCompletedEvent(
        session_id="sess_002",
        reason="done",
        turn_request_id="turnreq_sess_002",
        span_id="span_sess_002",
        parent_span_id="span_parent_sess_002",
    )
    assert event.session_id == "sess_002"
    assert event.turn_request_id == "turnreq_sess_002"
    assert event.span_id == "span_sess_002"
    assert event.parent_span_id == "span_parent_sess_002"


def test_session_waiting_event_has_correlation_fields() -> None:
    """SessionWaitingHumanEvent accepts turn_request_id, span_id, parent_span_id."""
    event = SessionWaitingHumanEvent(
        session_id="sess_003",
        reason="waiting",
        turn_request_id="turnreq_sess_003",
        span_id="span_sess_003",
        parent_span_id="span_parent_sess_003",
    )
    assert event.session_id == "sess_003"
    assert event.turn_request_id == "turnreq_sess_003"
    assert event.span_id == "span_sess_003"
    assert event.parent_span_id == "span_parent_sess_003"


def test_correlation_fields_default_to_none() -> None:
    """Verify all correlation fields default to None across multiple event types."""
    session_started = SessionStartedEvent(session_id="sess_default")
    assert session_started.turn_request_id is None
    assert session_started.span_id is None
    assert session_started.parent_span_id is None

    session_completed = SessionCompletedEvent(session_id="sess_default")
    assert session_completed.turn_request_id is None
    assert session_completed.span_id is None
    assert session_completed.parent_span_id is None

    session_waiting = SessionWaitingHumanEvent(session_id="sess_default", reason="wait")
    assert session_waiting.turn_request_id is None
    assert session_waiting.span_id is None
    assert session_waiting.parent_span_id is None


# ---------------------------------------------------------------------------
# Turn-level events -- correlation fields exist and default to None
# ---------------------------------------------------------------------------


def test_turn_phase_event_correlation_fields_default_to_none() -> None:
    """TurnPhaseEvent correlation fields (turn_request_id, span_id, parent_span_id) default to None."""
    event = TurnPhaseEvent.create(turn_id="turn_001", phase="decision_requested")
    assert event.turn_request_id is None
    assert event.span_id is None
    assert event.parent_span_id is None


def test_content_chunk_event_correlation_fields_default_to_none() -> None:
    """ContentChunkEvent correlation fields default to None."""
    event = ContentChunkEvent(turn_id="turn_002", chunk="hello")
    assert event.turn_request_id is None
    assert event.span_id is None
    assert event.parent_span_id is None


def test_error_event_correlation_fields_default_to_none() -> None:
    """ErrorEvent correlation fields default to None."""
    event = ErrorEvent(turn_id="turn_003", error_type="test_error", message="boom")
    assert event.turn_request_id is None
    assert event.span_id is None
    assert event.parent_span_id is None


# ---------------------------------------------------------------------------
# Correlation preserved through construction
# ---------------------------------------------------------------------------


def test_turn_phase_event_correlation_preserved() -> None:
    """Create TurnPhaseEvent with explicit correlation; verify fields survive."""
    event = TurnPhaseEvent.create(
        turn_id="turn_corr",
        phase="decision_completed",
        turn_request_id="turnreq_abc123",
        span_id="span_def456",
        parent_span_id="span_parent_789",
    )
    assert event.turn_request_id == "turnreq_abc123"
    assert event.span_id == "span_def456"
    assert event.parent_span_id == "span_parent_789"
    assert event.turn_id == "turn_corr"
    assert event.phase == "decision_completed"


def test_content_chunk_event_correlation_preserved() -> None:
    """Create ContentChunkEvent with explicit correlation; verify fields survive."""
    event = ContentChunkEvent(
        turn_id="turn_cc",
        chunk="data",
        turn_request_id="turnreq_cc",
        span_id="span_cc",
        parent_span_id="span_parent_cc",
    )
    assert event.turn_request_id == "turnreq_cc"
    assert event.span_id == "span_cc"
    assert event.parent_span_id == "span_parent_cc"


def test_error_event_correlation_preserved() -> None:
    """Create ErrorEvent with explicit correlation; verify fields survive."""
    event = ErrorEvent(
        turn_id="turn_ee",
        error_type="fatal",
        message="crash",
        turn_request_id="turnreq_ee",
        span_id="span_ee",
        parent_span_id="span_parent_ee",
    )
    assert event.turn_request_id == "turnreq_ee"
    assert event.span_id == "span_ee"
    assert event.parent_span_id == "span_parent_ee"


# ---------------------------------------------------------------------------
# _attach_event_correlation static/class method
# ---------------------------------------------------------------------------


def test_attach_event_correlation_populates_all_fields() -> None:
    """Test TurnTransactionController._attach_event_correlation directly.

    Create a TurnPhaseEvent without correlation, call _attach_event_correlation
    with request_id, span_id, parent_span_id, and verify the returned event
    has all fields populated.
    """
    from polaris.cells.roles.kernel.internal.turn_transaction_controller import (
        TurnTransactionController,
    )

    original = TurnPhaseEvent.create(
        turn_id="turn_attach",
        phase="tool_batch_started",
    )
    assert original.turn_request_id is None
    assert original.span_id is None
    assert original.parent_span_id is None

    enriched = TurnTransactionController._attach_event_correlation(
        original,
        turn_request_id="turnreq_attached",
        turn_span_id="span_turn_level",
        parent_span_id="span_parent_level",
    )

    # turn_request_id should be set
    assert enriched.turn_request_id == "turnreq_attached"  # type: ignore[union-attr]
    # span_id should be auto-generated (not None)
    assert enriched.span_id is not None  # type: ignore[union-attr]
    assert enriched.span_id != ""  # type: ignore[union-attr]
    # parent_span_id should be set from the explicit parent_span_id argument
    assert enriched.parent_span_id == "span_parent_level"  # type: ignore[union-attr]


def test_attach_event_correlation_preserves_existing_span() -> None:
    """If an event already has a span_id, _attach_event_correlation should not overwrite it."""
    from polaris.cells.roles.kernel.internal.turn_transaction_controller import (
        TurnTransactionController,
    )

    original = TurnPhaseEvent.create(
        turn_id="turn_preserve",
        phase="completed",
        span_id="existing_span_id",
    )
    assert original.span_id == "existing_span_id"

    enriched = TurnTransactionController._attach_event_correlation(
        original,
        turn_request_id="turnreq_preserve",
        turn_span_id="span_turn",
        parent_span_id="span_parent",
    )

    # span_id must NOT be overwritten
    assert enriched.span_id == "existing_span_id"  # type: ignore[union-attr]
    assert enriched.turn_request_id == "turnreq_preserve"  # type: ignore[union-attr]


def test_attach_event_correlation_enriches_session_events() -> None:
    """Session events have correlation fields; _attach should enrich them."""
    from polaris.cells.roles.kernel.internal.turn_transaction_controller import (
        TurnTransactionController,
    )

    original = SessionStartedEvent(session_id="sess_enrich")
    assert original.turn_request_id is None
    assert original.span_id is None
    assert original.parent_span_id is None

    result = TurnTransactionController._attach_event_correlation(
        original,
        turn_request_id="turnreq_enrich",
        turn_span_id="span_enrich",
        parent_span_id="span_parent_enrich",
    )

    # Session events DO have correlation fields and they should be populated
    assert result.turn_request_id == "turnreq_enrich"  # type: ignore[union-attr]
    assert result.span_id is not None  # type: ignore[union-attr]
    assert result.parent_span_id == "span_parent_enrich"  # type: ignore[union-attr]


def test_attach_event_correlation_uses_turn_span_as_parent_fallback() -> None:
    """When parent_span_id is None, _attach should use turn_span_id as fallback parent."""
    from polaris.cells.roles.kernel.internal.turn_transaction_controller import (
        TurnTransactionController,
    )

    original = TurnPhaseEvent.create(
        turn_id="turn_fallback",
        phase="decision_requested",
    )

    enriched = TurnTransactionController._attach_event_correlation(
        original,
        turn_request_id="turnreq_fallback",
        turn_span_id="span_turn_fallback",
        parent_span_id=None,
    )

    # parent_span_id should fall back to turn_span_id
    assert enriched.parent_span_id == "span_turn_fallback"  # type: ignore[union-attr]
