"""Claude-style rendering functions for the Polaris Director console.

Provides markup and plain-text rendering for console messages.
Design: clean markdown, syntax-highlighted diffs, proper role colors.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from rich.markup import escape

# ---------------------------------------------------------------------------
# Claude color palette
# ---------------------------------------------------------------------------
_CLAUDE_ACCENT = "#8b5cf6"
_CLAUDE_ACCENT2 = "#a78bfa"
_CLAUDE_USER_ACCENT = "#4f46e5"
_CLAUDE_TOOL_ACCENT = "#22c55e"
_CLAUDE_ERROR = "#ef4444"
_CLAUDE_WARN = "#f59e0b"
_CLAUDE_TEXT = "#e8e8ed"
_CLAUDE_TEXT2 = "#a0a0b0"
_CLAUDE_TEXT3 = "#686880"
_CLAUDE_CODE_BG = "#16161d"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _json_text(value: Any, *, max_chars: int = 4000) -> str:
    rendered = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if len(rendered) <= max_chars:
        return rendered
    return f"{rendered[:max_chars]}\n[dim]... ({len(rendered) - max_chars} chars truncated)[/dim]"


# ---------------------------------------------------------------------------
# Tool payload extraction
# ---------------------------------------------------------------------------


def _extract_tool_name(payload: Mapping[str, Any]) -> str:
    for source in (payload, _as_mapping(payload.get("result")), _as_mapping(payload.get("raw_result"))):
        tool_name = _safe_text(source.get("tool"))
        if tool_name:
            return tool_name
    return "unknown_tool"


def _extract_file_path(payload: Mapping[str, Any]) -> str:
    for source in (
        payload,
        _as_mapping(payload.get("result")),
        _as_mapping(payload.get("raw_result")),
        _as_mapping(payload.get("args")),
    ):
        for key in ("file_path", "file", "path", "filepath", "target_file"):
            value = _safe_text(source.get(key))
            if value:
                return value
    return ""


def _extract_error_text(payload: Mapping[str, Any]) -> str:
    for source in (payload, _as_mapping(payload.get("raw_result")), _as_mapping(payload.get("result"))):
        for key in ("error", "message"):
            value = _safe_text(source.get(key))
            if value:
                return value
    return ""


def _extract_patch_text(payload: Mapping[str, Any]) -> str:
    for source in (payload, _as_mapping(payload.get("result")), _as_mapping(payload.get("raw_result"))):
        for key in ("patch", "diff", "diff_patch", "workspace_diff", "unified_diff"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _extract_success(payload: Mapping[str, Any]) -> bool | None:
    for source in (payload, _as_mapping(payload.get("raw_result")), _as_mapping(payload.get("result"))):
        value = source.get("success")
        if isinstance(value, bool):
            return value
        value = source.get("ok")
        if isinstance(value, bool):
            return value
    error_text = _extract_error_text(payload)
    if error_text:
        return False
    return None


def _detail_payload(payload: Mapping[str, Any]) -> Any:
    result = payload.get("result")
    if result is not None:
        return result
    raw_result = payload.get("raw_result")
    if raw_result is not None:
        return raw_result
    return dict(payload)


# ---------------------------------------------------------------------------
# Tool overlay message builder
# ---------------------------------------------------------------------------


def build_tool_overlay_message(event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build a structured tool message for overlay rendering."""
    normalized_type = _safe_text(event_type)
    normalized_payload = dict(payload)
    tool_name = _extract_tool_name(normalized_payload)
    file_path = _extract_file_path(normalized_payload)

    if normalized_type == "tool_call":
        args = _as_mapping(normalized_payload.get("args"))
        summary = f"{tool_name}  →  {file_path}" if file_path else tool_name
        return {
            "role": "tool",
            "content": summary,
            "meta": {
                "event_type": "tool_call",
                "tool_name": tool_name,
                "summary": summary,
                "detail_kind": "json" if args else "text",
                "detail": _json_text(args) if args else "",
            },
        }

    success = _extract_success(normalized_payload)
    status = "ok" if success is True else ("failed" if success is False else "unknown")
    error_text = _extract_error_text(normalized_payload)
    patch_text = _extract_patch_text(normalized_payload)
    operation = _safe_text(normalized_payload.get("operation")) or "modify"

    summary_parts = [f"[bold]{tool_name}[/bold]"]
    if file_path:
        summary_parts.append(f"`{file_path}`")
    if status == "ok":
        summary_parts.append(f"[color={_CLAUDE_TOOL_ACCENT}]✓[/color]")
    elif status == "failed":
        summary_parts.append(f"[color={_CLAUDE_ERROR}]✗[/color]")
    if error_text and error_text not in summary_parts:
        summary_parts.append(f"[color={_CLAUDE_ERROR}]{error_text}[/color]")
    summary = "  ".join(part for part in summary_parts if part)

    detail_kind: str
    detail: str
    if patch_text:
        detail_kind = "diff"
        detail = patch_text
    elif error_text:
        detail_kind = "text"
        detail = error_text
    else:
        detail_kind = "json"
        detail = _json_text(_detail_payload(normalized_payload))

    return {
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
            "patch_truncated": bool(normalized_payload.get("patch_truncated")),
        },
    }


# ---------------------------------------------------------------------------
# Claude-style diff rendering
# ---------------------------------------------------------------------------


def _render_diff_markup_lines(
    diff_text: str,
    *,
    operation: str,
    truncated: bool,
    max_lines: int = 160,
) -> list[str]:
    """Render diff text with Claude-style syntax highlighting."""
    lines = diff_text.splitlines()
    visible = lines[:max_lines]
    has_unified_markers = any(
        line.startswith("+++ ") or line.startswith("--- ") or line.startswith("@@") for line in visible
    )

    rendered: list[str] = []
    for line in visible:
        escaped = escape(line)
        if has_unified_markers:
            if line.startswith("+++ ") or line.startswith("--- "):
                rendered.append(f"[bold]{escaped}[/bold]")
            elif line.startswith("@@"):
                rendered.append(f"[color={_CLAUDE_TEXT3}]{escaped}[/color]")
            elif line.startswith("+") and not line.startswith("+++ "):
                rendered.append(f"[color={_CLAUDE_TOOL_ACCENT}]{escaped}[/color]")
            elif line.startswith("-") and not line.startswith("--- "):
                rendered.append(f"[color={_CLAUDE_ERROR}]{escaped}[/color]")
            else:
                rendered.append(escaped)
            continue

        if operation == "delete":
            rendered.append(f"[color={_CLAUDE_ERROR}]{escaped}[/color]")
        else:
            rendered.append(f"[color={_CLAUDE_TOOL_ACCENT}]{escaped}[/color]")

    remaining = len(lines) - len(visible)
    if truncated or remaining > 0:
        hidden = remaining if remaining > 0 else 0
        rendered.append(f"[dim]... ({hidden} more lines)[/dim]")
    return rendered


# ---------------------------------------------------------------------------
# Claude-style message rendering
# ---------------------------------------------------------------------------


def render_message_markup(message: Mapping[str, Any]) -> str:
    """Render a single message with Claude-style Rich markup."""
    role = _safe_text(message.get("role"))
    content = _safe_text(message.get("content"))
    thinking = _safe_text(message.get("thinking"))
    meta = _as_mapping(message.get("meta"))

    if role == "user":
        return "\n".join(
            [
                f"[bold][color={_CLAUDE_USER_ACCENT}]You[/color][/bold]",
                escape(content) or "[dim](empty)[/dim]",
            ]
        )

    if role == "assistant":
        parts = [
            f"[bold][color={_CLAUDE_ACCENT2}]Director[/color][/bold]",
        ]
        if thinking:
            parts.append(f"[color={_CLAUDE_TEXT3}][italic]thinking:[/italic] {escape(thinking[:200])}[/color]")
        parts.append(escape(content) or "[dim](empty)[/dim]")
        return "\n".join(parts)

    if role == "system":
        return "\n".join(
            [
                f"[bold][color={_CLAUDE_WARN}]System[/color][/bold]",
                escape(content) or "[dim](empty)[/dim]",
            ]
        )

    if role == "tool":
        event_type = _safe_text(meta.get("event_type")) or "tool"
        summary = _safe_text(meta.get("summary")) or content
        detail_kind = _safe_text(meta.get("detail_kind"))
        detail = _safe_text(meta.get("detail"))
        operation = _safe_text(meta.get("operation")) or "modify"
        truncated = bool(meta.get("patch_truncated"))

        if event_type == "tool_call":
            parts = [
                f"[bold][color={_CLAUDE_TEXT2}]Tool Call[/color][/bold]",
                f"[dim]{escape(summary)}[/dim]",
            ]
        else:
            status = _safe_text(meta.get("status")) or "unknown"
            status_color = (
                _CLAUDE_TOOL_ACCENT if status == "ok" else (_CLAUDE_ERROR if status == "failed" else _CLAUDE_WARN)
            )
            status_icon = "✓" if status == "ok" else ("✗" if status == "failed" else "?")
            parts = [
                f"[bold][color={status_color}]Tool Result {status_icon}[/color][/bold]",
                escape(summary) or "[dim](empty)[/dim]",
            ]

        if detail:
            if detail_kind == "diff":
                parts.extend(
                    _render_diff_markup_lines(
                        detail,
                        operation=operation,
                        truncated=truncated,
                    )
                )
            elif detail_kind == "json":
                # JSON shown in collapsed form
                lines = detail.splitlines()
                parts.append("[dim]```json")
                parts.extend(f"  {escape(line)}" for line in lines[:20])
                if len(lines) > 20:
                    parts.append(f"  ... ({len(lines) - 20} more lines)")
                parts.append("```[/dim]")
            elif detail != summary:
                parts.append(escape(detail[:500]))
                if len(detail) > 500:
                    parts.append("[dim]...[/dim]")
        return "\n".join(parts)

    return "\n".join(
        [
            f"[bold][color={_CLAUDE_TEXT2}]{escape(role or 'Unknown')}[/color][/bold]",
            escape(content) or "[dim](empty)[/dim]",
        ]
    )


def render_message_plain_text(message: Mapping[str, Any]) -> str:
    """Render a single message as plain text (no markup)."""
    role = _safe_text(message.get("role")) or "unknown"
    content = _safe_text(message.get("content"))
    thinking = _safe_text(message.get("thinking"))
    meta = _as_mapping(message.get("meta"))

    if role == "tool":
        summary = _safe_text(meta.get("summary")) or content
        detail_kind = _safe_text(meta.get("detail_kind"))
        detail = _safe_text(meta.get("detail"))
        header = "Tool Call" if _safe_text(meta.get("event_type")) == "tool_call" else "Tool Result"
        parts = [header, summary]
        if detail and detail != summary:
            if detail_kind == "diff":
                lines = detail.splitlines()
                visible = lines[:80]
                parts.extend(visible)
                if len(lines) > len(visible):
                    parts.append(f"... ({len(lines) - len(visible)} more lines)")
            else:
                parts.append(detail[:500])
        return "\n".join(parts)

    header = {"user": "You", "assistant": "Director", "system": "System"}.get(role, role.title())
    parts = [header, content or "(empty)"]
    if thinking:
        parts.append(f"[thinking] {thinking[:200]}")
    return "\n".join(parts)


def conversation_markup(messages: list[dict[str, Any]], *, limit: int = 50) -> str:
    """Render a conversation as Claude-style markup."""
    rendered = [render_message_markup(message) for message in messages[-max(1, int(limit)) :]]
    return "\n\n".join(rendered) if rendered else "[dim]No conversation yet.[/dim]"


def conversation_plain_text(messages: list[dict[str, Any]], *, limit: int = 50) -> str:
    """Render a conversation as plain text."""
    rendered = [render_message_plain_text(message) for message in messages[-max(1, int(limit)) :]]
    return "\n\n".join(rendered) if rendered else "No conversation yet."


__all__ = [
    "build_tool_overlay_message",
    "conversation_markup",
    "conversation_plain_text",
    "render_message_markup",
    "render_message_plain_text",
]
