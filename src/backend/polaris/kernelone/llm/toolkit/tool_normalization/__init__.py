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
from collections.abc import Iterator, Mapping
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
    "normalize_tool_arguments_from_snapshot",
    "normalize_tool_name",
    "validate_tool_path_argument",
]


# CANONICAL STRATEGY: Only allow same-tool command aliases
# Cross-tool mappings (repo_* -> read_file, etc.) are FORBIDDEN
class _OwnerAliasCompatibilityView(Mapping[str, str]):
    """Read-only compatibility projection, never an alias resolver."""

    @staticmethod
    def _bindings() -> dict[str, str]:
        from polaris.kernelone.tool_execution.contracts import frozen_node_to_value
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        value = frozen_node_to_value(ToolSpecRegistry.capture_effective_spec("").alias_binding_view)
        return value if isinstance(value, dict) else {}

    def __getitem__(self, key: str) -> str:
        return self._bindings()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._bindings())

    def __len__(self) -> int:
        return len(self._bindings())


TOOL_NAME_ALIASES: Mapping[str, str] = _OwnerAliasCompatibilityView()


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
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    snapshot = ToolSpecRegistry.capture_effective_spec(str(tool_name or "").strip())
    return snapshot.canonical_tool_name if snapshot.registered else str(tool_name or "").strip().lower()


def _snapshot_argument_namespace(spec: Mapping[str, object]) -> set[str]:
    namespace: set[str] = set()
    arguments = spec.get("arguments")
    if isinstance(arguments, list):
        for argument in arguments:
            if isinstance(argument, Mapping):
                name = str(argument.get("name") or "").strip()
                if name:
                    namespace.add(name)
    aliases = spec.get("arg_aliases")
    if isinstance(aliases, Mapping):
        for alias, canonical in aliases.items():
            alias_name = str(alias or "").strip()
            canonical_name = str(canonical or "").strip()
            if alias_name:
                namespace.add(alias_name)
            if canonical_name:
                namespace.add(canonical_name)
    return namespace


def _snapshot_alias_owner(alias_bindings: Mapping[str, object], raw_name: str) -> str:
    raw = str(raw_name or "").strip()
    if not raw:
        return ""
    for candidate in dict.fromkeys((raw, raw.lower(), raw.replace("-", "_").lower())):
        owner = alias_bindings.get(candidate)
        if isinstance(owner, str) and owner:
            return owner
    return ""


def _unwrap_arguments_from_snapshot(
    *,
    canonical_tool_name: str,
    spec: Mapping[str, object],
    alias_bindings: Mapping[str, object],
    arguments: Mapping[str, object],
) -> dict[str, object]:
    """Unwrap provider envelopes without consulting the mutable registry."""

    namespace = _snapshot_argument_namespace(spec)

    def _decode(value: object) -> dict[str, object] | None:
        if isinstance(value, Mapping):
            return {str(key): item for key, item in value.items()}
        if isinstance(value, str):
            parsed = _parse_json_object(value)
            if parsed is not None:
                return parsed
        return None

    def _walk(payload: Mapping[str, object], depth: int = 0) -> dict[str, object]:
        normalized = {str(key): value for key, value in payload.items()}
        if depth >= 4:
            return normalized

        envelope_name = next(
            (
                value.strip()
                for key in _TOOL_ENVELOPE_NAME_KEYS
                if isinstance((value := normalized.get(key)), str) and value.strip()
            ),
            "",
        )
        if envelope_name:
            if _snapshot_alias_owner(alias_bindings, envelope_name) != canonical_tool_name:
                return normalized
            for key in _JSON_ARGUMENT_WRAPPER_KEYS:
                inner = _decode(normalized.get(key)) if key in normalized else None
                if inner is None:
                    continue
                if namespace and set(inner).issubset(namespace):
                    return inner
                nested = _walk(inner, depth + 1)
                if nested != inner:
                    return nested
                return normalized

        if len(normalized) == 1:
            key, value = next(iter(normalized.items()))
            if key in _JSON_ARGUMENT_WRAPPER_KEYS:
                inner = _decode(value)
                if inner is not None:
                    if namespace and set(inner).issubset(namespace):
                        return inner
                    nested = _walk(inner, depth + 1)
                    if nested != inner:
                        return nested
        return normalized

    return _walk(arguments)


def normalize_tool_arguments_from_snapshot(
    snapshot: object,
    arguments: Mapping[str, object],
) -> dict[str, object]:
    """Normalize one mapping using only a previously captured owner snapshot."""
    from polaris.kernelone.tool_execution.contracts import CapturedToolSpecSnapshotV1, frozen_node_to_value

    if not isinstance(snapshot, CapturedToolSpecSnapshotV1) or not snapshot.registered:
        raise ValueError("registered CapturedToolSpecSnapshotV1 required")
    validated = CapturedToolSpecSnapshotV1(
        raw_tool_name=snapshot.raw_tool_name,
        canonical_tool_name=snapshot.canonical_tool_name,
        registered=snapshot.registered,
        canonical_effective_spec=snapshot.canonical_effective_spec,
        canonical_name_view=snapshot.canonical_name_view,
        alias_binding_view=snapshot.alias_binding_view,
    )
    if (
        snapshot.tool_spec_hash != validated.tool_spec_hash
        or snapshot.canonical_name_view_hash != validated.canonical_name_view_hash
        or snapshot.alias_binding_hash != validated.alias_binding_hash
        or snapshot.snapshot_hash != validated.snapshot_hash
    ):
        raise ValueError("captured tool snapshot hash mismatch")
    spec = frozen_node_to_value(validated.canonical_effective_spec)
    if not isinstance(spec, dict):
        raise ValueError("snapshot effective spec must be a mapping")
    alias_bindings = frozen_node_to_value(validated.alias_binding_view)
    if not isinstance(alias_bindings, dict):
        raise ValueError("snapshot alias binding view must be a mapping")
    from .schema_driven_normalizer import SchemaDrivenNormalizer

    unwrapped = _unwrap_arguments_from_snapshot(
        canonical_tool_name=validated.canonical_tool_name,
        spec=spec,
        alias_bindings=alias_bindings,
        arguments=arguments,
    )
    normalized: dict[str, Any] = SchemaDrivenNormalizer({validated.canonical_tool_name: spec}).normalize(
        validated.canonical_tool_name, unwrapped
    )
    normalizer = TOOL_NORMALIZERS.get(validated.canonical_tool_name)
    if normalizer is not None:
        normalized = normalizer(normalized)
    return normalized


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
    # Capture exactly one owner view for canonical alias resolution.
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    snapshot = ToolSpecRegistry.capture_effective_spec(tool_name)
    normalized_tool_name = snapshot.canonical_tool_name if snapshot.registered else normalize_tool_name(tool_name)
    normalized = _unwrap_json_wrapped_arguments(normalized_tool_name, tool_args)

    # Stage 1: Apply schema-driven normalization (arg_aliases)
    # This handles all parameter alias mappings declared in contracts.py
    if snapshot.registered:
        return dict(normalize_tool_arguments_from_snapshot(snapshot, normalized))
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
