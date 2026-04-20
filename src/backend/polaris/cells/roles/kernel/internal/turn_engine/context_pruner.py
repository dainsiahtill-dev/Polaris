"""Context pruning - HALLUCINATION_LOOP detection and recovery.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

职责：
    检测工具调用中的幻觉循环，并执行选择性上下文剪枝。
    只保留 read 工具的成功结果，清除失败工具的偏见累积。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ContextPruner:
    """Detects hallucination loops and prunes controller history selectively."""

    def __init__(self) -> None:
        """Initialize with empty pending loop-break registry."""
        # key=(tool_name, fingerprint), value=suggestion
        self._pending_loop_break: dict[tuple[str, str], str] = {}

    def reset_turn(self) -> None:
        """Clear pending loop-break state at the start of each new turn."""
        self._pending_loop_break.clear()

    def prune_for_loop_break(
        self,
        controller: Any,
        failed_tool_name: str,
        suggestion: str,
    ) -> dict[str, Any]:
        """Execute Context Pruning: remove failed_tool_name calls, keep read_file results.

        Args:
            controller: ToolLoopController instance, its _history will be pruned.
            failed_tool_name: Tool name that triggered loop_break (e.g., "precision_edit").
            suggestion: HALLUCINATION_LOOP reminder text.

        Returns:
            Dict with type="system_reminder" and content for injection.
        """
        history = getattr(controller, "_history", [])
        if not history:
            return {"type": "system_reminder", "content": suggestion}

        kept_events: list[Any] = []
        removed_count = 0
        last_read_content: str | None = None

        for event in history:
            metadata = getattr(event, "metadata", {}) or {}
            event_tool = metadata.get("tool", "")
            event_role = getattr(event, "role", "")

            # Keep all read tool successful results
            if event_role == "tool" and event_tool in (
                "read_file",
                "repo_read_head",
                "repo_read_tail",
                "repo_read_slice",
                "repo_read_around",
                "repo_tree",
            ):
                content = getattr(event, "content", "") or ""
                if content:
                    last_read_content = content[:500]
                kept_events.append(event)
            # Remove failed_tool_name failed calls
            elif event_role == "tool" and event_tool == failed_tool_name:
                content = getattr(event, "content", "") or ""
                if "error" in content.lower() or "failed" in content.lower() or "no_match" in content.lower():
                    removed_count += 1
                    continue
                else:
                    kept_events.append(event)
            else:
                kept_events.append(event)

        controller._history = kept_events

        logger.info(
            "[ContextPruner] removed %d failed '%s' calls, kept %d events (last read_file: %s chars)",
            removed_count,
            failed_tool_name,
            len(kept_events),
            len(last_read_content) if last_read_content else 0,
        )

        reminder_parts = [
            f"[Context Pruning] Previous {removed_count} '{failed_tool_name}' call(s) failed due to HALLUCINATION_LOOP.",
            "The failed search patterns have been removed from context to clear error bias.",
        ]
        if last_read_content:
            reminder_parts.append(f"Latest file content (read_file result):\n{last_read_content}")
        reminder_parts.append(f"Reminder: {suggestion}")
        reminder_content = "\n\n".join(reminder_parts)

        return {"type": "system_reminder", "content": reminder_content}

    def check_and_handle_loop_break(
        self,
        tool_name: str,
        controller: Any,
        *,
        skip_tool: bool = True,
        fingerprint: str | None = None,
    ) -> dict[str, Any] | None:
        """Check if a tool is in loop_break state and optionally prune context.

        Args:
            tool_name: Tool name to check.
            controller: ToolLoopController instance.
            skip_tool: If True, skip execution and return prune result.
            fingerprint: Optional search fingerprint for scoped loop detection.

        Returns:
            Prune result dict if tool is in pending_loop_break and fingerprint matches,
            None otherwise.
        """
        key = (tool_name, fingerprint) if fingerprint else (tool_name, "")
        if key not in self._pending_loop_break:
            return None

        suggestion = self._pending_loop_break.pop(key)
        prune_result = self.prune_for_loop_break(
            controller=controller,
            failed_tool_name=tool_name,
            suggestion=suggestion,
        )

        from polaris.cells.roles.kernel.internal.tool_loop_controller import ContextEvent

        reminder_event = ContextEvent(
            event_id=f"loop_break_reminder_{len(controller._history)}",
            role="system",
            content=prune_result["content"],
            sequence=len(controller._history),
            metadata={"kind": "system_reminder", "tool": tool_name},
        )
        controller._history.append(reminder_event)

        if skip_tool:
            logger.info(
                "[ContextPruner] Loop break: skipping tool '%s', injected system_reminder",
                tool_name,
            )
            return prune_result
        return None

    def inject_loop_break_signal(
        self,
        tool_name: str,
        tool_result: dict[str, Any],
        fingerprint: str | None = None,
    ) -> None:
        """Record that a tool has triggered loop_break for future pruning.

        Args:
            tool_name: Tool name that returned loop_break=True.
            tool_result: Tool result dict containing loop_break flag.
            fingerprint: Optional search fingerprint.
        """
        if not tool_result.get("loop_break"):
            return

        suggestion = tool_result.get(
            "suggestion",
            "After confirming the exact content, retry precision_edit with the verified string.",
        )
        key = (tool_name, fingerprint) if fingerprint else (tool_name, "")
        self._pending_loop_break[key] = suggestion
        logger.info(
            "[ContextPruner] HALLUCINATION_LOOP detected for tool '%s' fingerprint='%s', pending context pruning",
            tool_name,
            fingerprint[:30] if fingerprint else "",
        )

    def handle_blocked_tool_pruning(
        self,
        tool_name: str,
        controller: Any,
        tool_result: dict[str, Any],
        fingerprint: str | None = None,
    ) -> None:
        """Trigger context pruning when a tool is blocked due to repeated failures.

        Args:
            tool_name: Blocked tool name.
            controller: ToolLoopController instance.
            tool_result: Tool result dict containing blocked flag.
            fingerprint: Optional search fingerprint.
        """
        if not tool_result.get("blocked"):
            return

        # Authorization failures are not hallucination loops
        if tool_result.get("authorization_failure") or tool_result.get("error_type") == "ToolAuthorizationError":
            logger.debug(
                "[ContextPruner] Tool '%s' blocked due to authorization failure, skipping pruning",
                tool_name,
            )
            return

        key = (tool_name, fingerprint) if fingerprint else (tool_name, "")
        if key in self._pending_loop_break:
            return

        suggestion = tool_result.get(
            "suggestion",
            f"Tool '{tool_name}' has been BLOCKED due to repeated failures. "
            "You MUST use read_file() to verify the exact content before retrying.",
        )
        self._pending_loop_break[key] = suggestion
        logger.debug(
            "[ContextPruner] Tool '%s' BLOCKED (fingerprint='%s'), triggering context pruning on next attempt",
            tool_name,
            fingerprint[:30] if fingerprint else "",
        )


__all__ = ["ContextPruner"]
