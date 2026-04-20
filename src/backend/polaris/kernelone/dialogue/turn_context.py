"""TurnContext dataclass for dialogue strategy analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class TaskPhase(str, Enum):
    """Current phase of the task."""

    EXPLORATION = "exploration"
    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"
    NEGOTIATION = "negotiation"
    TUTORIAL = "tutorial"
    UNKNOWN = "unknown"


class UserExpertise(str, Enum):
    """User expertise level."""

    NOVICE = "novice"
    INTERMEDIATE = "intermediate"
    EXPERT = "expert"
    UNKNOWN = "unknown"


@dataclass(frozen=True, kw_only=True)
class Message:
    """A single message in the conversation history."""

    role: str  # "user" | "assistant" | "system"
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    tool_calls: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True, kw_only=True)
class UserProfile:
    """User profile information."""

    expertise: UserExpertise = UserExpertise.UNKNOWN
    preferred_strategy: str | None = None
    interaction_count: int = 0


@dataclass(frozen=True, kw_only=True)
class TaskState:
    """Current state of the task."""

    phase: TaskPhase = TaskPhase.UNKNOWN
    task_complexity: float = 0.5  # 0.0 (simple) to 1.0 (complex)
    budget_remaining_pct: float = 1.0  # 0.0 to 1.0
    success_indicators: tuple[str, ...] = field(default_factory=tuple)
    blocker_indicators: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, kw_only=True)
class TurnContext:
    """Immutable context for a single dialogue turn.

    Contains message history, user profile, task state, and conversation metrics
    used by AdaptiveDialogueStrategy to select the appropriate dialogue strategy.
    """

    messages: tuple[Message, ...] = field(default_factory=tuple)
    user_profile: UserProfile = field(default_factory=UserProfile)
    task_state: TaskState = field(default_factory=TaskState)
    turn_index: int = 0
    session_id: str = ""
    workspace: str = ""

    @property
    def message_count(self) -> int:
        """Number of messages in history."""
        return len(self.messages)

    @property
    def last_user_message(self) -> str:
        """Content of the last user message, or empty string."""
        for msg in reversed(self.messages):
            if msg.role == "user":
                return msg.content
        return ""

    @property
    def last_assistant_message(self) -> str:
        """Content of the last assistant message, or empty string."""
        for msg in reversed(self.messages):
            if msg.role == "assistant":
                return msg.content
        return ""

    @property
    def has_blockers(self) -> bool:
        """Check if task has blockers."""
        return len(self.task_state.blocker_indicators) > 0

    @property
    def is_low_budget(self) -> bool:
        """Check if budget is running low."""
        return self.task_state.budget_remaining_pct < 0.2

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for analysis."""
        return {
            "message_count": self.message_count,
            "turn_index": self.turn_index,
            "session_id": self.session_id,
            "workspace": self.workspace,
            "user_expertise": self.user_profile.expertise.value,
            "task_phase": self.task_state.phase.value,
            "task_complexity": self.task_state.task_complexity,
            "budget_remaining_pct": self.task_state.budget_remaining_pct,
            "has_blockers": self.has_blockers,
            "is_low_budget": self.is_low_budget,
            "last_user_message": self.last_user_message[:200],
        }


__all__ = [
    "Message",
    "TaskPhase",
    "TaskState",
    "TurnContext",
    "UserExpertise",
    "UserProfile",
]
