from __future__ import annotations

import re
import warnings
from typing import TYPE_CHECKING, Any

from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry
from polaris.kernelone.tool_execution.validators import (
    ERROR_ARRAY_TOO_LONG,
    ERROR_ARRAY_TOO_SHORT,
    ERROR_INTEGER_TOO_LARGE,
    ERROR_INTEGER_TOO_SMALL,
    ERROR_INVALID_TYPE,
    ERROR_REQUIRED_MISSING,
    ERROR_STRING_PATTERN_MISMATCH,
    ERROR_STRING_TOO_LONG,
    ERROR_STRING_TOO_SHORT,
    BaseValidator,
    ValidationResult,
    get_validator,
)

# Alias for internal use
_get_validator = get_validator

ToolSpec = dict[str, Any]

# Tool-level error codes (not in validators.py)
ERROR_UNKNOWN_TOOL = "UNKNOWN_TOOL"
ERROR_INVALID_TOOL_ARGS = "INVALID_TOOL_ARGS"

# Tree-sitter 依赖工具集（当 TS 不可用时，这些工具应从 LLM 配置中过滤）
TS_DEPENDENT_TOOLS: frozenset[str] = frozenset(
    {
        "repo_symbols_index",
        "treesitter_find_symbol",
        "treesitter_replace_node",
        "treesitter_insert_method",
        "treesitter_rename_symbol",
    }
)


@warnings.deprecated("Use ToolSpecRegistry.clear() instead.")
def reset_tool_spec_registry_cache() -> None:
    """Reset the cached ToolSpecRegistry reference for test isolation.

    This clears the registry cache so that the next access will
    re-initialize the registry. Should be called before ToolSpecRegistry.clear()
    to ensure a fresh registry on re-initialization.
    """
    ToolSpecRegistry.clear()


# Import type for type hints only
if TYPE_CHECKING:
    from collections.abc import Iterable

# Backward-compatible aliases for external code that may use short names
# These map to the descriptive names from validators.py
ERROR_MIN_LENGTH = ERROR_STRING_TOO_SHORT
ERROR_MAX_LENGTH = ERROR_STRING_TOO_LONG
ERROR_PATTERN = ERROR_STRING_PATTERN_MISMATCH
ERROR_MINIMUM = ERROR_INTEGER_TOO_SMALL
ERROR_MAXIMUM = ERROR_INTEGER_TOO_LARGE

# Public API: error codes exported from this module
__all__ = [
    "ERROR_ARRAY_TOO_LONG",
    "ERROR_ARRAY_TOO_SHORT",
    "ERROR_INTEGER_TOO_LARGE",
    "ERROR_INTEGER_TOO_SMALL",
    "ERROR_INVALID_TOOL_ARGS",
    # Validation error codes (re-exported from validators)
    "ERROR_INVALID_TYPE",
    "ERROR_MAXIMUM",
    "ERROR_MAX_LENGTH",
    "ERROR_MINIMUM",
    # Short-name aliases (backward compatibility)
    "ERROR_MIN_LENGTH",
    "ERROR_PATTERN",
    "ERROR_REQUIRED_MISSING",
    "ERROR_STRING_PATTERN_MISMATCH",
    "ERROR_STRING_TOO_LONG",
    # Descriptive names (re-exported from validators)
    "ERROR_STRING_TOO_SHORT",
    # Tool-level error codes
    "ERROR_UNKNOWN_TOOL",
    # Tree-sitter dependent tools (filtered when TS unavailable)
    "TS_DEPENDENT_TOOLS",
]


class _ToolSpecsProxy:
    """Backward-compatible proxy over ToolSpecRegistry._registry.

    DEPRECATED (2026-04-16): Direct _TOOL_SPECS access is deprecated.
    Use ToolSpecRegistry methods instead.
    """

    def _get_registry(self) -> dict[str, dict[str, Any]]:
        return ToolSpecRegistry._get_registry()

    def get(self, key: str, default: Any = None) -> Any:
        return self._get_registry().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._get_registry()[key]

    def __contains__(self, key: object) -> bool:
        return key in self._get_registry()

    def keys(self) -> Any:
        return self._get_registry().keys()

    def items(self) -> Any:
        return self._get_registry().items()

    def values(self) -> Any:
        return self._get_registry().values()

    def __iter__(self) -> Any:
        return iter(self._get_registry())

    def __len__(self) -> int:
        return len(self._get_registry())


# DEPRECATED (2026-04-16): _TOOL_SPECS is a thin backward-compatible wrapper
# over ToolSpecRegistry. New code should use ToolSpecRegistry directly.
_TOOL_SPECS: dict[str, Any] = _ToolSpecsProxy()  # type: ignore[assignment]


def canonicalize_tool_name(name: str, *, keep_unknown: bool = True) -> str:
    """Resolve a tool name or alias to its canonical form.

    Args:
        name: The tool name or alias to canonicalize.
        keep_unknown: If True, unknown (unregistered) names are returned as-is.
            If False, unknown names return an empty string.

    Returns:
        The canonical tool name, or empty string if unknown and keep_unknown=False.
    """
    cleaned = str(name or "").strip()
    if not cleaned:
        return ""
    return ToolSpecRegistry.get_canonical(cleaned)


@warnings.deprecated(
    "Use _coerce_int/_coerce_bool from polaris.kernelone.llm.toolkit.tool_normalization.normalizers._shared instead."
)
def _has_value(value: Any) -> bool:
    """Check if a value is considered non-empty.

    DEPRECATED (2026-04-05): Use _coerce_int/_coerce_bool from
    polaris.kernelone.llm.toolkit.tool_normalization.normalizers._shared instead.
    """
    if value is None:
        return False
    if isinstance(value, str):
        # NOTE: Empty string "" is a valid string value (e.g., creating an empty file)
        # We only reject None and strings that are entirely whitespace.
        return True
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


@warnings.deprecated("Use normalize_tool_arguments() from polaris.kernelone.llm.toolkit.tool_normalization instead.")
def normalize_tool_args(tool: str, args: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize tool arguments by applying alias resolution, type conversions, and defaults.

    DEPRECATED (2026-04-05): Use normalize_tool_arguments() from
    polaris.kernelone.llm.toolkit.tool_normalization instead.
    This function now delegates to the canonical implementation.

    Args:
        tool: The canonical or alias tool name.
        args: The arguments dictionary to normalize. May be None.

    Returns:
        A normalized arguments dictionary with canonical names, proper types,
        and default values filled in.
    """
    # Delegate to canonical implementation in llm/toolkit
    from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments

    return normalize_tool_arguments(tool, args)


def _format_error(tool_name: str, description: str) -> str:
    """Format error message in unified {tool_name}: {description} format."""
    return f"{tool_name}: {description}"


def _is_present(value: Any) -> bool:
    """Check if a value is considered non-empty (non-deprecated internal helper)."""
    if value is None:
        return False
    if isinstance(value, str):
        return True
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def validate_tool_step(tool: str, args: dict[str, Any] | None) -> tuple[bool, str | None, str]:
    """Validate a tool name and its arguments against registered tool specs.

    Args:
        tool: The canonical or alias tool name to validate.
        args: The arguments dictionary to validate. May be None or non-dict.

    Returns:
        A tuple of (is_valid, error_code, error_message):
        - is_valid: True if the tool is known and all required args are present.
        - error_code: None if valid, otherwise an error code string
          (e.g., "UNKNOWN_TOOL", "INVALID_TOOL_ARGS").
        - error_message: Human-readable error description in format
          "{tool_name}: {error_description}", or empty string if valid.
    """
    registry = ToolSpecRegistry._get_registry()
    canonical_tool = canonicalize_tool_name(tool)
    if not registry.get(canonical_tool):
        allowed_tools = ", ".join(sorted(supported_tool_names()))
        return (
            False,
            ERROR_UNKNOWN_TOOL,
            _format_error(canonical_tool, f"unsupported tool. Allowed: {allowed_tools}"),
        )

    from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments

    normalized = normalize_tool_arguments(canonical_tool, args if isinstance(args, dict) else {})
    spec = _TOOL_SPECS.get(canonical_tool, {})

    # Validate required arguments
    required_any = spec.get("required_any", [])
    if isinstance(required_any, list):
        for group in required_any:
            group_values = group if isinstance(group, (list, tuple)) else [group]
            if not any(_is_present(normalized.get(str(key))) for key in group_values):
                required_text = " or ".join(str(key) for key in group_values)
                return (
                    False,
                    ERROR_REQUIRED_MISSING,
                    _format_error(canonical_tool, f"missing required argument: {required_text}"),
                )

    # Validate tool-specific constraints (background_run timeout)
    # Check RAW args (before normalization) so validation catches invalid inputs
    # that would be clamped/fixed by the normalizer
    if canonical_tool == "background_run":
        raw_args = args if isinstance(args, dict) else {}
        # Resolve timeout from raw args (handles max_seconds alias at raw level)
        timeout_value = raw_args.get("timeout", raw_args.get("max_seconds"))
        try:
            timeout_int = int(timeout_value) if timeout_value is not None else 300
        except (ValueError, TypeError):
            timeout_int = 0
        if timeout_int <= 0:
            return (
                False,
                ERROR_INVALID_TOOL_ARGS,
                _format_error(canonical_tool, "timeout must be greater than 0"),
            )
        if timeout_int > 3600:
            return (
                False,
                ERROR_INVALID_TOOL_ARGS,
                _format_error(canonical_tool, "timeout must be less than or equal to 3600"),
            )

    # Type validation using validators
    for arg_spec in spec.get("arguments", []):
        arg_name = arg_spec.get("name")
        if arg_name not in normalized:
            continue

        value = normalized[arg_name]
        arg_type = arg_spec.get("type")

        validator = _get_validator(arg_type)
        if validator:
            result = validator.validate(value, arg_spec)
            if not result.is_valid and result.error:
                return False, result.error.code, _format_error(canonical_tool, result.error.message)

    return True, None, ""


def read_tool_names() -> list[str]:
    """Return all registered tool names in the "read" category.

    Returns:
        Alphabetically sorted list of read-category tool names.
    """
    return sorted([spec.canonical_name for spec in ToolSpecRegistry.get_read_tools()])


def write_tool_names() -> list[str]:
    """Return all registered tool names in the "write" category.

    Returns:
        Alphabetically sorted list of write-category tool names.
    """
    return sorted([spec.canonical_name for spec in ToolSpecRegistry.get_write_tools()])


def supported_tool_names() -> list[str]:
    """Return all registered canonical tool names across all categories.

    Returns:
        Alphabetically sorted list of all tool names (read, write, and exec).
    """
    return ToolSpecRegistry.get_all_canonical_names()


def list_tool_contracts(categories: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """Return tool contracts (spec summaries) for the specified categories.

    Args:
        categories: Optional iterable of category strings ("read", "write", "exec").
            If None, all categories are included.

    Returns:
        A list of dicts, each containing name, category, aliases, and required_doc
        for one tool, sorted alphabetically by name.
    """
    if categories is None:
        allowed = {"read", "write", "exec"}
    else:
        allowed = {str(item).strip().lower() for item in categories if str(item or "").strip()}
    contracts: list[dict[str, Any]] = []
    for name in sorted(_TOOL_SPECS.keys()):
        spec = _TOOL_SPECS[name]
        category = str(spec.get("category") or "").strip().lower()
        if category not in allowed:
            continue
        contracts.append(
            {
                "name": name,
                "category": category,
                "aliases": list(spec.get("aliases", [])),
                "required_doc": str(spec.get("required_doc") or ""),
            }
        )
    return contracts


def render_tool_contract_for_prompt(
    *,
    include_write_tools: bool = False,
    include_exec_tools: bool = True,
) -> str:
    """Render the tool contract registry as a formatted string for LLM prompts.

    Args:
        include_write_tools: If True, include write-category tools in the output.
        include_exec_tools: If True (the default), include exec-category tools.

    Returns:
        A formatted plain-text string listing canonical tool names, their
        required arguments, and up to 4 aliases each.
    """
    categories = ["read"]
    if include_write_tools:
        categories.append("write")
    if include_exec_tools:
        categories.append("exec")
    contracts = list_tool_contracts(categories)
    lines: list[str] = []
    lines.append("Tool Contract (authoritative):")
    lines.append("- Prefer canonical tool names exactly as listed below.")
    lines.append("- Runtime accepts aliases, but planner output SHOULD use canonical names.")
    for item in contracts:
        aliases = item.get("aliases") or []
        alias_text = ", ".join(str(alias) for alias in aliases[:4]) if aliases else "none"
        if len(aliases) > 4:
            alias_text += ", ..."
        lines.append(f"- {item['name']}: {item['required_doc']}; aliases={alias_text}")
    return "\n".join(lines)
