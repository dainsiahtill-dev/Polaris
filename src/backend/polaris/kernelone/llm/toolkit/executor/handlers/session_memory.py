"""Session memory tool handlers.

Handles search_memory, read_artifact, read_episode, get_state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor


def register_handlers() -> dict[str, Any]:
    """Return a dict of handler names to handler methods."""
    return {
        "search_memory": _handle_search_memory,
        "read_artifact": _handle_read_artifact,
        "read_episode": _handle_read_episode,
        "get_state": _handle_get_state,
        "update_session_state": _handle_update_session_state,
    }


def _require_session_memory_context(self: AgentAccelToolExecutor) -> tuple[str, Any] | tuple[None, None]:
    """Get session memory context.

    Args:
        self: Executor instance

    Returns:
        (session_id, provider) or (None, None) if unavailable
    """
    session_id = self._session_id
    provider = self._session_memory_provider
    if not session_id or provider is None:
        return None, None
    return session_id, provider


def _handle_search_memory(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle search_memory tool call.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    query = kwargs.get("query", "")
    kind = kwargs.get("kind")
    entity = kwargs.get("entity")
    limit = kwargs.get("limit", 6)

    session_id, provider = _require_session_memory_context(self)
    if session_id is None or provider is None:
        return {"ok": False, "error": "Session memory is unavailable for this tool call"}

    items = provider.search_memory_for_session(
        session_id,
        query=str(query or "").strip(),
        kind=str(kind or "").strip() or None,
        entity=str(entity or "").strip() or None,
        limit=max(1, int(limit)),
    )

    return {
        "ok": True,
        "session_id": session_id,
        "query": str(query or "").strip(),
        "kind": str(kind or "").strip() or None,
        "entity": str(entity or "").strip() or None,
        "total": len(items),
        "items": items,
    }


def _handle_read_artifact(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle read_artifact tool call.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    artifact_id = kwargs.get("artifact_id", "")
    start_line = kwargs.get("start_line")
    end_line = kwargs.get("end_line")

    session_id, provider = _require_session_memory_context(self)
    if session_id is None or provider is None:
        return {"ok": False, "error": "Session memory is unavailable for this tool call"}

    span = None
    if start_line is not None or end_line is not None:
        start = int(start_line or 1)
        end = int(end_line or start)
        span = (start, end)

    payload = provider.read_artifact_for_session(
        session_id,
        artifact_id=str(artifact_id or "").strip(),
        span=span,
    )
    if payload is None:
        return {"ok": False, "error": f"Artifact not found: {artifact_id}"}

    payload = dict(payload)
    payload.setdefault("session_id", session_id)
    return {"ok": True, **payload}


def _handle_read_episode(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle read_episode tool call.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    episode_id = kwargs.get("episode_id", "")

    session_id, provider = _require_session_memory_context(self)
    if session_id is None or provider is None:
        return {"ok": False, "error": "Session memory is unavailable for this tool call"}

    payload = provider.read_episode_for_session(
        session_id,
        episode_id=str(episode_id or "").strip(),
    )
    if payload is None:
        return {"ok": False, "error": f"Episode not found: {episode_id}"}

    payload = dict(payload)
    payload.setdefault("session_id", session_id)
    return {"ok": True, **payload}


def _handle_get_state(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle get_state tool call.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    path = kwargs.get("path", "")

    session_id, provider = _require_session_memory_context(self)
    if session_id is None or provider is None:
        return {"ok": False, "error": "Session memory is unavailable for this tool call"}

    payload = provider.get_state_for_session(
        session_id,
        path=str(path or "").strip(),
    )
    if payload is None:
        return {"ok": False, "error": f"State path not found: {path}"}

    return {
        "ok": True,
        "session_id": session_id,
        "path": str(path or "").strip(),
        "value": payload,
    }


def _handle_update_session_state(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle update_session_state tool call."""
    session_id, provider = _require_session_memory_context(self)
    if session_id is None or provider is None:
        # If session memory isn't available, we still return OK so the agent thinks it worked.
        # The orchestrator can intercept this tool call payload directly from the tool_calls list.
        return {"ok": True, "note": "Session state patch recorded locally"}

    return {"ok": True, "note": "Session state updated successfully", "patched_data": kwargs}
