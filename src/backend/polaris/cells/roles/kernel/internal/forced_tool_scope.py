"""Shared forced-tool-scope helpers for transaction-kernel LLM requests."""

from __future__ import annotations

from typing import Any

_FORCED_SCOPE_CONTEXT_TOOL_NAMES = frozenset(
    {
        "file_exists",
        "glob",
        "list_directory",
        "read_file",
        "repo_read_around",
        "repo_read_head",
        "repo_read_slice",
        "repo_read_tail",
        "repo_rg",
        "repo_tree",
    }
)


def _normalize_tool_name(raw_name: Any) -> str:
    token = str(raw_name or "").strip()
    if not token:
        return ""
    try:
        from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_name

        return str(normalize_tool_name(token) or token).strip().lower()
    except (ImportError, RuntimeError, ValueError):
        return token.lower()


def _tool_definition_name(definition: dict[str, Any]) -> str:
    function_payload = definition.get("function")
    if isinstance(function_payload, dict):
        return _normalize_tool_name(function_payload.get("name"))
    return _normalize_tool_name(definition.get("name"))


def _forced_scope_needs_context_companion_tools(context_override: Any) -> bool:
    if not isinstance(context_override, dict):
        return False
    if context_override.get("_transaction_kernel_force_exact_tools") is True:
        return False

    quality_repair = context_override.get("director_quality_repair")
    if not isinstance(quality_repair, dict):
        return False

    if isinstance(quality_repair.get("write_only_single_target"), dict):
        return True
    if quality_repair.get("missing_target_files") or quality_repair.get("repair_target_files"):
        return True
    return bool(quality_repair.get("artifact_quality_errors"))


def augment_forced_transaction_tool_definitions(
    *,
    tool_definitions: list[dict[str, Any]],
    forced_definitions: list[dict[str, Any]],
    context_override: Any,
) -> list[dict[str, Any]]:
    """Keep minimal read/locate tools for forced quality-repair scopes."""

    if not forced_definitions:
        return forced_definitions
    if not _forced_scope_needs_context_companion_tools(context_override):
        return forced_definitions

    merged: list[dict[str, Any]] = [dict(item) for item in forced_definitions]
    seen = {_tool_definition_name(item) for item in merged}
    for definition in tool_definitions:
        if not isinstance(definition, dict):
            continue
        tool_name = _tool_definition_name(definition)
        if tool_name not in _FORCED_SCOPE_CONTEXT_TOOL_NAMES or tool_name in seen:
            continue
        merged.append(dict(definition))
        seen.add(tool_name)
    return merged
