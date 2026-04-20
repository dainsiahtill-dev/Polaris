"""Context Modeler - Session context management and pattern detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from polaris.kernelone.cognitive.perception.models import IntentNode


@dataclass
class SessionContext:
    """Session context containing intent history and behavior patterns."""

    session_id: str
    intent_history: list[IntentNode] = field(default_factory=list)
    behavior_patterns: dict[str, Any] = field(default_factory=dict)
    last_updated: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class IntentGraphStore:
    """Abstract store for intent graphs (placeholder for future persistence)."""

    def get_session_context(self, session_id: str) -> SessionContext | None:
        """Retrieve session context from store."""
        return None

    def save_session_context(self, context: SessionContext) -> None:
        """Save session context to store."""
        pass


class ContextModeler:
    """
    Manages session context, detects behavior patterns, and provides
    context enrichment for intent understanding.
    """

    def __init__(self, store: IntentGraphStore | None = None) -> None:
        """
        Initialize ContextModeler.

        Args:
            store: Optional persistent store for session contexts.
        """
        self._store = store
        self._session_contexts: dict[str, SessionContext] = {}

    def _get_or_create_context(self, session_id: str) -> SessionContext:
        """Get existing context or create new one."""
        if session_id not in self._session_contexts:
            # Try to load from store if available
            if self._store:
                stored = self._store.get_session_context(session_id)
                if stored:
                    self._session_contexts[session_id] = stored
                    return stored

            # Create new context
            self._session_contexts[session_id] = SessionContext(session_id=session_id)

        return self._session_contexts[session_id]

    def _update_timestamp(self, context: SessionContext) -> None:
        """Update the last_updated timestamp."""
        context.last_updated = datetime.now(timezone.utc).isoformat()

    def update_context(self, session_id: str, intent: IntentNode) -> None:
        """
        Update session context with a new intent.

        Args:
            session_id: Unique session identifier.
            intent: The intent node to add to history.
        """
        context = self._get_or_create_context(session_id)
        context.intent_history.append(intent)
        self._update_timestamp(context)

        # Persist to store if available
        if self._store:
            self._store.save_session_context(context)

    def get_relevant_history(
        self,
        session_id: str,
        current_intent: str,
        limit: int = 5,
    ) -> list[IntentNode]:
        """
        Get relevant historical intents for the current intent.

        Relevance is determined by intent type similarity.

        Args:
            session_id: Unique session identifier.
            current_intent: The current intent type to match against.
            limit: Maximum number of historical intents to return.

        Returns:
            List of relevant intent nodes, most recent first.
        """
        context = self._get_or_create_context(session_id)

        if not context.intent_history:
            return []

        # Score intents by relevance (same type = higher score)
        scored_intents: list[tuple[float, IntentNode]] = []
        for intent in reversed(context.intent_history):
            score = 0.0
            if intent.intent_type == current_intent:
                score = 1.0
            elif intent.intent_type in ("deep", "unstated"):
                score = 0.5  # Deep/unstated intents are generally relevant
            scored_intents.append((score, intent))

        # Sort by score descending, then take limit
        scored_intents.sort(key=lambda x: x[0], reverse=True)
        return [intent for _, intent in scored_intents[:limit]]

    def detect_patterns(self, session_id: str) -> dict[str, Any]:
        """
        Detect user behavior patterns from intent history.

        Args:
            session_id: Unique session identifier.

        Returns:
            Dictionary of detected patterns with their statistics.
        """
        context = self._get_or_create_context(session_id)
        history = context.intent_history

        if len(history) < 2:
            return {"status": "insufficient_data"}

        patterns: dict[str, Any] = {
            "status": "analyzed",
            "total_intents": len(history),
            "intent_type_distribution": {},
            "confidence_trend": None,
            "repetition_pattern": None,
        }

        # Count intent types
        type_counts: dict[str, int] = {}
        for intent in history:
            type_counts[intent.intent_type] = type_counts.get(intent.intent_type, 0) + 1
        patterns["intent_type_distribution"] = type_counts

        # Calculate confidence trend
        if len(history) >= 3:
            recent_confidence = sum(h.confidence for h in history[-3:]) / 3
            older_confidence = sum(h.confidence for h in history[:-3]) / max(1, len(history) - 3)
            if recent_confidence > older_confidence + 0.1:
                patterns["confidence_trend"] = "improving"
            elif recent_confidence < older_confidence - 0.1:
                patterns["confidence_trend"] = "declining"
            else:
                patterns["confidence_trend"] = "stable"

        # Detect repetition pattern
        if len(history) >= 3:
            recent_types = [h.intent_type for h in history[-3:]]
            if len(set(recent_types)) == 1:
                patterns["repetition_pattern"] = {
                    "type": "single_intent_repetition",
                    "intent_type": recent_types[0],
                    "count": 3,
                }
            elif len(set(recent_types)) == 2:
                patterns["repetition_pattern"] = {
                    "type": "alternating",
                    "intent_types": list(set(recent_types)),
                }

        # Update stored patterns
        context.behavior_patterns = patterns
        self._update_timestamp(context)

        return patterns

    def get_context_enrichment(self, session_id: str) -> dict[str, Any]:
        """
        Provide context enrichment for the current session.

        Returns contextual information that can enhance intent understanding.

        Args:
            session_id: Unique session identifier.

        Returns:
            Dictionary of enrichment data.
        """
        context = self._get_or_create_context(session_id)
        history = context.intent_history

        enrichment: dict[str, Any] = {
            "session_id": session_id,
            "has_history": len(history) > 0,
            "intent_count": len(history),
        }

        if not history:
            enrichment["context_type"] = "new_session"
            return enrichment

        # Determine context type based on history
        if len(history) == 1:
            enrichment["context_type"] = "early_session"
        elif len(history) <= 5:
            enrichment["context_type"] = "developing_session"
        else:
            enrichment["context_type"] = "established_session"

        # Add recent context summary
        recent_intents = history[-3:] if len(history) >= 3 else history
        enrichment["recent_intent_types"] = [i.intent_type for i in recent_intents]
        enrichment["recent_confidence_avg"] = sum(i.confidence for i in recent_intents) / len(recent_intents)

        # Add pattern insights if available
        if context.behavior_patterns:
            patterns = context.behavior_patterns
            if patterns.get("status") == "analyzed":
                enrichment["detected_patterns"] = {
                    "confidence_trend": patterns.get("confidence_trend"),
                    "repetition": patterns.get("repetition_pattern") is not None,
                }

        # Add dominant intent type
        if history:
            type_counts: dict[str, int] = {}
            for intent in history:
                type_counts[intent.intent_type] = type_counts.get(intent.intent_type, 0) + 1
            dominant_type = max(type_counts, key=type_counts.get)  # type: ignore[arg-type]
            enrichment["dominant_intent_type"] = dominant_type

        return enrichment

    def get_session_context(self, session_id: str) -> SessionContext | None:
        """
        Get the full session context.

        Args:
            session_id: Unique session identifier.

        Returns:
            SessionContext if exists, None otherwise.
        """
        return self._session_contexts.get(session_id)

    def clear_session(self, session_id: str) -> bool:
        """
        Clear session context.

        Args:
            session_id: Unique session identifier.

        Returns:
            True if session was cleared, False if not found.
        """
        if session_id in self._session_contexts:
            del self._session_contexts[session_id]
            return True
        return False
