"""Akashic Nexus: Episodic Memory Implementation.

Implements EpisodicMemoryPort for session-level storage.
This module provides persistent session history with episode sealing.

Architecture:
    - JSONL-based persistence for turns and episodes
    - Turn-level storage with sequential indexing
    - Episode sealing with summaries
    - Integration points for RoleContextCompressor for episode summaries
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from polaris.kernelone.fs.jsonl.locking import file_lock
from polaris.kernelone.storage import resolve_runtime_path

from .protocols import EpisodicMemoryPort

logger = logging.getLogger(__name__)


@dataclass
class TurnRecord:
    """A single turn in episodic memory."""

    turn_id: str
    turn_index: int
    messages: list[dict[str, Any]]
    created_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EpisodeRecord:
    """A sealed episode (session) in episodic memory."""

    episode_id: str
    session_id: str
    summary: str
    start_turn: int
    end_turn: int
    created_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


class AkashicEpisodicMemory:
    """Episodic memory with JSONL-based persistence.

    This implementation:
    - Stores individual turns with full message history
    - Supports episode sealing with summaries
    - Uses JSONL for persistence (compatible with MemoryStore patterns)

    Usage::

        episodic = AkashicEpisodicMemory(workspace=".")
        await episodic.store_turn(0, [{"role": "user", "content": "Hello"}])
        await episodic.seal_episode("session_1", "User greeted the assistant")
    """

    def __init__(
        self,
        workspace: str = ".",
        *,
        turns_file: str | None = None,
        episodes_file: str | None = None,
        enable_contextos_integration: bool = True,
    ) -> None:
        self._workspace = str(workspace or ".")
        self._turns_file = turns_file or resolve_runtime_path(self._workspace, "runtime/episodic/turns.jsonl")
        self._episodes_file = episodes_file or resolve_runtime_path(self._workspace, "runtime/episodic/episodes.jsonl")
        self._enable_contextos = enable_contextos_integration

        # In-memory cache
        self._turns_cache: dict[int, TurnRecord] = {}
        self._episodes_cache: dict[str, EpisodeRecord] = {}

        # Thread safety for async access
        self._lock: asyncio.Lock = asyncio.Lock()

        # Lazy-loaded ContextOS components
        self._contextos_policy: Any = None
        self._contextos_runtime: Any = None

        # Ensure directories exist
        os.makedirs(os.path.dirname(self._turns_file), exist_ok=True)

        # Load existing data
        self._load_turns()
        self._load_episodes()

    def _load_turns(self) -> None:
        """Load turns from JSONL file."""
        if not os.path.exists(self._turns_file):
            return
        try:
            with open(self._turns_file, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if isinstance(data.get("created_at"), str):
                        data["created_at"] = datetime.fromisoformat(data["created_at"])
                    record = TurnRecord(**data)
                    self._turns_cache[record.turn_index] = record
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Failed to load turns from %s: %s", self._turns_file, exc)

    def _load_episodes(self) -> None:
        """Load episodes from JSONL file."""
        if not os.path.exists(self._episodes_file):
            return
        try:
            with open(self._episodes_file, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if isinstance(data.get("created_at"), str):
                        data["created_at"] = datetime.fromisoformat(data["created_at"])
                    record = EpisodeRecord(**data)
                    self._episodes_cache[record.episode_id] = record
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Failed to load episodes from %s: %s", self._episodes_file, exc)

    def _persist_turn(self, record: TurnRecord) -> None:
        """Append a turn record to the JSONL file."""
        os.makedirs(os.path.dirname(self._turns_file), exist_ok=True)
        lock_path = f"{self._turns_file}.lock"
        data = asdict(record)
        # Convert datetime to isoformat string
        if isinstance(data.get("created_at"), datetime):
            data["created_at"] = data["created_at"].isoformat()
        with file_lock(lock_path, timeout_sec=5.0), open(self._turns_file, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def _persist_episode(self, record: EpisodeRecord) -> None:
        """Append an episode record to the JSONL file."""
        os.makedirs(os.path.dirname(self._episodes_file), exist_ok=True)
        lock_path = f"{self._episodes_file}.lock"
        data = asdict(record)
        # Convert datetime to isoformat string
        if isinstance(data.get("created_at"), datetime):
            data["created_at"] = data["created_at"].isoformat()
        with file_lock(lock_path, timeout_sec=5.0), open(self._episodes_file, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    async def store_turn(
        self,
        turn_index: int,
        messages: list[dict[str, Any]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a complete turn's messages."""
        turn_id = f"turn_{int(time.time() * 1000)}_{turn_index}"

        record = TurnRecord(
            turn_id=turn_id,
            turn_index=turn_index,
            messages=list(messages),
            created_at=datetime.now(timezone.utc),
            metadata=metadata or {},
        )

        async with self._lock:
            self._turns_cache[turn_index] = record
        self._persist_turn(record)

        logger.debug("Stored turn %d with %d messages", turn_index, len(messages))
        return turn_id

    async def get_turn(self, turn_index: int) -> dict[str, Any] | None:
        """Retrieve a specific turn by index."""
        async with self._lock:
            if turn_index in self._turns_cache:
                record = self._turns_cache[turn_index]
                return {
                    "turn_id": record.turn_id,
                    "turn_index": record.turn_index,
                    "messages": record.messages,
                    "created_at": record.created_at.isoformat(),
                    "metadata": record.metadata,
                }

        # Try to find in file (may have been evicted from cache)
        if os.path.exists(self._turns_file):
            try:
                with open(self._turns_file, encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        if data.get("turn_index") == turn_index:
                            if isinstance(data.get("created_at"), str):
                                data["created_at"] = datetime.fromisoformat(data["created_at"])
                            return data
            except (json.JSONDecodeError, TypeError):
                pass

        return None

    async def get_range(
        self,
        start_turn: int,
        end_turn: int,
    ) -> list[dict[str, Any]]:
        """Retrieve a range of turns."""
        results = []
        for turn_index in range(start_turn, end_turn + 1):
            turn = await self.get_turn(turn_index)
            if turn is not None:
                results.append(turn)
        return results

    async def seal_episode(
        self,
        session_id: str,
        summary: str,
    ) -> str:
        """Seal an episode (session) with a summary."""
        episode_id = f"episode_{int(time.time() * 1000)}_{session_id}"

        # Find the turn range for this episode
        async with self._lock:
            if self._turns_cache:
                turn_indices = sorted(self._turns_cache.keys())
                start_turn = turn_indices[0] if turn_indices else 0
                end_turn = turn_indices[-1] if turn_indices else 0
            else:
                start_turn = 0
                end_turn = 0

        record = EpisodeRecord(
            episode_id=episode_id,
            session_id=session_id,
            summary=summary,
            start_turn=start_turn,
            end_turn=end_turn,
            created_at=datetime.now(timezone.utc),
        )

        async with self._lock:
            self._episodes_cache[episode_id] = record
        self._persist_episode(record)

        logger.info("Sealed episode %s for session %s", episode_id, session_id)
        return episode_id

    async def get_episode(self, episode_id: str) -> dict[str, Any] | None:
        """Retrieve a specific episode by ID."""
        async with self._lock:
            if episode_id in self._episodes_cache:
                record = self._episodes_cache[episode_id]
                return {
                    "episode_id": record.episode_id,
                    "session_id": record.session_id,
                    "summary": record.summary,
                    "start_turn": record.start_turn,
                    "end_turn": record.end_turn,
                    "created_at": record.created_at.isoformat(),
                    "metadata": record.metadata,
                }

        # Try to find in file
        if os.path.exists(self._episodes_file):
            try:
                with open(self._episodes_file, encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        if data.get("episode_id") == episode_id:
                            if isinstance(data.get("created_at"), str):
                                data["created_at"] = datetime.fromisoformat(data["created_at"])
                            return data
            except (json.JSONDecodeError, TypeError):
                pass

        return None

    async def get_recent_episodes(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get the most recent episodes."""
        async with self._lock:
            episodes = sorted(
                self._episodes_cache.values(),
                key=lambda e: e.created_at,
                reverse=True,
            )
            return [
                {
                    "episode_id": e.episode_id,
                    "session_id": e.session_id,
                    "summary": e.summary,
                    "start_turn": e.start_turn,
                    "end_turn": e.end_turn,
                    "created_at": e.created_at.isoformat(),
                }
                for e in episodes[:limit]
            ]

    def get_status(self) -> dict[str, Any]:
        """Get episodic memory status (sync snapshot)."""
        # Best-effort snapshot without blocking
        return {
            "type": "akashic_episodic",
            "turns_cached": len(self._turns_cache),
            "episodes_cached": len(self._episodes_cache),
            "turns_file": self._turns_file,
            "episodes_file": self._episodes_file,
            "contextos_integration": self._enable_contextos,
        }


# Type annotation
AkashicEpisodicMemory.__protocol__ = EpisodicMemoryPort  # type: ignore[attr-defined]


__all__ = [
    "AkashicEpisodicMemory",
    "EpisodeRecord",
    "TurnRecord",
]
