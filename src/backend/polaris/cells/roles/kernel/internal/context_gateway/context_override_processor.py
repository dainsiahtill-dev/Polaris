"""context_override + history tool-message fallback processing.

Extracted (behavior-preserving) from ``gateway.py`` during the G8 god-class
decomposition (blueprint REMAINING_04_gateway-py.md, step 5). The gateway calls
this owner directly for context overrides and history tool-message fallback
materialization.

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

from polaris.kernelone.audit.context_os_prompt import (
    CONTROL_PLANE_PROMPT_CONTENT_KEYS,
    find_control_plane_prompt_content_hits,
    normalize_control_plane_prompt_key,
)
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
_PROMPT_PROJECTION_MAX_DEPTH = 64
_PROMPT_PROJECTION_MAX_NODES = 4096
_PROMPT_PROJECTION_MAX_TOP_LEVEL_ITEMS = 256
_PROMPT_PROJECTION_DEFAULT_STRING_SCAN_CHARS = 1500
_PROMPT_PROJECTION_LIMIT_MARKER = "[FILTERED_CONTEXT_PROJECTION_LIMIT]"
_RAW_AUTHORITY_MARKER_KEYS = frozenset(
    {
        "capability_token_id",
        "job_token_id",
        "parent_token_id",
        "token_id",
    }
)
_RAW_AUTHORITY_SHAPE_KEYS = frozenset(
    {
        "allowed_commands",
        "allowed_paths",
        "allowed_read_paths",
        "allowed_scope",
        "allowed_write_paths",
        "capability_audit",
        "factory_run_id",
        "gate_policy",
        "parent_token_id",
        "project_id",
        "repair_lineage",
        "run_id",
        "stage",
        "token_id",
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
        if not context_override or type(context_override) is not dict:
            return None

        filtered_items: list[str] = []
        has_injection = False
        value_char_cap = _context_override_value_char_cap()

        for item_index, (key, value) in enumerate(context_override.items()):
            if item_index >= _PROMPT_PROJECTION_MAX_TOP_LEVEL_ITEMS:
                filtered_items.append(_PROMPT_PROJECTION_LIMIT_MARKER)
                break
            if type(key) is not str:
                continue
            normalized_key = normalize_control_plane_prompt_key(key)
            if normalized_key.startswith("_") or normalized_key in _CONTROL_PLANE_CONTEXT_KEYS:
                continue

            prompt_value = self._project_prompt_safe_value(
                value,
                _string_scan_chars=value_char_cap,
            )
            str_value = str(prompt_value) if prompt_value is not None else ""
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

    @classmethod
    def _project_prompt_safe_value(
        cls,
        value: Any,
        *,
        _active_ids: set[int] | None = None,
        _depth: int = 0,
        _node_count: list[int] | None = None,
        _string_scan_chars: int = _PROMPT_PROJECTION_DEFAULT_STRING_SCAN_CHARS,
    ) -> Any:
        """Copy mixed data/control structures without nested runtime authority.

        ``context_override`` remains the authoritative runtime mapping consumed
        by ToolGateway/DEO. Only its provider-request projection is copied and
        filtered here. Domain ``task_id`` values remain available because the
        ContextOS audit explicitly treats them as valid contract evidence.
        """
        node_count = _node_count if _node_count is not None else [0]
        node_count[0] += 1
        if _depth >= _PROMPT_PROJECTION_MAX_DEPTH or node_count[0] > _PROMPT_PROJECTION_MAX_NODES:
            return _PROMPT_PROJECTION_LIMIT_MARKER

        value_type = type(value)
        if value is None or value_type in {bool, int, float}:
            return value
        if value_type is str:
            if find_control_plane_prompt_content_hits(value[:_string_scan_chars]):
                return "[FILTERED_CONTROL_PLANE_CONTENT]"
            return value

        if value_type in {dict, list, tuple}:
            active_ids = _active_ids if _active_ids is not None else set()
            identity = id(value)
            if identity in active_ids:
                return "[FILTERED_RECURSIVE_CONTEXT_VALUE]"
            active_ids.add(identity)
            try:
                if value_type is dict:
                    projected: dict[Any, Any] = {}
                    normalized_keys: set[str] = set()
                    remaining_budget = max(0, _PROMPT_PROJECTION_MAX_NODES - node_count[0])
                    for key_index, key in enumerate(value):
                        if key_index >= remaining_budget:
                            break
                        if type(key) is str:
                            normalized_keys.add(normalize_control_plane_prompt_key(key))
                    raw_authority_shape = bool(normalized_keys.intersection(_RAW_AUTHORITY_MARKER_KEYS))
                    for key, child in value.items():
                        node_count[0] += 1
                        if node_count[0] > _PROMPT_PROJECTION_MAX_NODES:
                            projected["projection_limit"] = _PROMPT_PROJECTION_LIMIT_MARKER
                            break
                        if type(key) is not str:
                            continue
                        normalized_key = normalize_control_plane_prompt_key(key)
                        if (
                            normalized_key.startswith("_")
                            or normalized_key in CONTROL_PLANE_PROMPT_CONTENT_KEYS
                            or (raw_authority_shape and normalized_key in _RAW_AUTHORITY_SHAPE_KEYS)
                        ):
                            continue
                        projected[key] = cls._project_prompt_safe_value(
                            child,
                            _active_ids=active_ids,
                            _depth=_depth + 1,
                            _node_count=node_count,
                            _string_scan_chars=_string_scan_chars,
                        )
                        if node_count[0] > _PROMPT_PROJECTION_MAX_NODES:
                            break
                    return projected
                if value_type is list:
                    projected_list: list[Any] = []
                    for item in value:
                        projected_list.append(
                            cls._project_prompt_safe_value(
                                item,
                                _active_ids=active_ids,
                                _depth=_depth + 1,
                                _node_count=node_count,
                                _string_scan_chars=_string_scan_chars,
                            )
                        )
                        if node_count[0] > _PROMPT_PROJECTION_MAX_NODES:
                            break
                    return projected_list
                projected_tuple: list[Any] = []
                for item in value:
                    projected_tuple.append(
                        cls._project_prompt_safe_value(
                            item,
                            _active_ids=active_ids,
                            _depth=_depth + 1,
                            _node_count=node_count,
                            _string_scan_chars=_string_scan_chars,
                        )
                    )
                    if node_count[0] > _PROMPT_PROJECTION_MAX_NODES:
                        break
                return tuple(projected_tuple)
            finally:
                active_ids.remove(identity)

        # Context overrides are a JSON-like contract. Never call arbitrary
        # ``__str__`` methods in the Provider request projection: opaque objects
        # can serialize complete capability or execution-attempt authority.
        return "[FILTERED_UNPROJECTABLE_CONTEXT_OBJECT]"

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
