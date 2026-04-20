"""Transcript archival service for Polaris backend.

Provides persistent storage of conversation history for audit and replay.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)


class MessageRole(str, Enum):
    """Message roles in conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class TranscriptMessage:
    """A single message in the transcript."""

    role: MessageRole
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "message_id": self.message_id,
            "role": self.role.value,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TranscriptMessage:
        """Create from dictionary."""
        return cls(
            message_id=data.get("message_id", str(uuid.uuid4())[:8]),
            role=MessageRole(data["role"]),
            content=data["content"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            metadata=data.get("metadata", {}),
        )


@dataclass
class TranscriptSession:
    """A transcript session."""

    session_id: str
    started_at: datetime
    ended_at: datetime | None = None
    messages: list[TranscriptMessage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_message(
        self,
        role: MessageRole | str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> TranscriptMessage:
        """Add a message to the session.

        Args:
            role: Message role
            content: Message content
            metadata: Optional metadata

        Returns:
            The created message
        """
        if isinstance(role, str):
            role = MessageRole(role)

        message = TranscriptMessage(
            role=role,
            content=content,
            metadata=metadata or {},
        )
        self.messages.append(message)
        return message

    def end_session(self) -> None:
        """Mark session as ended."""
        self.ended_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "session_id": self.session_id,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "metadata": self.metadata,
            "messages": [m.to_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TranscriptSession:
        """Create from dictionary."""
        session = cls(
            session_id=data["session_id"],
            started_at=datetime.fromisoformat(data["started_at"]),
            ended_at=datetime.fromisoformat(data["ended_at"]) if data.get("ended_at") else None,
            metadata=data.get("metadata", {}),
        )
        session.messages = [TranscriptMessage.from_dict(m) for m in data.get("messages", [])]
        return session


class TranscriptService:
    """Service for managing conversation transcripts.

    Provides:
    - Persistent transcript storage
    - Session-based organization
    - Search and retrieval
    - Export functionality
    """

    def __init__(self, transcripts_dir: Path | str) -> None:
        """Initialize transcript service.

        Args:
            transcripts_dir: Directory to store transcripts
        """
        self.transcripts_dir = Path(transcripts_dir)
        self.transcripts_dir.mkdir(parents=True, exist_ok=True)

        self._current_session: TranscriptSession | None = None

    def start_session(
        self,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TranscriptSession:
        """Start a new transcript session.

        Args:
            session_id: Optional session ID (generated if not provided)
            metadata: Optional session metadata

        Returns:
            The new session
        """
        # End current session if exists
        if self._current_session:
            self.end_session()

        session = TranscriptSession(
            session_id=session_id or str(uuid.uuid4())[:12],
            started_at=datetime.now(timezone.utc),
            metadata=metadata or {},
        )

        self._current_session = session
        return session

    def end_session(self) -> None:
        """End current session and persist."""
        if self._current_session:
            self._current_session.end_session()
            self._persist_session(self._current_session)
            self._current_session = None

    def record_message(
        self,
        role: MessageRole | str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> TranscriptMessage | None:
        """Record a message in the current session.

        Args:
            role: Message role
            content: Message content
            metadata: Optional metadata

        Returns:
            Created message or None if no session
        """
        if not self._current_session:
            # Auto-start session
            self.start_session()

        if not self._current_session:
            return None

        return self._current_session.add_message(role, content, metadata)

    def record_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: str,
    ) -> None:
        """Record a tool call and its result.

        Args:
            tool_name: Name of the tool
            arguments: Tool arguments
            result: Tool result
        """
        content = f"Tool: {tool_name}\nArguments: {json.dumps(arguments, indent=2)}\nResult: {result}"
        self.record_message(
            MessageRole.TOOL,
            content,
            metadata={"tool_name": tool_name, "arguments": arguments},
        )

    def get_messages(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get messages from current session.

        Args:
            limit: Maximum number of messages to return

        Returns:
            List of message dictionaries
        """
        if not self._current_session:
            return []
        messages = self._current_session.messages[-limit:]
        return [
            {
                "role": str(msg.role.value) if hasattr(msg.role, "value") else str(msg.role),
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
                "metadata": msg.metadata or {},
            }
            for msg in messages
        ]

    def get_current_session(self) -> TranscriptSession | None:
        """Get current active session.

        Returns:
            Current session or None
        """
        return self._current_session

    def load_session(self, session_id: str) -> TranscriptSession | None:
        """Load a session from storage.

        Args:
            session_id: Session ID

        Returns:
            Session or None if not found
        """
        file_path = self._get_session_path(session_id)

        if not file_path.exists():
            return None

        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            return TranscriptSession.from_dict(data)
        except (RuntimeError, ValueError):
            logger.exception("load_session failed: session_id=%s", session_id)
            return None

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all available sessions.

        Returns:
            List of session metadata
        """
        sessions = []

        for file_path in sorted(self.transcripts_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                sessions.append(
                    {
                        "session_id": data["session_id"],
                        "started_at": data["started_at"],
                        "ended_at": data.get("ended_at"),
                        "message_count": len(data.get("messages", [])),
                    }
                )
            except (RuntimeError, ValueError):
                logger.exception("list_sessions: skipped file=%s", file_path)
                continue

        return sessions

    def search(
        self,
        query: str,
        role: MessageRole | None = None,
    ) -> Iterator[TranscriptMessage]:
        """Search for messages containing query.

        Args:
            query: Search query
            role: Optional role filter

        Yields:
            Matching messages
        """
        for session_info in self.list_sessions():
            session = self.load_session(session_info["session_id"])
            if not session:
                continue

            for message in session.messages:
                if role and message.role != role:
                    continue

                if query.lower() in message.content.lower():
                    yield message

    def export_session(
        self,
        session_id: str,
        format: str = "json",
    ) -> str:
        """Export a session in various formats.

        Args:
            session_id: Session ID
            format: Export format (json, markdown, txt)

        Returns:
            Exported content
        """
        session = self.load_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        if format == "json":
            return json.dumps(session.to_dict(), indent=2)

        elif format == "markdown":
            lines = [f"# Transcript: {session.session_id}\n"]
            lines.append(f"Started: {session.started_at}\n")

            for msg in session.messages:
                role_emoji = {
                    MessageRole.SYSTEM: "⚙️",
                    MessageRole.USER: "👤",
                    MessageRole.ASSISTANT: "🤖",
                    MessageRole.TOOL: "🔧",
                }.get(msg.role, "💬")

                lines.append(f"\n## {role_emoji} {msg.role.value} ({msg.timestamp})\n")
                lines.append(f"{msg.content}\n")

            return "".join(lines)

        elif format == "txt":
            lines = [f"Transcript: {session.session_id}\n"]
            lines.append(f"Started: {session.started_at}\n")
            lines.append("=" * 50 + "\n")

            for msg in session.messages:
                lines.append(f"\n[{msg.role.value}] {msg.timestamp}\n")
                lines.append(f"{msg.content}\n")

            return "".join(lines)

        else:
            raise ValueError(f"Unknown format: {format}")

    def _get_session_path(self, session_id: str) -> Path:
        """Get file path for a session."""
        return self.transcripts_dir / f"{session_id}.json"

    def _persist_session(self, session: TranscriptSession) -> None:
        """Persist a session to disk."""
        file_path = self._get_session_path(session.session_id)
        file_path.write_text(
            json.dumps(session.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


# Global instance
_transcript_service: TranscriptService | None = None


def get_transcript_service(transcripts_dir: Path | str | None = None) -> TranscriptService:
    """Get or create global transcript service.

    Args:
        transcripts_dir: Directory for transcripts (uses default if None)

    Returns:
        TranscriptService instance
    """
    global _transcript_service

    if _transcript_service is None:
        if transcripts_dir is None:
            transcripts_dir = Path.cwd() / ".transcripts"
        _transcript_service = TranscriptService(transcripts_dir)

    return _transcript_service


def reset_transcript_service() -> None:
    """Reset global transcript service (for testing)."""
    global _transcript_service
    _transcript_service = None
