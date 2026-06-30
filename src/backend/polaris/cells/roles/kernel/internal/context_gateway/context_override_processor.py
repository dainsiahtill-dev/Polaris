"""context_override + history tool-message fallback processing.

Extracted (behavior-preserving) from ``gateway.py`` during the G8 god-class
decomposition (blueprint REMAINING_04_gateway-py.md, step 5). The gateway keeps
delegating shims (``_process_context_override`` /
``_extract_tool_messages_from_history`` / ``_process_tool_messages_for_fallback``)
with identical signatures — those names are frozen by test reach-ins.

Per the type-hygiene rule, the symbols this module needs (``SecuritySanitizer``,
``_context_override_value_char_cap``, ``_CONTROL_PLANE_CONTEXT_KEYS``) are
imported from their canonical sources directly — none are monkeypatched through
the gateway module namespace.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from polaris.kernelone.context.prompt_safety import prompt_safe_tool_failure_summary

from .gateway_helpers import _CONTROL_PLANE_CONTEXT_KEYS, _context_override_value_char_cap
from .security import SecuritySanitizer

logger = logging.getLogger(__name__)


_CONTEXT_OVERRIDE_TOOL_FAILURE_KEYS = frozenset(
    {
        "artifact",
        "artifacts",
        "history",
        "last_event",
        "last_outcome",
        "recent_decisions",
        "recent_episodes",
        "tool",
        "tool_result",
        "tool_results",
        "transcript",
    }
)


class ContextOverrideProcessor:
    """Filters context_override into a system message + materializes tool-message
    fallbacks from history when state-first projection is inactive.

    Stateless aside from the gateway's ``detect_prompt_injection`` config flag.
    """

    def __init__(self, *, detect_prompt_injection: bool) -> None:
        self._detect_prompt_injection = detect_prompt_injection

    def process_context_override(self, context_override: dict[str, Any]) -> dict[str, Any] | None:
        """Process context_override dict with prompt injection detection.

        Args:
            context_override: Dict of context key-value pairs to inject.

        Returns:
            Message dict with filtered content, or None if empty.
        """
        if not context_override or not isinstance(context_override, dict):
            return None

        filtered_items: list[str] = []
        has_injection = False
        value_char_cap = _context_override_value_char_cap()

        for key, value in context_override.items():
            if not isinstance(key, str):
                continue
            normalized_key = key.strip().lower()
            if normalized_key.startswith("_") or normalized_key in _CONTROL_PLANE_CONTEXT_KEYS:
                continue

            str_value = str(value) if value is not None else ""
            str_value = self._prompt_safe_context_value(normalized_key, str_value)
            # Bound each value so no single oversized payload can blow the window
            # (order-4): the weak model cannot use multi-thousand-token metadata.
            if len(str_value) > value_char_cap:
                str_value = str_value[:value_char_cap] + " …[truncated]"

            # Detect prompt injection patterns
            if self._detect_prompt_injection:
                if SecuritySanitizer.looks_like_prompt_injection(str_value):
                    # Degrade, don't destroy: platform-internal payloads
                    # (cognitive_guidance, session_turn_events) routinely
                    # contain instruction-like text and were being replaced
                    # wholesale with a [FILTERED] stub, deleting the model's
                    # own strategy guidance every turn (factory-bench L2-10
                    # live). Same neutralization contract as history content:
                    # escape + mark untrusted, keep the information.
                    has_injection = True
                    neutralized = SecuritySanitizer.sanitize_history_content(str_value, detect_injection=True)
                    filtered_items.append(f"{key}: {neutralized}")
                    logger.warning("Prompt injection detected in context_override key: %s", key)
                    continue

                # Check for dangerous key names
                if any(d in key.lower() for d in ("system", "role", "ignore", "override")):
                    has_injection = True
                    filtered_items.append(f"{key}: [FILTERED_SUSPICIOUS_KEY]")
                    logger.warning("Suspicious context_override key: %s", key)
                    continue

            filtered_items.append(f"{key}: {str_value}")

        if not filtered_items:
            return None

        content = "\n".join(filtered_items)
        if has_injection:
            content = "⚠️ CONTEXT_OVERRIDE_WITH_FILTERED_CONTENT:\n" + content

        return {"role": "system", "name": "context_override", "content": content}

    def extract_tool_messages_from_history(
        self, history: Sequence[tuple[str, str] | dict[str, str]]
    ) -> list[dict[str, Any]]:
        """Extract tool messages from history for fallback when state-first mode is inactive.

        Args:
            history: List of (role, content) tuples or dict messages.

        Returns:
            List of tool message dicts.
        """
        tool_messages: list[dict[str, Any]] = []
        for item in history:
            if isinstance(item, dict):
                role = str(item.get("role", "")).lower()
                content = item.get("content", "")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                role = str(item[0]).lower()
                content = str(item[1])
            else:
                continue

            if role == "tool":
                tool_messages.append(
                    {
                        "role": "tool",
                        "content": self._prompt_safe_tool_content(content),
                    }
                )
        return tool_messages

    def process_tool_messages_for_fallback(
        self,
        tool_messages: list[dict[str, Any]],
        max_chars: int = 2000,
    ) -> list[dict[str, Any]]:
        """Process tool messages for fallback, truncating large content.

        Args:
            tool_messages: List of tool message dicts.
            max_chars: Maximum characters before truncation.

        Returns:
            List of processed tool messages with CONTEXT_TRUNCATED markers if needed.
        """
        processed: list[dict[str, Any]] = []
        for msg in tool_messages:
            role = msg.get("role", "tool")
            content = self._prompt_safe_tool_content(msg.get("content", ""))
            if len(content) > max_chars:
                truncated_content = content[:max_chars]
                new_content = (
                    f"{truncated_content}\n\n"
                    f"[CONTEXT_TRUNCATED: Original {len(content)} chars, truncated to {max_chars} chars]"
                )
                processed.append({"role": role, "content": new_content})
            else:
                # Always return a copy to avoid mutation of the original object
                processed.append({"role": role, "content": content})
        return processed

    @staticmethod
    def _prompt_safe_tool_content(content: Any) -> str:
        text = str(content or "")
        return prompt_safe_tool_failure_summary("tool", text) or text

    @staticmethod
    def _prompt_safe_context_value(normalized_key: str, value: str) -> str:
        key_parts = {part for part in normalized_key.replace("-", "_").split("_") if part}
        if normalized_key not in _CONTEXT_OVERRIDE_TOOL_FAILURE_KEYS and not key_parts.intersection(
            _CONTEXT_OVERRIDE_TOOL_FAILURE_KEYS
        ):
            return value
        return prompt_safe_tool_failure_summary("tool", value) or value
