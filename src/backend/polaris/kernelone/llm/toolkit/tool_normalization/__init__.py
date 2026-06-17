"""Tool name and argument normalization.

CANONICAL STRATEGY (2026-03-28):
- Tool name aliases are ONLY for command-line style aliases (execute_command variants)
- Cross-tool semantic mapping is FORBIDDEN
- repo_* tools are CANONICAL and MUST NOT be mapped to other tools
- Only same-tool parameter aliases are allowed

Single source of truth for:
- tool alias mapping
- common argument alias handling
"""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from polaris.kernelone.llm.toolkit.ts_availability import (
    TreeSitterAvailability,
    is_tree_sitter_available,
)

# NOTE: TS_DEPENDENT_TOOLS is lazily imported inside get_available_tools()
# to avoid circular import with polaris.kernelone.tool_execution.contracts
# Import normalizers from the normalizers subpackage
from .normalizers import TOOL_NORMALIZERS

# Re-export shared helpers for backwards compatibility
from .normalizers._shared import (
    WriteContentNormalization,
    looks_like_patch_like_write_content,
    normalize_patch_like_write_content,
)

__all__ = [
    # Aliases registry
    "TOOL_NAME_ALIASES",
    "WriteContentNormalization",
    # Core normalization functions
    "get_available_tools",
    # Path validation
    "is_path_safe_for_workspace",
    "looks_like_patch_like_write_content",
    # Patch content normalization
    "normalize_patch_like_write_content",
    "normalize_tool_arguments",
    "normalize_tool_name",
    "validate_tool_path_argument",
]


# CANONICAL STRATEGY: Only allow same-tool command aliases
# Cross-tool mappings (repo_* -> read_file, etc.) are FORBIDDEN
TOOL_NAME_ALIASES: dict[str, str] = {
    # command execution aliases (same tool, different invocation style)
    # All map to: execute_command
    "run_command": "execute_command",
    "run_shell": "execute_command",
    "exec_cmd": "execute_command",
    "shell_execute": "execute_command",
    "system_call": "execute_command",
    "command_line": "execute_command",
    # NOTE: All other tool names are CANONICAL - do NOT add aliases here
    # Any alias that maps to a different tool is a policy violation
}


_JSON_ARGUMENT_WRAPPER_KEYS = frozenset(
    {
        "arguments",
        "parameters",
        "params",
        "input",
        "args",
        "kwargs",
        "tool_input",
        "tool_arguments",
        "tool_args",
        "function_arguments",
        "function_args",
    }
)
_TOOL_ENVELOPE_NAME_KEYS = frozenset(
    {
        "name",
        "tool",
        "tool_name",
        "toolName",
        "function_name",
        "functionName",
    }
)


def _single_object_array(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], Mapping):
        return dict(value[0])
    return None


def _parse_json_object(value: str) -> dict[str, Any] | None:
    token = value.strip()
    if not token or not token.startswith(("{", "[")):
        return None
    try:
        parsed = json.loads(token)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(token)
        except (ValueError, SyntaxError):
            return None
    if isinstance(parsed, dict):
        return parsed
    return _single_object_array(parsed)


def _tool_argument_namespace(tool_name: str) -> set[str]:
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    spec = ToolSpecRegistry.get_all_specs().get(tool_name) or {}
    keys: set[str] = set()
    for arg in spec.get("arguments", []):
        if isinstance(arg, Mapping):
            name = str(arg.get("name") or "").strip()
            if name:
                keys.add(name)
    arg_aliases = spec.get("arg_aliases", {})
    if isinstance(arg_aliases, Mapping):
        for alias, canonical in arg_aliases.items():
            alias_key = str(alias or "").strip()
            canonical_key = str(canonical or "").strip()
            if alias_key:
                keys.add(alias_key)
            if canonical_key:
                keys.add(canonical_key)
    return keys


def _object_keys_belong_to_tool(tool_name: str, payload: Mapping[str, Any]) -> bool:
    namespace = _tool_argument_namespace(tool_name)
    return bool(namespace) and set(payload).issubset(namespace)


def _extract_same_tool_envelope_arguments(tool_name: str, payload: Mapping[str, Any]) -> dict[str, Any] | None:
    envelope_tool_name: str | None = None
    for key in _TOOL_ENVELOPE_NAME_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            envelope_tool_name = value.strip()
            break
    if envelope_tool_name is None:
        return None

    if normalize_tool_name(envelope_tool_name) != tool_name:
        return None

    for key in _JSON_ARGUMENT_WRAPPER_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, Mapping):
            if _object_keys_belong_to_tool(tool_name, value):
                return dict(value)
            unwrapped = _unwrap_json_wrapped_arguments(tool_name, value)
            if unwrapped != value:
                return unwrapped
            return None
        if isinstance(value, str):
            parsed = _parse_json_object(value)
            if parsed is None:
                return None
            if _object_keys_belong_to_tool(tool_name, parsed):
                return dict(parsed)
            unwrapped = _unwrap_json_wrapped_arguments(tool_name, parsed)
            if unwrapped != parsed:
                return unwrapped
            return None
    return None


def _unwrap_json_wrapped_arguments(tool_name: str, tool_args: Any) -> dict[str, Any]:
    if isinstance(tool_args, str):
        parsed = _parse_json_object(tool_args)
        if parsed is not None and _object_keys_belong_to_tool(tool_name, parsed):
            return dict(parsed)
        if parsed is not None:
            unwrapped = _unwrap_json_wrapped_arguments(tool_name, parsed)
            if unwrapped != parsed:
                return unwrapped
        return {}

    single_object = _single_object_array(tool_args)
    if single_object is not None:
        if _object_keys_belong_to_tool(tool_name, single_object):
            return single_object
        unwrapped = _unwrap_json_wrapped_arguments(tool_name, single_object)
        if unwrapped != single_object:
            return unwrapped
        return {}

    if not isinstance(tool_args, Mapping):
        return {}

    normalized = dict(tool_args)
    enveloped = _extract_same_tool_envelope_arguments(tool_name, normalized)
    if enveloped is not None:
        return enveloped

    if len(normalized) != 1:
        return normalized

    key, value = next(iter(normalized.items()))
    if str(key) not in _JSON_ARGUMENT_WRAPPER_KEYS:
        return normalized

    if isinstance(value, Mapping):
        if _object_keys_belong_to_tool(tool_name, value):
            return dict(value)
        unwrapped = _unwrap_json_wrapped_arguments(tool_name, value)
        if unwrapped != value:
            return unwrapped
        return normalized

    if not isinstance(value, str):
        return normalized

    parsed = _parse_json_object(value)
    if parsed is not None and _object_keys_belong_to_tool(tool_name, parsed):
        return dict(parsed)
    return normalized


def normalize_tool_name(tool_name: str) -> str:
    """Normalize tool name by applying alias mappings.

    Resolution order:
    1. Explicit TOOL_NAME_ALIASES (for command-style aliases)
    2. ToolSpecRegistry canonical/alias resolution
    3. Schema-driven resolution fallback via contracts.py aliases
    """
    raw_token = str(tool_name or "").strip()
    token = raw_token.lower()
    # First check explicit TOOL_NAME_ALIASES
    if token in TOOL_NAME_ALIASES:
        return TOOL_NAME_ALIASES[token]
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    registry_name = ToolSpecRegistry.get_canonical(raw_token)
    if registry_name != raw_token:
        return registry_name
    # Then resolve via schema-driven alias resolution
    from .schema_driven_normalizer import get_schema_normalizer

    return get_schema_normalizer()._resolve_tool_alias(token)


def normalize_tool_arguments(
    tool_name: str,
    tool_args: Any,
) -> dict[str, Any]:
    """Normalize tool arguments using two-stage normalization.

    Stage 1: Schema-driven normalization via arg_aliases (contracts.py)
    Stage 2: Per-tool complex transformations (TOOL_NORMALIZERS)

    This ensures contracts.py arg_aliases is the single source of truth for
    parameter aliases, while per-tool normalizers handle complex transformations
    that cannot be expressed as simple alias mappings.
    """
    # First resolve tool name aliases
    normalized_tool_name = normalize_tool_name(tool_name)
    normalized = _unwrap_json_wrapped_arguments(normalized_tool_name, tool_args)

    # Stage 1: Apply schema-driven normalization (arg_aliases)
    # This handles all parameter alias mappings declared in contracts.py
    from .schema_driven_normalizer import normalize_with_schema

    normalized = normalize_with_schema(normalized_tool_name, normalized)

    # Stage 2: Apply per-tool complex transformations
    # Only for tools with special transformations (range params, clamping, etc.)
    normalizer = TOOL_NORMALIZERS.get(normalized_tool_name)
    if normalizer is not None:
        normalized = normalizer(normalized)

    return normalized


# ============================================================================
# Path safety validation
# ============================================================================


def is_path_safe_for_workspace(path: str, workspace: str) -> tuple[bool, str]:
    """Verify path is within workspace.

    Args:
        path: Relative or absolute path
        workspace: Workspace root directory

    Returns:
        (is_safe, full_path_or_error_message)
    """
    import urllib.parse

    if not path:
        return False, "Empty path"

    try:
        # URL decode path
        decoded = urllib.parse.unquote(path)
        decoded = urllib.parse.unquote(decoded)
    except (UnicodeDecodeError, ValueError):
        return False, f"Path decode failed: {path}"

    # Detect path traversal patterns
    dangerous_patterns = [
        "../",
        "..\\",
        "%2e%2e%2f",
        "%252e%252e%252f",
        "%2e%2e%5c",
        "%252e%252e%255c",
    ]
    lower = decoded.lower()
    if any(p in lower for p in dangerous_patterns):
        return False, f"Path traversal detected: {path}"

    try:
        workspace_real = Path(workspace).resolve()
        target = (workspace_real / decoded).resolve()
        target.relative_to(workspace_real)
        return True, str(target)
    except (ValueError, OSError) as e:
        return False, f"Path outside workspace: {path} ({e})"


def validate_tool_path_argument(
    tool_name: str,
    path: str | None,
    workspace: str,
) -> tuple[bool, str]:
    """Validate tool path argument safety.

    Args:
        tool_name: Tool name
        path: Path argument
        workspace: Workspace root directory

    Returns:
        (is_safe, error_message_or_empty_string)
    """
    if not path:
        return True, ""  # Empty path handled by tool itself

    # Tools that need path validation
    path_tools = {
        "read_file",
        "write_file",
        "edit_file",
        "append_to_file",
        "search_replace",
        "file_exists",
        "list_directory",
    }

    if tool_name not in path_tools:
        return True, ""

    safe, result = is_path_safe_for_workspace(path, workspace)
    if not safe:
        return False, f"Security validation failed for {tool_name}: {result}"
    return True, ""


def get_available_tools(
    requested_tools: list[str],
    ts_availability: TreeSitterAvailability | None = None,
) -> list[str]:
    """Filter tools based on tree-sitter availability.

    When tree-sitter is unavailable, removes TS_DEPENDENT_TOOLS from
    the requested tools list.

    Args:
        requested_tools: List of requested tool names.
        ts_availability: TS availability status (None for auto-detect).

    Returns:
        Filtered list of available tools (order preserved).
    """
    # Get TS availability (auto-detect with caching)
    if ts_availability is None:
        ts_availability = is_tree_sitter_available()

    if ts_availability.available:
        # TS available, all tools are usable
        return list(requested_tools)

    # TS unavailable, filter out dependent tools
    # Lazy import to avoid circular dependency with tools.contracts
    from polaris.kernelone.tool_execution.contracts import TS_DEPENDENT_TOOLS

    available = [t for t in requested_tools if t not in TS_DEPENDENT_TOOLS]
    return available
