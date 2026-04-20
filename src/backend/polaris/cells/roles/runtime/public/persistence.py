"""Persistence operations for `roles.runtime` cell.

This module contains session persistence, strategy receipt emission,
and history projection logic extracted from RoleRuntimeService.

Wave 1 extraction - E4: Service Layer Lead.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from polaris.kernelone.context import (
    StrategyReceiptEmitter,
    StrategyRunContext,
)
from polaris.kernelone.context.session_continuity import (
    SessionContinuityEngine,
    SessionContinuityRequest,
    history_pairs_to_messages,
    messages_to_history_pairs,
)

if TYPE_CHECKING:
    from pathlib import Path

    from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1

logger = logging.getLogger(__name__)


# ── Session Persistence ───────────────────────────────────────────────────────


def resolve_session_override(session_id: str) -> dict[str, Any] | None:
    """Read session strategy override from roles.session source-of-truth.

    Args:
        session_id: Session identifier to look up.

    Returns:
        Strategy override dict from session context_config, or None if unavailable.
    """
    try:
        from polaris.cells.roles.session.internal.role_session_service import RoleSessionService

        with RoleSessionService() as svc:
            session = svc.get_session(session_id)
            if session is None:
                return None
            raw = session.context_config
            if not raw:
                return None

            cfg: dict[str, Any] = json.loads(raw)
            return cfg.get("strategy_override") or None
    except (RuntimeError, ValueError):
        # Session service may not be initialized; degrade gracefully.
        return None


async def persist_session_turn_state(
    command: ExecuteRoleSessionCommandV1,
    *,
    turn_history: list[tuple[str, str]],
    turn_events_metadata: list[dict[str, Any]] | None = None,
) -> None:
    """Persist role session turn state to roles.session source-of-truth.

    This is the canonical persistence method for session turns. It handles:
    1. Creating new sessions if they don't exist
    2. Adding messages to the session conversation
    3. Updating session context_config with ContextOS projection

    Args:
        command: The session execution command containing session metadata.
        turn_history: List of (role, content) pairs for the turn.
        turn_events_metadata: Optional list of turn event metadata dicts
            for SSOT ContextOS projection continuity.
    """
    session_id = str(command.session_id or "").strip()
    if not session_id:
        return
    try:
        from polaris.cells.roles.session.public import Conversation, RoleSessionService
        from polaris.cells.roles.session.public.contracts import (
            AttachmentMode,
            RoleHostKind,
            SessionState,
            SessionType,
        )

        with RoleSessionService() as svc:
            session = svc.get_session(session_id)
            if session is None:
                # Session doesn't exist yet — create it with the provided session_id.
                # This handles the case where the streaming path receives a command
                # with a pre-assigned session_id (e.g., from benchmark adapter) but
                # no session was created in the DB yet.
                session = Conversation(
                    id=session_id,
                    role=command.role,
                    host_kind=command.host_kind or RoleHostKind.ELECTRON_WORKBENCH.value,
                    session_type=SessionType.WORKBENCH.value,
                    attachment_mode=AttachmentMode.ISOLATED.value,
                    workspace=command.workspace,
                    title=f"{command.role}_runtime_session",
                    context_config=None,
                    capability_profile=None,
                    state=SessionState.ACTIVE.value,
                )
                svc.db.add(session)
                svc.db.commit()
                svc.db.refresh(session)

            # SSOT: Use turn_events_metadata for session persistence (if provided)
            # Retain complete event metadata for ContextOS event sourcing
            events_to_persist = turn_events_metadata or []
            for evt in events_to_persist:
                role = str(evt.get("role") or "user")
                content = str(evt.get("content") or "")
                if not role or not content:
                    continue
                evt_meta = dict(evt.get("metadata") or {})
                svc.add_message(
                    session_id,
                    role=role,
                    content=content,
                    meta={
                        "source": "roles.runtime.turn_events",
                        "event_id": str(evt.get("event_id") or ""),
                        "run_id": command.run_id,
                        "task_id": command.task_id,
                        "stream": bool(command.stream),
                        "kind": evt_meta.get("kind") or "",
                        "dialog_act": evt_meta.get("dialog_act") or "",
                    },
                )

            # Fallback to tuple history (for non-SSOT compatible paths)
            if not events_to_persist and turn_history:
                for role, content in turn_history:
                    svc.add_message(
                        session_id,
                        role=role,
                        content=content,
                        meta={
                            "source": "roles.runtime.turn_history",
                            "run_id": command.run_id,
                            "task_id": command.task_id,
                            "stream": bool(command.stream),
                        },
                    )

            context_config = svc.get_context_config_dict(session_id) or {}
            engine = SessionContinuityEngine()
            existing_pack = context_config.get("session_continuity")
            configured_recent_window = 0
            if isinstance(existing_pack, Mapping):
                try:
                    configured_recent_window = int(existing_pack.get("recent_window_messages") or 0)
                except (TypeError, ValueError):
                    configured_recent_window = 0
            history_limit = configured_recent_window or int(engine.policy.default_history_window_messages)
            history_limit = max(2, min(24, history_limit))

            # SSOT: Use turn_events_metadata to build ContextOS projection
            # turn_events retain complete event metadata (kind, route, dialog_act, source_turns, etc.)
            # SSOT Fix: Merge prior turn_events from session context with current turn_events
            # to maintain transcript_log continuity across turns.
            current_turn_events = tuple(events_to_persist) if events_to_persist else ()
            # Load prior turn_events from command.context (loaded from session in _build_session_request)
            prior_turn_events_raw = command.context.get("session_turn_events") if command.context else None
            # Initialize with empty tuple to avoid else branch (test constraint)
            prior_turn_events: tuple[dict[str, Any], ...] = ()
            if isinstance(prior_turn_events_raw, list) and prior_turn_events_raw:
                # Merge prior events with current events (prior first, then current)
                prior_turn_events = tuple(prior_turn_events_raw)
            combined_turn_events = prior_turn_events + current_turn_events
            # BUG FIX: Deduplicate events by (role, content_hash, kind) to prevent
            # unbounded transcript bloat across turns. Without this, the same
            # assistant content gets injected N times after N turns.
            # Use full content hash (not truncation) to avoid mismerging different
            # tool_results that share a common prefix.
            import hashlib

            _seen_event_keys: set[tuple[str, str, str]] = set()
            deduped_turn_events: list[dict[str, Any]] = []
            for ev in combined_turn_events:
                if not isinstance(ev, dict):
                    continue  # Skip non-dict events (defensive)
                ev_role = str(ev.get("role") or "").strip().lower()
                ev_content = str(ev.get("content") or "").strip()
                ev_kind = str(ev.get("kind") or "").strip()
                # Use SHA-256 hash of full content to avoid 500-char truncation mismerge
                _content_hash = (
                    hashlib.sha256(ev_content.encode("utf-8", errors="replace")).hexdigest()[:32] if ev_content else ""
                )
                _key = (ev_role, _content_hash, ev_kind) if ev_content else None
                if _key and _key in _seen_event_keys:
                    continue
                if _key:
                    _seen_event_keys.add(_key)
                deduped_turn_events.append(ev)
            # BUG FIX: Cap the total stored events to prevent unbounded growth.
            # Keep only the most recent MAX_SESSION_TURN_EVENTS (last N events)
            # so long-running sessions don't accumulate megabytes of event data.
            _MAX_SESSION_TURN_EVENTS = 200
            if len(deduped_turn_events) > _MAX_SESSION_TURN_EVENTS:
                deduped_turn_events = deduped_turn_events[-_MAX_SESSION_TURN_EVENTS:]
            combined_turn_events = tuple(deduped_turn_events)
            projection = await engine.project(
                SessionContinuityRequest(
                    session_id=session_id,
                    role=command.role,
                    workspace=command.workspace,
                    session_title=str(session.title or f"{command.role}_runtime_session"),
                    messages=history_pairs_to_messages(turn_history) if not events_to_persist else (),
                    turn_events=combined_turn_events,
                    session_context_config=context_config,
                    incoming_context=command.context,
                    history_limit=history_limit,
                )
            )
            # SSOT Fix: Accumulate turn_events for next turn's ContextOS projection continuity.
            # transcript_log cannot be persisted (invariant forbids it), so we store
            # the raw turn_events which will be used to reconstruct transcript_log
            # via _merge_transcript on the next turn.
            # IMPORTANT: We accumulate ALL turn events (prior + current) so that
            # _merge_transcript can rebuild the complete transcript_log across turns.
            persisted_ctx = dict(projection.persisted_context_config)
            if combined_turn_events:
                # Store the combined events (prior + current) for next turn's continuity
                persisted_ctx["session_turn_events"] = list(combined_turn_events)
            svc.update_session(
                session_id,
                context_config=persisted_ctx,
            )
    except (RuntimeError, ValueError):
        logger.warning("Failed to persist role session turn state for %s", session_id, exc_info=True)


# ── Strategy Receipt Emission ──────────────────────────────────────────────────


def emit_strategy_receipt(
    run_ctx: StrategyRunContext,
    workspace: str,
) -> Path:
    """Persist a strategy run's receipt to `<metadata_dir>/runtime/strategy_runs/`.

    Args:
        run_ctx: Strategy run context containing identity and metadata.
        workspace: Workspace directory path.

    Returns:
        Path to the written receipt file.
    """
    emitter = StrategyReceiptEmitter(workspace=workspace)
    receipt = run_ctx.emit_receipt(emitter=None)
    return emitter.write_receipt(receipt)


# ── History Projection ─────────────────────────────────────────────────────────


async def project_host_history(
    *,
    session_id: str,
    role: str,
    workspace: str,
    history: tuple[tuple[str, str], ...] | list[tuple[str, str]] | None,
    context: Mapping[str, Any] | None,
    session_context_config: Mapping[str, Any] | None = None,
    history_limit: int = 10,
    session_title: str = "",
) -> tuple[tuple[tuple[str, str], ...], dict[str, Any], dict[str, Any]]:
    """Project host history for session continuity.

    Args:
        session_id: Session identifier.
        role: Role name.
        workspace: Workspace directory path.
        history: Prior conversation history as (role, content) pairs.
        context: Incoming context dict.
        session_context_config: Existing session context configuration.
        history_limit: Maximum number of history messages to include.
        session_title: Session title for projection.

    Returns:
        Tuple of (projected_history, projected_context, persisted_context_config).
    """
    if not history:
        return (), dict(context or {}), dict(session_context_config or {})
    engine = SessionContinuityEngine()
    projection = await engine.project(
        SessionContinuityRequest(
            session_id=session_id,
            role=role,
            workspace=workspace,
            session_title=session_title or f"{role}_runtime_session",
            messages=history_pairs_to_messages(tuple(history)),
            session_context_config=session_context_config,
            incoming_context=context,
            history_limit=history_limit,
        )
    )
    return (
        messages_to_history_pairs(projection.recent_messages),
        dict(projection.prompt_context),
        dict(projection.persisted_context_config),
    )


__all__ = [
    "emit_strategy_receipt",
    "persist_session_turn_state",
    "project_host_history",
    "resolve_session_override",
]
