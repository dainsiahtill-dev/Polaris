"""Stateless tool-surface reduction policy for RoleExecutionKernel.

Cognitive-Runtime and ContextGateway tool-surface reductions extracted verbatim
from ``core.py`` as module-level functions. ContextGateway decisions may only
*reduce* the tool surface; they never grant access to tools outside the role
profile whitelist.

``core.py`` binds these onto ``RoleExecutionKernel`` as ``@staticmethod`` shims
so callers (``RoleExecutionKernel._apply_runtime_tool_policy`` etc.) keep working
unchanged. This module must not import ``core.py`` at module top-level.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleTurnRequest

_CONTEXT_SAFE_MUTATING_TOOL_EXCEPTIONS = frozenset(
    {
        "compact_context",
        "update_session_state",
    }
)
_CONTEXT_EXPENSIVE_TOOL_NAMES = frozenset({"read_file"})


def _cognitive_runtime_blocked_tools(request: RoleTurnRequest) -> frozenset[str]:
    """Return canonical tool names blocked by Cognitive Runtime mainline policy."""
    values: list[Any] = []
    metadata = getattr(request, "metadata", None)
    if isinstance(metadata, dict):
        policy = metadata.get("cognitive_tool_policy")
        if isinstance(policy, dict):
            values.extend(_iter_tool_policy_values(policy.get("blocked_tools")))
        values.extend(_iter_tool_policy_values(metadata.get("cognitive_runtime_blocked_tools")))

    context_override = getattr(request, "context_override", None)
    if isinstance(context_override, dict):
        guidance = context_override.get("cognitive_guidance")
        if isinstance(guidance, dict):
            values.extend(_iter_tool_policy_values(guidance.get("blocked_tools")))
        policy = context_override.get("cognitive_tool_policy")
        if isinstance(policy, dict):
            values.extend(_iter_tool_policy_values(policy.get("blocked_tools")))

    normalized: set[str] = set()
    for value in values:
        tool_name = _normalize_tool_policy_name(value)
        if tool_name:
            normalized.add(tool_name)
    return frozenset(normalized)


def _iter_tool_policy_values(raw_value: Any) -> tuple[Any, ...]:
    if isinstance(raw_value, (list, tuple, set, frozenset)):
        return tuple(raw_value)
    if isinstance(raw_value, str):
        return (raw_value,)
    return ()


def _normalize_tool_policy_name(raw_name: Any) -> str:
    token = str(raw_name or "").strip()
    if not token:
        return ""
    try:
        from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_name

        return str(normalize_tool_name(token) or token).strip().lower()
    except (ImportError, RuntimeError, ValueError):
        return token.lower()


def _filter_cognitive_blocked_tool_definitions(
    tool_definitions: list[dict[str, Any]],
    blocked_tools: frozenset[str],
) -> list[dict[str, Any]]:
    """Remove tools disallowed by Cognitive Runtime from native LLM schemas."""
    if not tool_definitions or not blocked_tools:
        return tool_definitions
    filtered: list[dict[str, Any]] = []
    for definition in tool_definitions:
        schema_name = ""
        if isinstance(definition, dict):
            function_payload = definition.get("function")
            if isinstance(function_payload, dict):
                schema_name = str(function_payload.get("name") or "").strip()
            else:
                schema_name = str(definition.get("name") or "").strip()
        if _normalize_tool_policy_name(schema_name) in blocked_tools:
            continue
        filtered.append(definition)
    return filtered


def _runtime_tool_policy_from_context(
    context_result: Any,
) -> tuple[frozenset[str], dict[str, Any]]:
    """Derive tool-surface reductions from ContextGateway decision hints.

    ContextGateway decisions may only reduce the tool surface. They never
    grant access to tools outside the role profile whitelist.
    """

    metadata = getattr(context_result, "metadata", None)
    metadata_payload = metadata if isinstance(metadata, dict) else {}
    hints = metadata_payload.get("context_decision_hints")
    hints_payload = hints if isinstance(hints, dict) else {}

    suppress_expensive_context_tools = bool(
        hints_payload.get("suppress_expensive_context_tools") or metadata_payload.get("budget_pressure_detected")
    )
    suppress_mutating_tools = bool(hints_payload.get("suppress_mutating_tools"))
    blocked: set[str] = set()

    if suppress_expensive_context_tools:
        blocked.update(_CONTEXT_EXPENSIVE_TOOL_NAMES)

    if suppress_mutating_tools:
        try:
            from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

            for spec in ToolSpecRegistry.get_write_tools() + ToolSpecRegistry.get_exec_tools():
                tool_name = _normalize_tool_policy_name(spec.canonical_name)
                if tool_name and tool_name not in _CONTEXT_SAFE_MUTATING_TOOL_EXCEPTIONS:
                    blocked.add(tool_name)
        except (ImportError, RuntimeError, ValueError):
            blocked.update(
                {
                    "append_to_file",
                    "apply_patch",
                    "background_run",
                    "edit_blocks",
                    "edit_file",
                    "execute_command",
                    "precision_edit",
                    "repo_apply_diff",
                    "search_replace",
                    "write_file",
                }
            )

    audit = {
        "context_tool_policy_applied": bool(blocked),
        "context_blocked_tools": sorted(blocked),
        "context_decision_hints": dict(hints_payload),
    }
    return frozenset(blocked), audit


def _apply_runtime_tool_policy(
    *,
    request: RoleTurnRequest,
    context_result: Any,
    tool_definitions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cognitive_blocked_tools = _cognitive_runtime_blocked_tools(request)
    filtered = _filter_cognitive_blocked_tool_definitions(
        tool_definitions,
        cognitive_blocked_tools,
    )
    context_blocked_tools, context_audit = _runtime_tool_policy_from_context(context_result)
    filtered = _filter_cognitive_blocked_tool_definitions(
        filtered,
        context_blocked_tools,
    )
    return filtered, {
        **context_audit,
        "cognitive_tool_policy_applied": bool(cognitive_blocked_tools),
        "cognitive_blocked_tools": sorted(cognitive_blocked_tools),
    }


def _extract_forced_transaction_tool_definitions(context_override: Any) -> list[dict[str, Any]] | None:
    """Return explicit TransactionKernel forced tool definitions, if present."""
    if not isinstance(context_override, dict):
        return None
    raw_definitions = context_override.get("_transaction_kernel_forced_tool_definitions")
    if not isinstance(raw_definitions, list):
        return None
    forced_definitions = [dict(item) for item in raw_definitions if isinstance(item, dict)]
    if forced_definitions:
        return forced_definitions
    forced_choice = str(context_override.get("_transaction_kernel_forced_tool_choice") or "").strip().lower()
    if forced_choice == "none":
        return []
    return None


def _apply_forced_transaction_tool_definitions(
    tool_definitions: list[dict[str, Any]],
    context_override: Any,
) -> list[dict[str, Any]]:
    """Prefer explicit TransactionKernel forced tool scope over default role tools."""
    forced_definitions = _extract_forced_transaction_tool_definitions(context_override)
    if forced_definitions is None:
        return tool_definitions
    return forced_definitions
