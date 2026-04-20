"""Projection layer — renders Director console events to TUI widget updates.

This module implements the ProjectionLayer architecture: Director console events
are transformed into WidgetUpdate objects by registered ProjectionRenderers.
The resulting updates drive the TUI widget state.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Noise-filtering helpers
# --------------------------------------------------------------------------

_NOISE_TOKEN_PATTERN = re.compile(r"\b(?:Offset|Region|Size|Spacing|Point)\([^)]*\)")
_NOISE_LINE_PATTERN = re.compile(r"(?m)^\s*(?:Offset|Region|Size|Spacing|Point)\([^)]*\)\s*$")


def _filter_noise(text: str | None) -> str:
    """Remove noisy token patterns (Offset, Region, Size, etc.) from text."""
    if not text:
        return ""
    if "Offset(" not in text and "Region(" not in text and "Size(" not in text:
        return text
    filtered = _NOISE_LINE_PATTERN.sub("", text)
    filtered = _NOISE_TOKEN_PATTERN.sub("", filtered)
    filtered = re.sub(r"[ \t]{2,}", " ", filtered)
    filtered = re.sub(r"\n{3,}", "\n\n", filtered)
    return filtered.strip()


# --------------------------------------------------------------------------
# WidgetUpdate
# --------------------------------------------------------------------------


class WidgetUpdate:
    """A structured widget update produced by the projection layer.

    Attributes:
        action: One of "append", "update", "flush", "clear", "replace".
        payload: Arbitrary data for the target widget.
        widget_id: Target widget identifier (e.g. "detail_pane").
        position: Optional item identifier for "update" actions (e.g. message id).
        timestamp: UTC datetime when this update was created.
    """

    def __init__(
        self,
        action: str,
        payload: Mapping[str, Any] | str,
        *,
        widget_id: str | None = None,
        position: str | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        self.action = action
        self.payload = dict(payload) if isinstance(payload, dict) else payload
        self.widget_id = widget_id
        self.position = position
        self.timestamp = timestamp or datetime.now(tz=UTC)

    @classmethod
    def append(cls, widget_id: str, payload: dict[str, Any]) -> WidgetUpdate:
        """Factory: create an append action."""
        return cls(action="append", payload=payload, widget_id=widget_id)

    @classmethod
    def update(cls, widget_id: str, item_id: str, payload: dict[str, Any]) -> WidgetUpdate:
        """Factory: create an update action targeting a specific item."""
        return cls(action="update", payload=payload, widget_id=widget_id, position=item_id)

    @classmethod
    def clear(cls, widget_id: str) -> WidgetUpdate:
        """Factory: create a clear action."""
        return cls(action="clear", payload="", widget_id=widget_id)

    @classmethod
    def flush(cls, widget_id: str) -> WidgetUpdate:
        """Factory: create a flush action."""
        return cls(action="flush", payload="", widget_id=widget_id)

    def __repr__(self) -> str:
        return f"WidgetUpdate(action={self.action!r}, widget_id={self.widget_id!r}, position={self.position!r})"


# --------------------------------------------------------------------------
# Stub renderers (B1 wave — tasks #11-#15 will implement these properly)
# These are minimal stubs so the import chain in app.py resolves at load time.
# --------------------------------------------------------------------------


class _StubRenderer:
    """Minimal stub base — replace with real ProjectionRenderer implementations."""

    def __init__(self, widget_id: str = "detail_pane", **kwargs: Any) -> None:
        self.widget_id = widget_id
        for k, v in kwargs.items():
            setattr(self, k, v)

    def render(self, event: dict[str, Any]) -> WidgetUpdate | None:
        """Implement ProjectionRenderer protocol."""
        return self.project(event)

    def project(self, event: dict[str, Any]) -> WidgetUpdate | None:
        return None  # pragma: no cover — stub

    def flush(self) -> list[WidgetUpdate]:
        return []  # pragma: no cover — stub


class ContentChunkRenderer(_StubRenderer):
    """Stub: renders content_chunk events to the detail pane.

    TODO (B1-②): implement with console_render markup pipeline
    """

    def project(self, event: dict[str, Any]) -> WidgetUpdate | None:
        return WidgetUpdate(
            action="update",
            payload={"content": event.get("content", "")},
            widget_id=self.widget_id,
        )


class ThinkingChunkRenderer(_StubRenderer):
    """Stub: renders thinking_chunk events (collapsible thinking block).

    TODO (B1-②): implement collapsible thinking section
    """

    def project(self, event: dict[str, Any]) -> WidgetUpdate | None:
        return WidgetUpdate(
            action="update",
            payload={"thinking": event.get("thinking", event.get("content", ""))},
            widget_id=self.widget_id,
        )


class ToolCallRenderer(_StubRenderer):
    """Stub: renders tool_call events.

    TODO (B1-②): implement with ToolProgressIndicator integration
    """

    def project(self, event: dict[str, Any]) -> WidgetUpdate | None:
        return WidgetUpdate(
            action="append",
            payload={**event, "kind": "tool_call"},
            widget_id=self.widget_id,
        )


class ToolResultRenderer(_StubRenderer):
    """Stub: renders tool_result events.

    TODO (B1-②): implement patch/summary overlay
    """

    def project(self, event: dict[str, Any]) -> WidgetUpdate | None:
        return WidgetUpdate(
            action="append",
            payload={"content": str(event.get("result", "")), "kind": "tool_result"},
            widget_id=self.widget_id,
        )


class MessageCompleteRenderer(_StubRenderer):
    """Stub: renders message complete events.

    TODO (B1-②): implement final message commit
    """

    def project(self, event: dict[str, Any]) -> WidgetUpdate | None:
        return WidgetUpdate(
            action="flush",
            payload={"content": event.get("content", ""), "status": "complete"},
            widget_id=self.widget_id,
        )


class ErrorRenderer(_StubRenderer):
    """Stub: renders error events.

    TODO (B1-②): implement error display
    """

    def project(self, event: dict[str, Any]) -> WidgetUpdate | None:
        return WidgetUpdate(
            action="append",
            payload={"content": event.get("error", str(event)), "status": "error"},
            widget_id=self.widget_id,
        )


# --------------------------------------------------------------------------
# ProjectionRenderer protocol
# --------------------------------------------------------------------------


class ProjectionRenderer(Protocol):
    """Protocol for projectors that transform a Director event dict into a WidgetUpdate."""

    def render(self, event: dict[str, Any]) -> WidgetUpdate | None:
        """Transform an event into a widget update. Return None to skip."""
        ...


# --------------------------------------------------------------------------
# ToolProjectionRenderer — tool_call / tool_result events
# --------------------------------------------------------------------------

_TOOL_STATUS_OK = "ok"
_TOOL_STATUS_FAILED = "failed"


class ToolProjectionRenderer:
    """Renders tool_call and tool_result events into WidgetUpdates.

    Maps the Director console event format (with nested ``data`` key) to
    widget-friendly updates with ``role``, ``content``, and ``meta`` fields.
    """

    def render(self, event: dict[str, Any]) -> WidgetUpdate | None:
        """Project a tool event into a WidgetUpdate."""
        event_type = event.get("type", "")
        data = event.get("data")
        if data is None:
            return None
        if not isinstance(data, dict):
            data = {}

        if event_type == "tool_call":
            return self._render_tool_call(data)
        if event_type == "tool_result":
            return self._render_tool_result(data)
        return None

    def _render_tool_call(self, data: dict[str, Any]) -> WidgetUpdate | None:
        tool_name = data.get("tool", "")
        if not tool_name:
            return None
        args = data.get("args", {}) or {}

        # Extract a human-readable detail from common args
        detail = ""
        for key in ("file_path", "path", "command", "target", "content"):
            if key in args:
                detail = str(args[key])
                break

        meta = {"tool_name": tool_name, "detail": detail}
        return WidgetUpdate.append(
            widget_id="detail_pane",
            payload={"role": "tool", "kind": "tool_call", "meta": meta},
        )

    def _render_tool_result(self, data: dict[str, Any]) -> WidgetUpdate:
        tool_name = data.get("tool", "")
        result = data.get("result", {}) or {}
        operation = data.get("operation", "")
        patch = data.get("patch")

        # Determine status
        status = _TOOL_STATUS_OK
        detail_kind = "text"

        if isinstance(result, dict):
            if result.get("success") is False:
                status = _TOOL_STATUS_FAILED
            error = result.get("error")
            if error:
                status = _TOOL_STATUS_FAILED
            # Pick detail kind based on operation or result shape
            if operation in ("edit", "create") or patch:
                detail_kind = "diff"
            elif "bytes_read" in result or "size" in result:
                detail_kind = "json"
        elif isinstance(result, str) and "\n" in result:
            detail_kind = "text"

        meta = {
            "tool_name": tool_name,
            "status": status,
            "detail_kind": detail_kind,
        }
        if patch:
            meta["patch"] = patch

        return WidgetUpdate.append(
            widget_id="detail_pane",
            payload={"role": "tool", "meta": meta},
        )


# --------------------------------------------------------------------------
# ArtifactProjectionRenderer
# --------------------------------------------------------------------------

_MAX_ARTIFACT_LINES = 50


class ArtifactProjectionRenderer:
    """Renders artifact events for the TUI artifact panel."""

    def summarize_artifact(self, artifact: dict[str, Any]) -> str:
        """Build a one-line summary of an artifact."""
        kind = artifact.get("kind", "artifact")
        content = artifact.get("content", "")
        path = artifact.get("path") or artifact.get("title", "")
        language = artifact.get("language", "")

        if kind == "code":
            line_count = content.count("\n") + 1
            return f"code artifact ({language}, {line_count} lines): {path}"
        if kind == "markdown":
            preview = content[:80].replace("\n", " ")
            return f"markdown artifact: {preview}..."
        if kind == "diff":
            lines = content.count("\n") + 1
            return f"diff artifact ({lines} lines): {path}"
        return f"{kind} artifact: {path}"

    def render_full_artifact(self, artifact: dict[str, Any], *, max_lines: int = _MAX_ARTIFACT_LINES) -> str:
        """Render full artifact content (possibly truncated)."""
        content = artifact.get("content", "")
        lines = content.splitlines()
        if len(lines) > max_lines:
            truncated = "\n".join(lines[:max_lines])
            return f"{truncated}\n... ({len(lines) - max_lines} more lines)"
        return content

    def render(self, event: dict[str, Any]) -> WidgetUpdate | None:
        """Project an artifact event into a WidgetUpdate."""
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        artifact = data if isinstance(data, dict) else {}
        if not artifact:
            return None

        summary = self.summarize_artifact(artifact)
        kind = artifact.get("kind", "artifact")
        return WidgetUpdate.append(
            widget_id="detail_pane",
            payload={"role": "artifact", "meta": {"summary": summary, "kind": kind}},
        )


# --------------------------------------------------------------------------
# DiffProjectionRenderer
# --------------------------------------------------------------------------

_MAX_DIFF_LINES = 200


class DiffProjectionRenderer:
    """Renders diff events with summarization and full diff rendering."""

    def summarize_diff(self, diff: str) -> str:
        """Build a one-line summary of a diff (additions/removals)."""
        if not diff:
            return ""
        additions = diff.count("\n+")
        deletions = diff.count("\n-")
        lines = diff.splitlines()
        # Try to extract filename from ---/+++ headers
        filename = ""
        for line in lines:
            if line.startswith("+++ b/"):
                filename = line[6:]  # skip "+++ b/" prefix
                break
            if line.startswith("+++ a/"):
                filename = line[6:]
                break
        if additions or deletions:
            return f"{filename} +{additions} -{deletions}"
        return filename or "diff"

    def render_full_diff(self, diff: str, *, max_lines: int = _MAX_DIFF_LINES) -> str:
        """Render a full diff (possibly truncated)."""
        if not diff:
            return ""
        lines = diff.splitlines()
        if len(lines) > max_lines:
            truncated = "\n".join(lines[:max_lines])
            return f"{truncated}\n... ({len(lines) - max_lines} more lines)"
        return diff

    def render(self, event: dict[str, Any]) -> WidgetUpdate | None:
        """Project a diff event into a WidgetUpdate."""
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        diff = data.get("diff", "") if isinstance(data, dict) else ""
        if not diff:
            return None
        summary = self.summarize_diff(diff)
        return WidgetUpdate.append(
            widget_id="detail_pane",
            payload={"role": "diff", "meta": {"summary": summary}},
        )


# --------------------------------------------------------------------------
# ThinkingRenderer
# --------------------------------------------------------------------------

_MAX_THINKING_PREVIEW = 80


class ThinkingRenderer:
    """Renders thinking_chunk events, filtering noise."""

    def filter_noise(self, text: str | None) -> str:
        """Remove noisy Offset/Region/Size/Spacing/Point tokens."""
        return _filter_noise(text)

    def render_thinking(self, thinking: str, *, max_preview: int = _MAX_THINKING_PREVIEW) -> str:
        """Render thinking text, possibly truncated."""
        if len(thinking) <= max_preview:
            return thinking
        return thinking[:max_preview] + f" ... ({len(thinking) - max_preview} more chars)"

    def render(self, event: dict[str, Any]) -> WidgetUpdate | None:
        """Project a thinking_chunk event into a WidgetUpdate."""
        event_type = event.get("type", "")
        if event_type != "thinking_chunk":
            return None

        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        chunk = data.get("content", "") if isinstance(data, dict) else ""
        filtered = self.filter_noise(chunk)
        preview = self.render_thinking(filtered)

        return WidgetUpdate.update(
            widget_id="detail_pane",
            item_id="__thinking__",
            payload={"thinking": preview},
        )


# --------------------------------------------------------------------------
# RendererRegistry
# --------------------------------------------------------------------------


class RendererRegistry:
    """Map from event type (str) to ProjectionRenderer.

    Manages renderer registration and dispatches events to the appropriate
    renderer, returning a WidgetUpdate (or None to skip).
    """

    def __init__(self) -> None:
        self._renderers: dict[str, ProjectionRenderer] = {}

    def register(self, event_type: str, renderer: ProjectionRenderer) -> None:
        """Register a renderer for a given event type."""
        self._renderers[event_type] = renderer

    def render(self, event: dict[str, Any]) -> WidgetUpdate | None:
        """Dispatch a single event to its registered renderer.

        Returns None if no renderer is registered or the renderer returns None.
        Tries both ``render()`` (ProjectionRenderer protocol) and ``project()``
        (legacy stub renderer interface) to maintain compatibility.
        Catches renderer exceptions and logs them rather than propagating.
        """
        event_type = event.get("type", "")
        renderer = self._renderers.get(event_type)
        if renderer is None:
            return None
        try:
            # Try render() first (ProjectionRenderer protocol), fall back to project()
            if hasattr(renderer, "render"):
                return renderer.render(event)
            if hasattr(renderer, "project"):
                return renderer.project(event)
            logger.warning("Renderer %r has neither render() nor project()", renderer.__class__.__name__)
            return None
        except (RuntimeError, ValueError) as exc:
            logger.warning("Renderer %r raised: %s", renderer.__class__.__name__, exc)
            return None

    def render_batch(self, events: list[dict[str, Any]]) -> list[WidgetUpdate]:
        """Dispatch a batch of events, collecting all non-None results."""
        updates: list[WidgetUpdate] = []
        for evt in events:
            update = self.render(evt)
            if update is not None:
                updates.append(update)
        return updates

    # Aliases for backward compatibility with tests
    def get(self, kind: str) -> ProjectionRenderer | None:
        """Return the renderer registered for this kind, or None."""
        return self._renderers.get(kind)

    def dispatch(self, event: dict[str, Any]) -> list[WidgetUpdate]:
        """Dispatch an event to the correct renderer; returns a list of WidgetUpdate."""
        update = self.render(event)
        return [update] if update is not None else []


# --------------------------------------------------------------------------
# ProjectionLayer
# --------------------------------------------------------------------------


class ProjectionLayer:
    """Transforms Director console events into WidgetUpdate objects.

    The layer holds a RendererRegistry and optionally throttles rapid updates.
    Supports both synchronous single-event rendering and async batch rendering.
    """

    def __init__(
        self,
        registry: RendererRegistry,
        *,
        throttle_s: float = 0.0,
    ) -> None:
        self._registry = registry
        self._throttle_s = throttle_s
        self._pending_updates: list[WidgetUpdate] = []

    @property
    def registry(self) -> RendererRegistry:
        """Alias _registry as registry for compatibility."""
        return self._registry

    def render(self, event: dict[str, Any]) -> WidgetUpdate | None:
        """Render a single event synchronously (no throttling)."""
        return self._registry.render(event)

    def project_event(self, event: dict[str, Any]) -> list[WidgetUpdate]:
        """Alias for render(); returns a list for compatibility."""
        update = self.render(event)
        return [update] if update is not None else []

    def project_stream(self, events: list[dict[str, Any]]) -> list[WidgetUpdate]:
        """Render a batch of events, returning all WidgetUpdates."""
        return self._registry.render_batch(events)

    async def render_batch(self, event_stream: AsyncIterator[dict[str, Any]]) -> AsyncIterator[WidgetUpdate]:
        """Render events from an async stream, optionally throttling updates."""
        last_flush = 0.0

        async for event in event_stream:
            update = self._registry.render(event)
            if update is None:
                continue

            # For "update" actions (content/thinking chunks), accumulate
            if update.action == "update":
                self._pending_updates.append(update)
                if self._throttle_s > 0:
                    now = asyncio.get_running_loop().time()
                    if now - last_flush >= self._throttle_s:
                        # Flush accumulated updates
                        while self._pending_updates:
                            yield self._pending_updates.pop(0)
                        last_flush = now
                else:
                    # No throttling: yield immediately
                    while self._pending_updates:
                        yield self._pending_updates.pop(0)
            else:
                # Non-update actions flush pending updates first
                while self._pending_updates:
                    yield self._pending_updates.pop(0)
                yield update

        # Flush any remaining pending updates
        while self._pending_updates:
            yield self._pending_updates.pop(0)


# --------------------------------------------------------------------------
# Default layer factory
# --------------------------------------------------------------------------

_DEFAULT_RENDERERS: list[tuple[str, ProjectionRenderer]] = [
    ("tool_call", ToolProjectionRenderer()),
    ("tool_result", ToolProjectionRenderer()),
    ("artifact", ArtifactProjectionRenderer()),
    ("thinking_chunk", ThinkingRenderer()),
    ("content_chunk", ContentChunkRenderer()),
    ("complete", MessageCompleteRenderer()),
]


def create_default_projection_layer(*, throttle_s: float = 0.0) -> ProjectionLayer:
    """Create a ProjectionLayer with all standard renderers pre-registered."""
    registry = RendererRegistry()
    for event_type, renderer in _DEFAULT_RENDERERS:
        registry.register(event_type, renderer)
    return ProjectionLayer(registry, throttle_s=throttle_s)
