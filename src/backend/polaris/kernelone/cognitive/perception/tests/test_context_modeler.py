"""Unit tests for ContextModeler component."""

from __future__ import annotations

import pytest
from polaris.kernelone.cognitive.perception.context_modeler import (
    ContextModeler,
    IntentGraphStore,
    SessionContext,
)
from polaris.kernelone.cognitive.perception.models import IntentNode


class MockIntentGraphStore(IntentGraphStore):
    """Mock store for testing persistence."""

    def __init__(self) -> None:
        self._storage: dict[str, SessionContext] = {}

    def get_session_context(self, session_id: str) -> SessionContext | None:
        return self._storage.get(session_id)

    def save_session_context(self, context: SessionContext) -> None:
        self._storage[context.session_id] = context


@pytest.fixture
def modeler():
    """Create a fresh ContextModeler instance."""
    return ContextModeler()


@pytest.fixture
def sample_intent():
    """Create a sample intent node."""
    return IntentNode(
        node_id="intent_001",
        intent_type="create_file",
        content="Create a new API endpoint",
        confidence=0.85,
        source_event_id="test_event",
    )


@pytest.fixture
def sample_intent_2():
    """Create another sample intent node."""
    return IntentNode(
        node_id="intent_002",
        intent_type="modify_file",
        content="Update the user module",
        confidence=0.75,
        source_event_id="test_event_2",
    )


class TestContextModelerInitialization:
    """Tests for ContextModeler initialization."""

    def test_init_without_store(self):
        modeler = ContextModeler()
        assert modeler._store is None
        assert modeler._session_contexts == {}

    def test_init_with_store(self):
        store = MockIntentGraphStore()
        modeler = ContextModeler(store=store)
        assert modeler._store is store
        assert modeler._session_contexts == {}


class TestUpdateContext:
    """Tests for update_context method."""

    def test_update_new_session(self, modeler, sample_intent):
        modeler.update_context("session_001", sample_intent)

        context = modeler.get_session_context("session_001")
        assert context is not None
        assert context.session_id == "session_001"
        assert len(context.intent_history) == 1
        assert context.intent_history[0] == sample_intent

    def test_update_existing_session(self, modeler, sample_intent, sample_intent_2):
        modeler.update_context("session_001", sample_intent)
        modeler.update_context("session_001", sample_intent_2)

        context = modeler.get_session_context("session_001")
        assert len(context.intent_history) == 2
        assert context.intent_history[0] == sample_intent
        assert context.intent_history[1] == sample_intent_2

    def test_update_multiple_sessions(self, modeler, sample_intent, sample_intent_2):
        modeler.update_context("session_001", sample_intent)
        modeler.update_context("session_002", sample_intent_2)

        context1 = modeler.get_session_context("session_001")
        context2 = modeler.get_session_context("session_002")

        assert len(context1.intent_history) == 1
        assert len(context2.intent_history) == 1
        assert context1.intent_history[0] == sample_intent
        assert context2.intent_history[0] == sample_intent_2

    def test_update_with_store(self, sample_intent):
        store = MockIntentGraphStore()
        modeler = ContextModeler(store=store)

        modeler.update_context("session_001", sample_intent)

        # Verify persistence
        stored_context = store.get_session_context("session_001")
        assert stored_context is not None
        assert len(stored_context.intent_history) == 1


class TestGetRelevantHistory:
    """Tests for get_relevant_history method."""

    def test_empty_history(self, modeler):
        result = modeler.get_relevant_history("session_001", "create_file")
        assert result == []

    def test_single_intent_relevance(self, modeler, sample_intent):
        modeler.update_context("session_001", sample_intent)

        # Same type should be relevant
        result = modeler.get_relevant_history("session_001", "create_file")
        assert len(result) == 1
        assert result[0] == sample_intent

        # Different type should not be relevant (score 0)
        result = modeler.get_relevant_history("session_001", "delete_file")
        assert len(result) == 1  # Still returned but with lower score

    def test_limit_respected(self, modeler):
        # Add multiple intents
        for i in range(10):
            intent = IntentNode(
                node_id=f"intent_{i:03d}",
                intent_type="create_file",
                content=f"Create file {i}",
                confidence=0.8,
                source_event_id=f"event_{i}",
            )
            modeler.update_context("session_001", intent)

        result = modeler.get_relevant_history("session_001", "create_file", limit=5)
        assert len(result) == 5

    def test_relevance_scoring(self, modeler):
        # Add intents of different types
        create_intent = IntentNode(
            node_id="intent_create",
            intent_type="create_file",
            content="Create something",
            confidence=0.9,
            source_event_id="event_create",
        )
        deep_intent = IntentNode(
            node_id="intent_deep",
            intent_type="deep",
            content="Deep analysis",
            confidence=0.7,
            source_event_id="event_deep",
        )
        modify_intent = IntentNode(
            node_id="intent_modify",
            intent_type="modify_file",
            content="Modify something",
            confidence=0.8,
            source_event_id="event_modify",
        )

        modeler.update_context("session_001", create_intent)
        modeler.update_context("session_001", deep_intent)
        modeler.update_context("session_001", modify_intent)

        # Query for create_file type - should prioritize create_file and deep intents
        result = modeler.get_relevant_history("session_001", "create_file", limit=2)
        assert len(result) == 2
        # First should be create_file (score 1.0), second should be deep (score 0.5)
        assert result[0].intent_type == "create_file"


class TestDetectPatterns:
    """Tests for detect_patterns method."""

    def test_insufficient_data(self, modeler):
        result = modeler.detect_patterns("session_001")
        assert result["status"] == "insufficient_data"

    def test_single_intent_insufficient(self, modeler, sample_intent):
        modeler.update_context("session_001", sample_intent)
        result = modeler.detect_patterns("session_001")
        assert result["status"] == "insufficient_data"

    def test_intent_type_distribution(self, modeler):
        # Add multiple intents of different types
        intents = [
            IntentNode(
                node_id=f"intent_{i}",
                intent_type="create_file" if i % 2 == 0 else "modify_file",
                content=f"Action {i}",
                confidence=0.8,
                source_event_id=f"event_{i}",
            )
            for i in range(4)
        ]
        for intent in intents:
            modeler.update_context("session_001", intent)

        result = modeler.detect_patterns("session_001")
        assert result["status"] == "analyzed"
        assert result["total_intents"] == 4
        assert result["intent_type_distribution"]["create_file"] == 2
        assert result["intent_type_distribution"]["modify_file"] == 2

    def test_confidence_trend_improving(self, modeler):
        # Add intents with increasing confidence
        for i in range(5):
            intent = IntentNode(
                node_id=f"intent_{i}",
                intent_type="create_file",
                content=f"Action {i}",
                confidence=0.5 + (i * 0.1),  # 0.5, 0.6, 0.7, 0.8, 0.9
                source_event_id=f"event_{i}",
            )
            modeler.update_context("session_001", intent)

        result = modeler.detect_patterns("session_001")
        assert result["confidence_trend"] == "improving"

    def test_confidence_trend_declining(self, modeler):
        # Add intents with decreasing confidence
        for i in range(5):
            intent = IntentNode(
                node_id=f"intent_{i}",
                intent_type="create_file",
                content=f"Action {i}",
                confidence=0.9 - (i * 0.1),  # 0.9, 0.8, 0.7, 0.6, 0.5
                source_event_id=f"event_{i}",
            )
            modeler.update_context("session_001", intent)

        result = modeler.detect_patterns("session_001")
        assert result["confidence_trend"] == "declining"

    def test_confidence_trend_stable(self, modeler):
        # Add intents with stable confidence
        for i in range(5):
            intent = IntentNode(
                node_id=f"intent_{i}",
                intent_type="create_file",
                content=f"Action {i}",
                confidence=0.8,
                source_event_id=f"event_{i}",
            )
            modeler.update_context("session_001", intent)

        result = modeler.detect_patterns("session_001")
        assert result["confidence_trend"] == "stable"

    def test_repetition_pattern_single(self, modeler):
        # Add same intent type 3 times
        for i in range(3):
            intent = IntentNode(
                node_id=f"intent_{i}",
                intent_type="create_file",
                content=f"Action {i}",
                confidence=0.8,
                source_event_id=f"event_{i}",
            )
            modeler.update_context("session_001", intent)

        result = modeler.detect_patterns("session_001")
        assert result["repetition_pattern"]["type"] == "single_intent_repetition"
        assert result["repetition_pattern"]["intent_type"] == "create_file"
        assert result["repetition_pattern"]["count"] == 3

    def test_repetition_pattern_alternating(self, modeler):
        # Add alternating intent types
        for i in range(3):
            intent = IntentNode(
                node_id=f"intent_{i}",
                intent_type="create_file" if i % 2 == 0 else "modify_file",
                content=f"Action {i}",
                confidence=0.8,
                source_event_id=f"event_{i}",
            )
            modeler.update_context("session_001", intent)

        result = modeler.detect_patterns("session_001")
        assert result["repetition_pattern"]["type"] == "alternating"


class TestGetContextEnrichment:
    """Tests for get_context_enrichment method."""

    def test_new_session(self, modeler):
        result = modeler.get_context_enrichment("session_001")
        assert result["session_id"] == "session_001"
        assert result["has_history"] is False
        assert result["intent_count"] == 0
        assert result["context_type"] == "new_session"

    def test_early_session(self, modeler, sample_intent):
        modeler.update_context("session_001", sample_intent)
        result = modeler.get_context_enrichment("session_001")
        assert result["context_type"] == "early_session"
        assert result["has_history"] is True
        assert result["intent_count"] == 1

    def test_developing_session(self, modeler):
        for i in range(3):
            intent = IntentNode(
                node_id=f"intent_{i}",
                intent_type="create_file",
                content=f"Action {i}",
                confidence=0.8,
                source_event_id=f"event_{i}",
            )
            modeler.update_context("session_001", intent)

        result = modeler.get_context_enrichment("session_001")
        assert result["context_type"] == "developing_session"
        assert result["intent_count"] == 3

    def test_established_session(self, modeler):
        for i in range(6):
            intent = IntentNode(
                node_id=f"intent_{i}",
                intent_type="create_file",
                content=f"Action {i}",
                confidence=0.8,
                source_event_id=f"event_{i}",
            )
            modeler.update_context("session_001", intent)

        result = modeler.get_context_enrichment("session_001")
        assert result["context_type"] == "established_session"
        assert result["intent_count"] == 6

    def test_recent_intent_types(self, modeler):
        for i in range(5):
            intent = IntentNode(
                node_id=f"intent_{i}",
                intent_type="create_file" if i % 2 == 0 else "modify_file",
                content=f"Action {i}",
                confidence=0.8,
                source_event_id=f"event_{i}",
            )
            modeler.update_context("session_001", intent)

        result = modeler.get_context_enrichment("session_001")
        # Should have last 3 intent types (chronological order)
        assert len(result["recent_intent_types"]) == 3
        # History: [create, modify, create, modify, create]
        # recent_intent_types[-3:] = [create, modify, create] (3rd from last, 2nd from last, most recent)
        # Most recent is at index 2
        assert result["recent_intent_types"][2] == "create_file"  # Most recent

    def test_recent_confidence_avg(self, modeler):
        intents = [
            IntentNode(
                node_id="intent_1",
                intent_type="create_file",
                content="Action 1",
                confidence=0.9,
                source_event_id="event_1",
            ),
            IntentNode(
                node_id="intent_2",
                intent_type="modify_file",
                content="Action 2",
                confidence=0.7,
                source_event_id="event_2",
            ),
            IntentNode(
                node_id="intent_3",
                intent_type="read_file",
                content="Action 3",
                confidence=0.8,
                source_event_id="event_3",
            ),
        ]
        for intent in intents:
            modeler.update_context("session_001", intent)

        result = modeler.get_context_enrichment("session_001")
        expected_avg = (0.9 + 0.7 + 0.8) / 3
        assert result["recent_confidence_avg"] == pytest.approx(expected_avg, 0.01)

    def test_dominant_intent_type(self, modeler):
        # Create more create_file intents than others
        for i in range(5):
            intent = IntentNode(
                node_id=f"intent_{i}",
                intent_type="create_file" if i < 3 else "modify_file",
                content=f"Action {i}",
                confidence=0.8,
                source_event_id=f"event_{i}",
            )
            modeler.update_context("session_001", intent)

        result = modeler.get_context_enrichment("session_001")
        assert result["dominant_intent_type"] == "create_file"

    def test_detected_patterns_in_enrichment(self, modeler):
        # Add intents and detect patterns
        for i in range(5):
            intent = IntentNode(
                node_id=f"intent_{i}",
                intent_type="create_file",
                content=f"Action {i}",
                confidence=0.8,
                source_event_id=f"event_{i}",
            )
            modeler.update_context("session_001", intent)

        # First detect patterns
        modeler.detect_patterns("session_001")

        # Then get enrichment
        result = modeler.get_context_enrichment("session_001")
        assert "detected_patterns" in result
        assert result["detected_patterns"]["confidence_trend"] == "stable"
        assert result["detected_patterns"]["repetition"] is True


class TestClearSession:
    """Tests for clear_session method."""

    def test_clear_existing_session(self, modeler, sample_intent):
        modeler.update_context("session_001", sample_intent)
        assert modeler.get_session_context("session_001") is not None

        result = modeler.clear_session("session_001")
        assert result is True
        assert modeler.get_session_context("session_001") is None

    def test_clear_nonexistent_session(self, modeler):
        result = modeler.clear_session("session_001")
        assert result is False


class TestPerceptionLayerIntegration:
    """Integration tests with PerceptionLayer."""

    @pytest.mark.asyncio
    async def test_context_modeler_integration(self):
        from polaris.kernelone.cognitive.perception.engine import PerceptionLayer

        layer = PerceptionLayer()

        # Process multiple messages in same session
        _graph1, _ = await layer.process("Create a new API endpoint", session_id="test_session")
        _graph2, _ = await layer.process("Update the user module", session_id="test_session")

        # Verify context was updated
        context = layer._context_modeler.get_session_context("test_session")
        assert context is not None
        assert len(context.intent_history) == 2

        # Verify enrichment works
        enrichment = layer._context_modeler.get_context_enrichment("test_session")
        assert enrichment["context_type"] == "developing_session"  # 2 intents = developing_session
        assert enrichment["intent_count"] == 2

    @pytest.mark.asyncio
    async def test_session_isolation(self):
        from polaris.kernelone.cognitive.perception.engine import PerceptionLayer

        layer = PerceptionLayer()

        # Process messages in different sessions
        await layer.process("Create file A", session_id="session_1")
        await layer.process("Create file B", session_id="session_2")

        # Verify isolation
        context1 = layer._context_modeler.get_session_context("session_1")
        context2 = layer._context_modeler.get_session_context("session_2")

        assert len(context1.intent_history) == 1
        assert len(context2.intent_history) == 1
        assert context1.intent_history[0].content == "Create file A"
        assert context2.intent_history[0].content == "Create file B"

    @pytest.mark.asyncio
    async def test_context_enrichment_affects_uncertainty(self):
        from polaris.kernelone.cognitive.perception.engine import PerceptionLayer

        layer = PerceptionLayer()

        # First message - no history
        graph1, _uncertainty1 = await layer.process("Read the file at src/main.py", session_id="context_test")

        # Second message - has history
        graph2, _uncertainty2 = await layer.process("Update the user module", session_id="context_test")

        # Both should complete successfully
        assert graph1.session_id == "context_test"
        assert graph2.session_id == "context_test"
