"""Cognitive Session Context - Manages per-session cognitive state."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name
from polaris.kernelone.cognitive.personality.posture import InteractionPosture
from polaris.kernelone.cognitive.personality.traits import TraitProfile


@dataclass(frozen=True)
class ConversationTurn:
    """A single turn in the conversation."""

    turn_id: str
    role_id: str
    message: str
    intent_type: str
    confidence: float
    execution_path: str
    response: str | None
    timestamp: str
    blocked: bool = False
    block_reason: str | None = None


@dataclass(frozen=True)
class CognitiveContext:
    """Session context for cognitive processing."""

    session_id: str
    role_id: str
    trait_profile: TraitProfile
    interaction_posture: InteractionPosture
    conversation_history: tuple[ConversationTurn, ...] = field(default_factory=tuple)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class CognitiveSessionManager:
    """
    Manages cognitive session state.

    Each session has a single CognitiveContext that persists across turns.
    Supports optional disk persistence via workspace.
    Implements LRU eviction with TTL-based cleanup to prevent unbounded memory growth.
    """

    DEFAULT_MAX_SESSIONS = 1000
    DEFAULT_SESSION_TTL_SECONDS = 3600.0
    DEFAULT_CLEANUP_INTERVAL_SECONDS = 300.0

    def __init__(
        self,
        workspace: str | None = None,
        max_history_size: int = 100,
        max_sessions: int | None = None,
        session_ttl_seconds: float | None = None,
        cleanup_interval_seconds: float | None = None,
    ) -> None:
        self._sessions: OrderedDict[str, tuple[CognitiveContext, float]] = OrderedDict()
        self._workspace = workspace
        self._max_history_size = max_history_size
        self._max_sessions = max(1, max_sessions if max_sessions is not None else self.DEFAULT_MAX_SESSIONS)
        self._session_ttl = max(
            60.0, session_ttl_seconds if session_ttl_seconds is not None else self.DEFAULT_SESSION_TTL_SECONDS
        )
        self._cleanup_interval = (
            cleanup_interval_seconds if cleanup_interval_seconds is not None else self.DEFAULT_CLEANUP_INTERVAL_SECONDS
        )
        self._sessions_dir: Path | None = None
        self._lock = threading.RLock()
        self._cleanup_thread: threading.Thread | None = None
        self._stop_cleanup = threading.Event()
        if workspace:
            metadata_dir = get_workspace_metadata_dir_name()
            self._sessions_dir = Path(workspace) / metadata_dir / "cognitive_sessions"
            self._sessions_dir.mkdir(parents=True, exist_ok=True)
            self._load_sessions_from_disk()
        self._start_cleanup_thread()

    def _session_file_path(self, session_id: str) -> Path | None:
        """Get the file path for a session."""
        if self._sessions_dir is None:
            return None
        return self._sessions_dir / f"{session_id}.json"

    def _load_sessions_from_disk(self) -> None:
        """Load existing sessions from disk."""
        if self._sessions_dir is None:
            return
        _logger = logging.getLogger(__name__)
        current_time = time.time()
        for session_file in self._sessions_dir.glob("*.json"):
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
                ctx = self._reconstruct_context(data)
                if ctx:
                    self._sessions[ctx.session_id] = (ctx, current_time)
            except (RuntimeError, ValueError):
                _logger.exception("Failed to load session file: %s", session_file)

    def _start_cleanup_thread(self) -> None:
        """Start background cleanup thread for expired sessions."""
        if self._cleanup_thread is not None:
            return
        self._stop_cleanup.clear()
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def _stop_cleanup_thread(self) -> None:
        """Stop background cleanup thread."""
        if self._cleanup_thread is None:
            return
        self._stop_cleanup.set()
        self._cleanup_thread.join(timeout=5.0)
        self._cleanup_thread = None

    def _cleanup_loop(self) -> None:
        """Background loop that periodically evicts expired sessions."""
        while not self._stop_cleanup.is_set():
            self._stop_cleanup.wait(timeout=self._cleanup_interval)
            if self._stop_cleanup.is_set():
                break
            self._evict_expired_sessions()

    def _evict_expired_sessions(self) -> None:
        """Evict sessions that have exceeded their TTL."""
        with self._lock:
            current_time = time.time()
            expired_keys = [
                sid
                for sid, (ctx, last_access) in self._sessions.items()
                if current_time - last_access > self._session_ttl
            ]
            for sid in expired_keys:
                del self._sessions[sid]

            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)

    def _reconstruct_context(self, data: dict[str, Any]) -> CognitiveContext | None:
        """Reconstruct CognitiveContext from serialized data."""
        try:
            history = []
            for turn_data in data.get("conversation_history", []):
                turn = ConversationTurn(
                    turn_id=turn_data["turn_id"],
                    role_id=turn_data["role_id"],
                    message=turn_data["message"],
                    intent_type=turn_data["intent_type"],
                    confidence=turn_data["confidence"],
                    execution_path=turn_data["execution_path"],
                    response=turn_data.get("response"),
                    timestamp=turn_data["timestamp"],
                    blocked=turn_data.get("blocked", False),
                    block_reason=turn_data.get("block_reason"),
                )
                history.append(turn)

            from polaris.kernelone.cognitive.personality.traits import get_trait_profile_for_role

            profile = get_trait_profile_for_role(data.get("role_id", "director"))
            if profile is None:
                return None
            posture = InteractionPosture(data.get("posture", "transparent_reasoning"))

            return CognitiveContext(
                session_id=data["session_id"],
                role_id=data.get("role_id", "director"),
                trait_profile=profile,
                interaction_posture=posture,
                conversation_history=tuple(history),
                created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            )
        except (RuntimeError, ValueError, KeyError):
            _logger = logging.getLogger(__name__)
            _logger.exception("Failed to reconstruct context from data")
            return None

    def _persist_session(self, session_id: str, ctx: CognitiveContext) -> None:
        """Persist session to disk using atomic write."""
        path = self._session_file_path(session_id)
        if path is None:
            return
        _logger = logging.getLogger(__name__)
        try:
            data = {
                "session_id": ctx.session_id,
                "role_id": ctx.role_id,
                "posture": ctx.interaction_posture.value,
                "created_at": ctx.created_at,
                "conversation_history": [
                    {
                        "turn_id": t.turn_id,
                        "role_id": t.role_id,
                        "message": t.message,
                        "intent_type": t.intent_type,
                        "confidence": t.confidence,
                        "execution_path": t.execution_path,
                        "response": t.response,
                        "timestamp": t.timestamp,
                        "blocked": t.blocked,
                        "block_reason": t.block_reason,
                    }
                    for t in ctx.conversation_history
                ],
            }
            content = json.dumps(data, ensure_ascii=False)
        except (RuntimeError, ValueError):
            _logger.exception("Failed to serialize session %s to JSON", session_id)
            return
        # Atomic write: temp file + rename to avoid corruption on crash
        # NOTE: rename failure orphans the temp file — clean it up on error
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
                temp_path = Path(f.name)
            # Retry loop for Windows replace() concurrency issues
            max_attempts = 3
            for attempt in range(max_attempts):
                try:
                    temp_path.replace(path)
                    break
                except PermissionError:
                    if attempt < max_attempts - 1:
                        time.sleep(0.1 * (2**attempt))  # Exponential backoff: 0.1s, 0.2s
                    else:
                        raise
        except (RuntimeError, ValueError):
            _logger.exception("Failed to persist session %s to disk: %s", session_id, path)
            # Clean up orphaned temp file to prevent disk leakage
            if temp_path is not None and temp_path.exists():
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass  # Already cleaned up, ignore
                except (RuntimeError, ValueError) as cleanup_err:
                    _logger.warning("Failed to clean up temp file %s: %s", temp_path, cleanup_err)

    def get_or_create_session(
        self,
        session_id: str,
        role_id: str = "director",
        trait_profile: TraitProfile | None = None,
        posture: InteractionPosture | None = None,
    ) -> CognitiveContext:
        """Get existing session or create a new one."""
        with self._lock:
            if session_id in self._sessions:
                ctx, _ = self._sessions[session_id]
                self._sessions.move_to_end(session_id)
                return ctx

            from polaris.kernelone.cognitive.personality.traits import get_trait_profile_for_role

            profile = trait_profile or get_trait_profile_for_role(role_id)
            if profile is None:
                from polaris.kernelone.cognitive.personality.traits import ROLE_TRAIT_PROFILES

                profile = ROLE_TRAIT_PROFILES.get("director")
                if profile is None:
                    from polaris.kernelone.cognitive.personality.traits import CognitiveTrait, TraitProfile

                    profile = TraitProfile(
                        enabled_traits={CognitiveTrait.CAUTIOUS},
                        dominant_trait=CognitiveTrait.CAUTIOUS,
                        trait_weights={},
                    )

            if posture is None:
                from polaris.kernelone.cognitive.personality.posture import select_posture_for_intent

                posture = select_posture_for_intent(
                    intent_type="default",
                    role_id=role_id,
                    stakes_level="medium",
                    uncertainty_level=0.5,
                ).primary_posture

            ctx = CognitiveContext(
                session_id=session_id,
                role_id=role_id,
                trait_profile=profile,
                interaction_posture=posture,
                conversation_history=(),
            )

            while len(self._sessions) >= self._max_sessions:
                self._sessions.popitem(last=False)

            current_time = time.time()
            self._sessions[session_id] = (ctx, current_time)
            self._sessions.move_to_end(session_id)
            self._persist_session(session_id, ctx)
            return ctx

    def update_session(self, session_id: str, turn: ConversationTurn) -> None:
        """Update session with a new conversation turn."""
        with self._lock:
            if session_id not in self._sessions:
                return

            ctx, _ = self._sessions[session_id]
            updated_history = (*ctx.conversation_history, turn)
            if len(updated_history) > self._max_history_size:
                updated_history = updated_history[-self._max_history_size :]
            updated_ctx = CognitiveContext(
                session_id=ctx.session_id,
                role_id=ctx.role_id,
                trait_profile=ctx.trait_profile,
                interaction_posture=ctx.interaction_posture,
                conversation_history=updated_history,
                created_at=ctx.created_at,
            )
            current_time = time.time()
            self._sessions[session_id] = (updated_ctx, current_time)
            self._sessions.move_to_end(session_id)
            self._persist_session(session_id, updated_ctx)

    def get_session(self, session_id: str) -> CognitiveContext | None:
        """Get session by ID."""
        with self._lock:
            if session_id not in self._sessions:
                return None
            ctx, _last_access = self._sessions[session_id]
            current_time = time.time()
            self._sessions[session_id] = (ctx, current_time)
            self._sessions.move_to_end(session_id)
            return ctx

    def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        with self._lock:
            self._sessions.pop(session_id, None)
        path = self._session_file_path(session_id)
        if path and path.exists():
            path.unlink(missing_ok=True)


# Global session managers - workspace isolation
_global_managers: dict[str, CognitiveSessionManager] = {}


def get_session_manager(workspace: str | None = None) -> CognitiveSessionManager:
    """Get the global session manager instance for the given workspace."""
    global _global_managers

    # Use empty string as key for None workspace to maintain consistency
    key = workspace if workspace is not None else ""

    if key not in _global_managers:
        _global_managers[key] = CognitiveSessionManager(workspace=workspace)

    return _global_managers[key]
