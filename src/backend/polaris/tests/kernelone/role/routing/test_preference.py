"""Tests for preference routing module.

Covers Feedback, PersonaPreference dataclasses and PreferenceLearner logic.
All tests are pure logic (no filesystem or network side effects).
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from polaris.kernelone.role.routing.preference import (
    Feedback,
    PersonaPreference,
    PreferenceLearner,
)
from polaris.kernelone.role.routing.context import RoutingContext, UserPreference


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def learner() -> PreferenceLearner:
    """Return a fresh PreferenceLearner instance."""
    return PreferenceLearner(workspace="/tmp/test")


@pytest.fixture
def casual_context() -> RoutingContext:
    """Return a RoutingContext with casual formality preference."""
    return RoutingContext(
        task_type="chat",
        domain="general",
        intent="converse",
        user_preference=UserPreference(formality="casual"),
    )


# ------------------------------------------------------------------
# Feedback Tests
# ------------------------------------------------------------------


class TestFeedback:
    """Tests for Feedback dataclass."""

    def test_create_with_all_fields(self) -> None:
        fb = Feedback(
            session_id="session-1",
            persona_id="assistant",
            score=0.9,
            timestamp=1234567890.0,
            context={"topic": "coding"},
        )
        assert fb.session_id == "session-1"
        assert fb.persona_id == "assistant"
        assert fb.score == 0.9
        assert fb.timestamp == 1234567890.0
        assert fb.context == {"topic": "coding"}

    def test_default_timestamp(self) -> None:
        before = __import__("time").time()
        fb = Feedback(session_id="s", persona_id="p", score=1.0)
        after = __import__("time").time()
        assert before <= fb.timestamp <= after

    def test_default_context(self) -> None:
        fb = Feedback(session_id="s", persona_id="p", score=1.0)
        assert fb.context == {}

    def test_field_count(self) -> None:
        assert len(fields(Feedback)) == 5

    def test_score_boundaries(self) -> None:
        fb_min = Feedback(session_id="s", persona_id="p", score=0.0)
        fb_max = Feedback(session_id="s", persona_id="p", score=1.0)
        assert fb_min.score == 0.0
        assert fb_max.score == 1.0


# ------------------------------------------------------------------
# PersonaPreference Tests
# ------------------------------------------------------------------


class TestPersonaPreference:
    """Tests for PersonaPreference dataclass."""

    def test_create_with_defaults(self) -> None:
        pref = PersonaPreference(persona_id="assistant")
        assert pref.persona_id == "assistant"
        assert pref.total_score == 0.0
        assert pref.count == 0
        assert pref.last_used == 0.0

    def test_average_score_with_no_feedback(self) -> None:
        pref = PersonaPreference(persona_id="assistant")
        assert pref.average_score == 0.5

    def test_average_score_with_feedback(self) -> None:
        pref = PersonaPreference(persona_id="assistant", total_score=3.0, count=4)
        assert pref.average_score == 0.75

    def test_average_score_single_feedback(self) -> None:
        pref = PersonaPreference(persona_id="assistant", total_score=1.0, count=1)
        assert pref.average_score == 1.0

    def test_average_score_zero_feedback(self) -> None:
        pref = PersonaPreference(persona_id="assistant", total_score=0.0, count=3)
        assert pref.average_score == 0.0

    def test_average_score_with_negative_total(self) -> None:
        """PersonaPreference does not validate scores; negative totals are computed as-is."""
        pref = PersonaPreference(persona_id="assistant", total_score=-1.0, count=2)
        assert pref.average_score == -0.5


# ------------------------------------------------------------------
# PreferenceLearner - record_feedback Tests
# ------------------------------------------------------------------


class TestRecordFeedback:
    """Tests for PreferenceLearner.record_feedback method."""

    def test_single_feedback_recorded(self, learner: PreferenceLearner) -> None:
        learner.record_feedback("session-1", "persona-a", 0.8)
        assert len(learner._feedback_history) == 1
        assert learner._feedback_history[0].score == 0.8

    def test_multiple_feedback_same_session(self, learner: PreferenceLearner) -> None:
        learner.record_feedback("session-1", "persona-a", 0.8)
        learner.record_feedback("session-1", "persona-a", 0.9)
        assert len(learner._feedback_history) == 2

    def test_feedback_updates_persona_scores(self, learner: PreferenceLearner) -> None:
        learner.record_feedback("session-1", "persona-a", 0.8)
        pref = learner._persona_scores["session-1"]["persona-a"]
        assert pref.total_score == 0.8
        assert pref.count == 1

    def test_feedback_accumulates_scores(self, learner: PreferenceLearner) -> None:
        learner.record_feedback("session-1", "persona-a", 0.8)
        learner.record_feedback("session-1", "persona-a", 0.9)
        pref = learner._persona_scores["session-1"]["persona-a"]
        assert pref.total_score == pytest.approx(1.7)
        assert pref.count == 2

    def test_feedback_different_sessions_isolated(self, learner: PreferenceLearner) -> None:
        learner.record_feedback("session-1", "persona-a", 0.8)
        learner.record_feedback("session-2", "persona-a", 0.9)

        assert learner._persona_scores["session-1"]["persona-a"].total_score == 0.8
        assert learner._persona_scores["session-2"]["persona-a"].total_score == 0.9

    def test_feedback_different_personas_isolated(self, learner: PreferenceLearner) -> None:
        learner.record_feedback("session-1", "persona-a", 0.8)
        learner.record_feedback("session-1", "persona-b", 0.9)

        assert learner._persona_scores["session-1"]["persona-a"].total_score == 0.8
        assert learner._persona_scores["session-1"]["persona-b"].total_score == 0.9

    def test_feedback_updates_last_used(self, learner: PreferenceLearner) -> None:
        before = __import__("time").time()
        learner.record_feedback("session-1", "persona-a", 0.8)
        after = __import__("time").time()

        pref = learner._persona_scores["session-1"]["persona-a"]
        assert before <= pref.last_used <= after

    def test_feedback_with_context(self, learner: PreferenceLearner) -> None:
        ctx = {"topic": "python", "difficulty": "hard"}
        learner.record_feedback("session-1", "persona-a", 0.8, context=ctx)
        assert learner._feedback_history[0].context == ctx

    def test_feedback_with_none_context_defaults_to_empty(self, learner: PreferenceLearner) -> None:
        learner.record_feedback("session-1", "persona-a", 0.8, context=None)
        assert learner._feedback_history[0].context == {}

    def test_feedback_logs_info(self, learner: PreferenceLearner, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        with caplog.at_level(logging.INFO):
            learner.record_feedback("session-1", "persona-a", 0.8)

        assert "Recorded feedback" in caplog.text
        assert "session=session-1" in caplog.text
        assert "persona=persona-a" in caplog.text
        assert "score=0.8" in caplog.text


# ------------------------------------------------------------------
# PreferenceLearner - get_preferred_personas Tests
# ------------------------------------------------------------------


class TestGetPreferredPersonas:
    """Tests for PreferenceLearner.get_preferred_personas method."""

    def test_unknown_session_returns_default(self, learner: PreferenceLearner) -> None:
        result = learner.get_preferred_personas("unknown-session")
        assert result == ["gongbu_shilang"]

    def test_single_persona_returned(self, learner: PreferenceLearner) -> None:
        learner.record_feedback("session-1", "persona-a", 0.8)
        result = learner.get_preferred_personas("session-1")
        assert result == ["persona-a"]

    def test_sorted_by_average_score(self, learner: PreferenceLearner) -> None:
        learner.record_feedback("session-1", "persona-b", 0.9)
        learner.record_feedback("session-1", "persona-a", 0.5)
        learner.record_feedback("session-1", "persona-c", 1.0)

        result = learner.get_preferred_personas("session-1")
        assert result == ["persona-c", "persona-b", "persona-a"]

    def test_tie_break_by_count(self, learner: PreferenceLearner) -> None:
        """When scores are tied, higher count should win."""
        learner.record_feedback("session-1", "persona-a", 0.8)
        learner.record_feedback("session-1", "persona-b", 0.8)
        learner.record_feedback("session-1", "persona-b", 0.8)

        result = learner.get_preferred_personas("session-1")
        assert result[0] == "persona-b"

    def test_tie_break_by_last_used(self, learner: PreferenceLearner) -> None:
        """When score and count are tied, more recent last_used should win."""
        import time

        learner.record_feedback("session-1", "persona-a", 0.8)
        time.sleep(0.01)
        learner.record_feedback("session-1", "persona-b", 0.8)

        result = learner.get_preferred_personas("session-1")
        assert result[0] == "persona-b"

    def test_casual_context_reorders(self, learner: PreferenceLearner, casual_context: RoutingContext) -> None:
        learner.record_feedback("session-1", "casual", 0.5)
        learner.record_feedback("session-1", "formal", 0.9)

        result = learner.get_preferred_personas("session-1", context=casual_context)
        assert result[0] == "casual"

    def test_casual_context_moves_all_casual_to_front(self, learner: PreferenceLearner, casual_context: RoutingContext) -> None:
        learner.record_feedback("session-1", "relaxed", 0.5)
        learner.record_feedback("session-1", "casual", 0.6)
        learner.record_feedback("session-1", "cyberpunk_hacker", 0.7)
        learner.record_feedback("session-1", "formal", 0.9)

        result = learner.get_preferred_personas("session-1", context=casual_context)
        # Insertions at index 0 are LIFO: last processed casual persona ends up first
        assert result[0] == "relaxed"
        assert result[1] == "casual"
        assert result[2] == "cyberpunk_hacker"

    def test_no_context_ignores_formality(self, learner: PreferenceLearner) -> None:
        learner.record_feedback("session-1", "casual", 0.5)
        learner.record_feedback("session-1", "formal", 0.9)

        result = learner.get_preferred_personas("session-1", context=None)
        assert result[0] == "formal"

    def test_neutral_context_no_reorder(self, learner: PreferenceLearner) -> None:
        neutral_ctx = RoutingContext(
            task_type="chat",
            domain="general",
            intent="converse",
            user_preference=UserPreference(formality="neutral"),
        )
        learner.record_feedback("session-1", "casual", 0.5)
        learner.record_feedback("session-1", "formal", 0.9)

        result = learner.get_preferred_personas("session-1", context=neutral_ctx)
        assert result[0] == "formal"

    def test_empty_session_returns_default(self, learner: PreferenceLearner) -> None:
        result = learner.get_preferred_personas("")
        assert result == ["gongbu_shilang"]

    def test_context_without_user_preference(self, learner: PreferenceLearner) -> None:
        ctx = RoutingContext(
            task_type="chat",
            domain="general",
            intent="converse",
        )
        learner.record_feedback("session-1", "persona-a", 0.8)
        result = learner.get_preferred_personas("session-1", context=ctx)
        assert result == ["persona-a"]


# ------------------------------------------------------------------
# PreferenceLearner - get_persona_score Tests
# ------------------------------------------------------------------


class TestGetPersonaScore:
    """Tests for PreferenceLearner.get_persona_score method."""

    def test_unknown_session_returns_none(self, learner: PreferenceLearner) -> None:
        result = learner.get_persona_score("unknown", "persona-a")
        assert result is None

    def test_unknown_persona_returns_none(self, learner: PreferenceLearner) -> None:
        learner.record_feedback("session-1", "persona-a", 0.8)
        result = learner.get_persona_score("session-1", "unknown")
        assert result is None

    def test_returns_average_score(self, learner: PreferenceLearner) -> None:
        learner.record_feedback("session-1", "persona-a", 0.8)
        learner.record_feedback("session-1", "persona-a", 0.6)
        result = learner.get_persona_score("session-1", "persona-a")
        assert result == 0.7

    def test_single_feedback_score(self, learner: PreferenceLearner) -> None:
        learner.record_feedback("session-1", "persona-a", 0.9)
        result = learner.get_persona_score("session-1", "persona-a")
        assert result == 0.9


# ------------------------------------------------------------------
# PreferenceLearner - save/load Tests
# ------------------------------------------------------------------


class TestSaveLoad:
    """Tests for PreferenceLearner save/load persistence."""

    def test_save_creates_file(self, learner: PreferenceLearner, tmp_path: Any) -> None:
        path = tmp_path / "prefs.json"
        learner.save(path=path)
        assert path.exists()

    def test_save_default_path(self, learner: PreferenceLearner) -> None:
        """Save with default path should not raise."""
        with patch("polaris.kernelone.role.routing.preference.get_workspace_metadata_dir_name", return_value=".polaris"):
            learner.save()

    def test_round_trip_preserves_feedback(self, learner: PreferenceLearner, tmp_path: Any) -> None:
        learner.record_feedback("session-1", "persona-a", 0.8, context={"topic": "test"})
        path = tmp_path / "prefs.json"

        learner.save(path=path)

        new_learner = PreferenceLearner(workspace="/tmp/test")
        new_learner.load(path=path)

        assert len(new_learner._feedback_history) == 1
        assert new_learner._feedback_history[0].score == 0.8
        assert new_learner._feedback_history[0].context == {"topic": "test"}

    def test_round_trip_preserves_scores(self, learner: PreferenceLearner, tmp_path: Any) -> None:
        learner.record_feedback("session-1", "persona-a", 0.8)
        learner.record_feedback("session-1", "persona-a", 0.9)
        path = tmp_path / "prefs.json"

        learner.save(path=path)

        new_learner = PreferenceLearner(workspace="/tmp/test")
        new_learner.load(path=path)

        pref = new_learner._persona_scores["session-1"]["persona-a"]
        assert pref.total_score == pytest.approx(1.7)
        assert pref.count == 2

    def test_load_missing_file_does_not_raise(self, learner: PreferenceLearner, tmp_path: Any) -> None:
        path = tmp_path / "nonexistent.json"
        learner.load(path=path)  # Should not raise
        assert len(learner._feedback_history) == 0

    def test_load_missing_file_logs_debug(self, learner: PreferenceLearner, tmp_path: Any, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        path = tmp_path / "nonexistent.json"
        with caplog.at_level(logging.DEBUG):
            learner.load(path=path)

        assert "Preference file not found" in caplog.text

    def test_load_corrupted_json_logs_error(self, learner: PreferenceLearner, tmp_path: Any, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        path = tmp_path / "corrupt.json"
        path.write_text("not valid json", encoding="utf-8")

        with caplog.at_level(logging.ERROR):
            learner.load(path=path)

        assert "Failed to load preference data" in caplog.text

    def test_load_missing_keys_logs_error(self, learner: PreferenceLearner, tmp_path: Any, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        path = tmp_path / "bad.json"
        path.write_text('{"feedback_history": [], "persona_scores": {"user1": {"persona1": {}}}}', encoding="utf-8")

        with caplog.at_level(logging.ERROR):
            learner.load(path=path)

        assert "Failed to load preference data" in caplog.text

    def test_save_truncates_to_last_100_feedback(self, learner: PreferenceLearner, tmp_path: Any) -> None:
        for i in range(150):
            learner.record_feedback("session-1", "persona-a", 0.8)

        assert len(learner._feedback_history) == 150

        path = tmp_path / "prefs.json"
        learner.save(path=path)

        new_learner = PreferenceLearner(workspace="/tmp/test")
        new_learner.load(path=path)

        assert len(new_learner._feedback_history) == 100

    def test_save_logs_info(self, learner: PreferenceLearner, tmp_path: Any, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        path = tmp_path / "prefs.json"
        with caplog.at_level(logging.INFO):
            learner.save(path=path)

        assert "Saved preference data" in caplog.text

    def test_load_logs_info(self, learner: PreferenceLearner, tmp_path: Any, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        path = tmp_path / "prefs.json"
        learner.save(path=path)

        with caplog.at_level(logging.INFO):
            new_learner = PreferenceLearner(workspace="/tmp/test")
            new_learner.load(path=path)

        assert "Loaded preference data" in caplog.text
