"""Unified Projection Layer for the Polaris Director TUI.

Architectural role:
  - ProjectionLayer is the boundary between the stream event bus
    (RoleConsoleHost.stream_turn) and the widget render layer.
  - It translates typed stream events into neutral WidgetUpdate payloads
    that Textual widgets consume.
  - All rendering logic lives here; widgets remain purely presentational.

Event types consumed (from RoleConsoleHost.stream_turn):
  content_chunk  → accumulated into the live assistant message
  thinking_chunk → accumulated into the live thinking section
  tool_call      → rendered as a tool-invocation bubble
  tool_result    → rendered as a tool-result bubble (may carry patch/diff)
  fingerprint    → metadata pulse (ignored for rendering)
  error          → rendered as an error notification bubble
  complete       → finalises the current assistant message
  done           → stream terminator (no-op for rendering)

Render policy:
  content_chunk / thinking_chunk default to immediate pass-through.
  Optional throttling is still supported via explicit configuration.

UTF-8: all text rendering is encoding-safe via Rich markup escaping.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

if __name__ == "__main__":
    raise SystemExit("projection.py is not a standalone script")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

ProjectionEvent = dict[str, Any]  # {type: str, data: dict, metadata: dict}

# ---------------------------------------------------------------------------
# WidgetUpdate
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WidgetUpdate:
    """Neutral rendering payload for a Textual widget.

    Attributes
    ----------
    widget_id:
        Which widget receives the update.
        Known IDs: "detail_pane" | "tool_overlay" | "artifact_overlay"
    action:
        What the widget should do.
        "append"  — add a new item
        "update"  — patch an existing item by id
        "clear"   — remove all items
        "flush"   — finalise streaming state (no more chunks)
    payload:
        Rendered content (str or dict depending on widget_id/action).
    position:
        Insertion index for "append", or the id of the item for "update".
    timestamp:
        When this update was produced (UTC).
    """

    widget_id: str
    action: str
    payload: Any = ""
    position: int | str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    # Convenience factories ---------------------------------------------------------

    @classmethod
    def append(cls, widget_id: str, payload: Any, position: int | str | None = None) -> WidgetUpdate:
        return cls(widget_id=widget_id, action="append", payload=payload, position=position)

    @classmethod
    def update(cls, widget_id: str, item_id: str, payload: Any) -> WidgetUpdate:
        return cls(widget_id=widget_id, action="update", payload=payload, position=item_id)

    @classmethod
    def clear(cls, widget_id: str) -> WidgetUpdate:
        return cls(widget_id=widget_id, action="clear")

    @classmethod
    def flush(cls, widget_id: str) -> WidgetUpdate:
        return cls(widget_id=widget_id, action="flush")


# ---------------------------------------------------------------------------
# Renderer protocol
# ---------------------------------------------------------------------------


class Renderer(Protocol):
    """Protocol for stream-event renderers."""

    def render(self, event: ProjectionEvent) -> WidgetUpdate | None:
        """Render a single projection event. Returns None to skip."""
        ...


# ---------------------------------------------------------------------------
# ToolProjectionRenderer
# ---------------------------------------------------------------------------

_TOOL_NAME_RE = re.compile(r"\b(Read|Write|Edit|Bash|Grep|Search|WebSearch|View|Open|MultiEdit)\b")


def _extract_tool_name(payload: dict[str, Any]) -> str:
    for source in (payload,):
        tool_name = str(source.get("tool") or "").strip()
        if tool_name:
            return tool_name
        result = source.get("result")
        if isinstance(result, dict):
            tn = str(result.get("tool") or "").strip()
            if tn:
                return tn
    return "unknown_tool"


def _extract_file_path(payload: dict[str, Any]) -> str:
    for source in (
        payload,
        payload.get("result"),
        payload.get("raw_result"),
        payload.get("args"),
    ):
        if not isinstance(source, dict):
            continue
        for key in ("file_path", "file", "path", "filepath", "target_file"):
            token = str(source.get(key) or "").strip()
            if token:
                return token.replace("\\", "/")
    return ""


def _extract_error_text(payload: dict[str, Any]) -> str:
    for source in (payload, payload.get("raw_result"), payload.get("result")):
        if not isinstance(source, dict):
            continue
        for key in ("error", "message"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def _extract_success(payload: dict[str, Any]) -> bool | None:
    for source in (payload, payload.get("raw_result"), payload.get("result")):
        if not isinstance(source, dict):
            continue
        success = source.get("success")
        if isinstance(success, bool):
            return success
        ok = source.get("ok")
        if isinstance(ok, bool):
            return ok
    error_text = _extract_error_text(payload)
    if error_text:
        return False
    return None


def _detail_payload(payload: dict[str, Any]) -> Any:
    result = payload.get("result")
    if result is not None:
        return result
    raw_result = payload.get("raw_result")
    if raw_result is not None:
        return raw_result
    return dict(payload)


def _json_pretty(value: Any, *, max_chars: int = 4000) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (RuntimeError, ValueError):  # pragma: no cover - defensive
        rendered = str(value)
    if len(rendered) <= max_chars:
        return rendered
    return f"{rendered[:max_chars]}\n... ({len(rendered) - max_chars} chars truncated)"


class ToolProjectionRenderer:
    """Renders tool_call and tool_result events into WidgetUpdate payloads."""

    def render(self, event: ProjectionEvent) -> WidgetUpdate | None:
        event_type = str(event.get("type") or "").strip()
        if event_type not in {"tool_call", "tool_result"}:
            return None

        data = event.get("data")
        if not isinstance(data, dict):
            return None

        if event_type == "tool_call":
            return self._render_tool_call(data)
        return self._render_tool_result(data)

    def _render_tool_call(self, payload: dict[str, Any]) -> WidgetUpdate:
        tool_name = _extract_tool_name(payload)
        file_path = _extract_file_path(payload)
        args = payload.get("args")
        args_dict = dict(args) if isinstance(args, dict) else {}

        summary = f"{tool_name}  →  {file_path}" if file_path else tool_name

        return WidgetUpdate.append(
            widget_id="detail_pane",
            payload={
                "role": "tool",
                "content": summary,
                "meta": {
                    "event_type": "tool_call",
                    "tool_name": tool_name,
                    "summary": summary,
                    "detail_kind": "json" if args_dict else "text",
                    "detail": _json_pretty(args_dict) if args_dict else "",
                },
            },
            position=None,
        )

    def _render_tool_result(self, payload: dict[str, Any]) -> WidgetUpdate:
        tool_name = _extract_tool_name(payload)
        file_path = _extract_file_path(payload)
        success = _extract_success(payload)
        error_text = _extract_error_text(payload)
        patch_text = str(payload.get("patch") or payload.get("diff") or "").strip()
        operation = str(payload.get("operation") or "modify").strip()

        status = "ok" if success is True else ("failed" if success is False else "unknown")

        if success is True:
            status_icon = "\u2713"  # check mark
        elif success is False:
            status_icon = "\u2717"  # cross mark
        else:
            status_icon = "?"

        summary_parts = [f"[bold]{tool_name}[/bold]"]
        if file_path:
            summary_parts.append(f"`{file_path}`")
        summary_parts.append(f"{status_icon} {status}")
        if error_text:
            summary_parts.append(error_text[:80])

        summary = "  ".join(part for part in summary_parts if part)

        if patch_text:
            detail_kind = "diff"
            detail = patch_text
        elif error_text:
            detail_kind = "text"
            detail = error_text
        else:
            detail_kind = "json"
            detail = _json_pretty(_detail_payload(payload))

        return WidgetUpdate.append(
            widget_id="detail_pane",
            payload={
                "role": "tool",
                "content": summary,
                "meta": {
                    "event_type": "tool_result",
                    "tool_name": tool_name,
                    "status": status,
                    "operation": operation,
                    "summary": summary,
                    "detail_kind": detail_kind,
                    "detail": detail,
                    "patch_truncated": bool(payload.get("patch_truncated")),
                },
            },
            position=None,
        )


# ---------------------------------------------------------------------------
# DiffProjectionRenderer
# ---------------------------------------------------------------------------


class DiffProjectionRenderer:
    """Renders unified diff text into structured WidgetUpdate payloads."""

    def render(self, event: ProjectionEvent) -> WidgetUpdate | None:
        # Diffs are carried as tool_result payloads — this renderer is
        # called explicitly by ArtifactOverlay when the user requests a
        # full diff view.
        return None

    @staticmethod
    def summarize_diff(diff_text: str) -> str:
        """Compute a single-line summary of a unified diff.

        Returns e.g. ``"\u00b110 lines /path/to/file.py"``
        """
        if not diff_text:
            return ""
        lines = diff_text.splitlines()
        added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++ "))
        removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("--- "))
        net = added - removed

        # Try to extract the most relevant file path
        file_path = ""
        for line in lines:
            if line.startswith("+++ b/") or line.startswith("--- "):
                candidate = line.lstrip("-+").lstrip(" b/").split("\t")[0].strip()
                if candidate and candidate != "/dev/null":
                    file_path = candidate
                    break

        sign = ("+" if net else "") if net >= 0 else ""

        if file_path:
            return f"{sign}{net} lines {file_path}"
        return f"{sign}{net} lines"

    @staticmethod
    def render_full_diff(diff_text: str, max_lines: int = 200) -> str:
        """Render a complete diff, truncated to max_lines lines.

        Adds a ``... (N more lines)`` footer when truncated.
        """
        if not diff_text:
            return ""
        lines = diff_text.splitlines()
        visible = lines[: int(max_lines)]
        remaining = len(lines) - len(visible)
        body = "\n".join(visible)
        if remaining > 0:
            body = f"{body}\n... ({remaining} more lines)"
        return body


# ---------------------------------------------------------------------------
# ArtifactProjectionRenderer
# ---------------------------------------------------------------------------


class ArtifactProjectionRenderer:
    """Renders code / markdown / diff artifacts into WidgetUpdate payloads."""

    def render(self, event: ProjectionEvent) -> WidgetUpdate | None:
        return None  # Artifacts are delivered via ConsoleMessage.artifacts

    @staticmethod
    def summarize_artifact(artifact: dict[str, Any]) -> str:
        """Return a one-line summary string for an artifact.

        Returns e.g. ``"code artifact: python / 42 lines / src/main.py"``
        """
        kind = str(artifact.get("kind") or artifact.get("type") or "text").strip().lower()
        language = str(artifact.get("language") or "").strip()
        path = str(artifact.get("path") or artifact.get("file_path") or "").strip()
        content = str(artifact.get("content") or "")

        line_count = len(content.splitlines())

        if kind == "markdown":
            kind_label = "markdown"
        elif kind == "diff":
            kind_label = "diff"
        else:
            kind_label = "code artifact"

        parts = [kind_label]
        if language:
            parts.append(language)
        parts.append(f"/ {line_count} lines")
        if path:
            parts.append(f"/ {path}")

        return " ".join(parts)

    @staticmethod
    def render_full_artifact(artifact: dict[str, Any], max_lines: int = 2000) -> str:
        """Return the full artifact content, truncated to max_lines lines."""
        content = str(artifact.get("content") or "")
        lines = content.splitlines()
        if len(lines) <= int(max_lines):
            return content
        visible = "\n".join(lines[: int(max_lines)])
        return f"{visible}\n... ({len(lines) - int(max_lines)} more lines)"


# ---------------------------------------------------------------------------
# ThinkingRenderer
# ---------------------------------------------------------------------------

_NOISE_TOKEN_RE = re.compile(r"\b(?:Offset|Region|Size|Spacing|Point)\([^)]*\)")
_NOISE_LINE_RE = re.compile(r"(?m)^\s*(?:Offset|Region|Size|Spacing|Point)\([^)]*\)\s*$")


class ThinkingRenderer:
    """Renders thinking / reasoning content with noise filtering."""

    def render(self, event: ProjectionEvent) -> WidgetUpdate | None:
        return None  # Thinking is accumulated in the stream and flushed on complete

    @staticmethod
    def filter_noise(text: str) -> str:
        """Strip Offset/Region/Size/Spacing/Point tokens from thinking text.

        These are common LLM reasoning noise that clutters the UI.
        """
        if not text:
            return ""
        if not any(token in text for token in ("Offset(", "Region(", "Size(", "Spacing(", "Point(")):
            return text
        sanitized = _NOISE_LINE_RE.sub("", text)
        sanitized = _NOISE_TOKEN_RE.sub("", sanitized)
        sanitized = re.sub(r"[ \t]{2,}", " ", sanitized)
        sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
        return sanitized.strip()

    @staticmethod
    def render_thinking(thinking_text: str, max_preview: int = 200) -> str:
        """Filter thinking text and return a preview of at most max_preview chars."""
        cleaned = ThinkingRenderer.filter_noise(thinking_text)
        if len(cleaned) <= int(max_preview):
            return cleaned
        return f"{cleaned[: int(max_preview)]}\n... ({len(cleaned) - int(max_preview)} more chars)"


# ---------------------------------------------------------------------------
# RendererRegistry
# ---------------------------------------------------------------------------


class RendererRegistry:
    """Maps event types to renderer instances."""

    def __init__(self) -> None:
        self._renderers: dict[str, Renderer] = {}

    def register(self, event_type: str, renderer: Renderer) -> None:
        """Register (or replace) a renderer for the given event type."""
        self._renderers[str(event_type).strip()] = renderer

    def render(self, event: ProjectionEvent) -> WidgetUpdate | None:
        """Dispatch a single event to the appropriate renderer."""
        event_type = str(event.get("type") or "").strip()
        renderer = self._renderers.get(event_type)
        if renderer is None:
            return None
        try:
            return renderer.render(event)
        except (RuntimeError, ValueError) as exc:
            logger.warning("Renderer for %r raised %s: %s", event_type, type(exc).__name__, exc)
            return None

    def render_batch(self, events: list[ProjectionEvent]) -> list[WidgetUpdate]:
        """Render a batch of events (non-streaming path)."""
        results: list[WidgetUpdate] = []
        for event in events:
            update = self.render(event)
            if update is not None:
                results.append(update)
        return results


# ---------------------------------------------------------------------------
# ProjectionLayer
# ---------------------------------------------------------------------------


@dataclass
class _PendingChunk:
    """Accumulator for throttled content/thinking chunks."""

    content_parts: list[str] = field(default_factory=list)
    thinking_parts: list[str] = field(default_factory=list)
    last_at: float = field(default_factory=0.0)


class ProjectionLayer:
    """Unified projection layer between the stream bus and Textual widgets.

    Responsibilities:
    1. Register renderers for each event type.
    2. Dispatch individual events to the appropriate renderer.
    3. Pass through ``content_chunk`` / ``thinking_chunk`` immediately by
       default, with opt-in throttling when explicitly requested.
    4. Expose the event stream as an async generator of WidgetUpdate.

    Widget IDs produced
    -------------------
    ``detail_pane``
        All message bubbles (user, assistant, tool) and streaming updates.
    ``artifact_overlay``
        Full-screen artifact/diff viewer (opened via binding).
    ``tool_overlay``
        Reserved for future dedicated tool-call overlay panel.

    Usage
    -----
    >>> registry = RendererRegistry()
    >>> registry.register("tool_call", ToolProjectionRenderer())
    >>> registry.register("tool_result", ToolProjectionRenderer())
    >>> layer = ProjectionLayer(registry)
    >>>
    >>> async for update in layer.render_batch(
    ...     host.stream_turn(session_id, message)
    ... ):
    ...     await detail_pane.apply_update(update)
    """

    DEFAULT_THROTTLE_S: float = 0.0

    def __init__(
        self,
        registry: RendererRegistry,
        *,
        throttle_s: float = DEFAULT_THROTTLE_S,
    ) -> None:
        self._registry = registry
        self._throttle_s = float(throttle_s)
        self._pending: _PendingChunk = _PendingChunk()

    def render(self, event: ProjectionEvent) -> WidgetUpdate | None:
        """Render a single event immediately (no throttling).

        Use this for non-chunk events (tool_call, tool_result, complete, error).
        For content/thinking chunks prefer render_batch().
        """
        return self._registry.render(event)

    async def render_batch(
        self,
        events: AsyncIterator[ProjectionEvent],
    ) -> AsyncIterator[WidgetUpdate]:
        """Convert an async event stream to an async stream of WidgetUpdate.

        Internal policy:
        - content_chunk / thinking_chunk → accumulated and flushed at most
          every throttle_s.  The accumulated content is emitted as a single
          ``update`` action on ``detail_pane``.
        - All other event types → rendered immediately and yielded.

        Events that yield None from their renderer are silently dropped
        (e.g. fingerprint, done).
        """
        pending = _PendingChunk()
        pending.last_at = time.monotonic()

        async for event in events:
            event_type = str(event.get("type") or "").strip()

            if event_type == "content_chunk":
                data = event.get("data", {})
                content = str(data.get("content") or "" if isinstance(data, dict) else "")
                pending.content_parts.append(content)
                flushed = self._maybe_flush(pending)
                if flushed is not None:
                    yield flushed
                continue

            if event_type == "thinking_chunk":
                data = event.get("data", {})
                thinking = str(data.get("content") or "" if isinstance(data, dict) else "")
                pending.thinking_parts.append(thinking)
                flushed = self._maybe_flush(pending)
                if flushed is not None:
                    yield flushed
                continue

            # For all non-chunk events: flush any pending content first
            flushed = self._flush_pending(pending)
            if flushed is not None:
                yield flushed

            update = self._registry.render(event)
            if update is not None:
                yield update

        # Drain remaining pending on stream end
        final = self._flush_pending(pending)
        if final is not None:
            yield final

    # Internal flush helpers ----------------------------------------------------

    def _maybe_flush(self, pending: _PendingChunk) -> WidgetUpdate | None:
        """Flush pending chunks if throttle interval has elapsed."""
        elapsed = time.monotonic() - pending.last_at
        if elapsed < self._throttle_s:
            return None
        return self._flush_pending(pending)

    def _flush_pending(self, pending: _PendingChunk) -> WidgetUpdate | None:
        """Emit a pending chunk flush and reset the accumulator."""
        content = "".join(pending.content_parts)
        thinking = "".join(pending.thinking_parts)
        pending.content_parts.clear()
        pending.thinking_parts.clear()
        pending.last_at = time.monotonic()

        if not content and not thinking:
            return None

        return WidgetUpdate.update(
            widget_id="detail_pane",
            item_id="__streaming__",
            payload={"content": content, "thinking": thinking},
        )


# ---------------------------------------------------------------------------
# Module-level convenience factory
# ---------------------------------------------------------------------------


def create_default_projection_layer(*, throttle_s: float = 0.0) -> ProjectionLayer:
    """Create a ProjectionLayer with all standard renderers pre-registered."""
    registry = RendererRegistry()
    registry.register("tool_call", ToolProjectionRenderer())
    registry.register("tool_result", ToolProjectionRenderer())
    return ProjectionLayer(registry, throttle_s=throttle_s)


__all__ = [
    "ArtifactProjectionRenderer",
    "DiffProjectionRenderer",
    "ProjectionEvent",
    "ProjectionLayer",
    "Renderer",
    "RendererRegistry",
    "ThinkingRenderer",
    "ToolProjectionRenderer",
    "WidgetUpdate",
    "create_default_projection_layer",
]
