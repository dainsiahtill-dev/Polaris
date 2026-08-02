"""Forced tool surface builder — registry-faithful only.

Polaris final-provider qualification compares every tool schema on the wire
against ``ToolSpecRegistry`` (``tool_registry_function_contract_drift`` and
related codes). Quality-repair, empty-write retry, and mutation-retry paths
historically invented or mutated schemas, which systematically blocked
Director turns that were required to finish true-run projects.

This module is the single builder for forced/narrowed tool *sets*:

- Schemas always come from ``ToolSpecRegistry.get_llm_schema``
- Optional path pinning is limited to the authorization already accepted by
  qualification (``write_file`` file parameter + aliases)
- Callers put mode guidance in prompts/SESSION_PATCH, never in tool descriptions

Design intent (aligned with Codex/Claude-style agent CLIs): one tool catalog,
immutable tool surface across repair modes; only messages and tool_choice change.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Final

_WRITE_FILE_PATH_PROPERTY_NAMES: Final[tuple[str, ...]] = (
    "file",
    "path",
    "filepath",
    "filePath",
    "file_path",
    "filename",
    "target",
    "target_file",
    "targetFile",
    "target_path",
    "targetPath",
)

_DEFAULT_INCLUDE_ARG_ALIASES: Final[bool] = True
_DEFAULT_DETERMINISTIC: Final[bool] = True


class ForcedToolSurfaceError(RuntimeError):
    """Raised when a forced tool surface cannot be built registry-faithfully."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code or "forced_tool_surface_error").strip()
        message = self.code if not detail else f"{self.code}: {detail}"
        super().__init__(message)


def resolve_registry_tool_schema(
    tool_name: str,
    *,
    include_arg_aliases: bool = _DEFAULT_INCLUDE_ARG_ALIASES,
    deterministic: bool = _DEFAULT_DETERMINISTIC,
) -> dict[str, Any]:
    """Return a deep-copied OpenAI-format schema from ToolSpecRegistry.

    Raises:
        ForcedToolSurfaceError: when the tool is unknown or registry lookup fails.
    """
    name = str(tool_name or "").strip()
    if not name:
        raise ForcedToolSurfaceError("forced_tool_name_missing")
    try:
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry
    except (ImportError, RuntimeError, ValueError) as exc:
        raise ForcedToolSurfaceError("tool_spec_registry_unavailable", str(exc)) from exc
    try:
        schema = ToolSpecRegistry.get_llm_schema(
            name,
            include_arg_aliases=include_arg_aliases,
            deterministic=deterministic,
        )
    except (RuntimeError, TypeError, ValueError) as exc:
        raise ForcedToolSurfaceError("tool_registry_lookup_failed", f"{name}: {exc}") from exc
    if not isinstance(schema, dict):
        raise ForcedToolSurfaceError("tool_registry_contract_missing", name)
    # Deep copy so callers can pin enums without mutating registry cache.
    return json.loads(json.dumps(schema, ensure_ascii=False))


def pin_write_file_paths(definition: Mapping[str, Any], target_files: Sequence[str]) -> dict[str, Any]:
    """Pin write_file path properties to declared targets (qualification-safe).

    Final-provider qualification allows scoped ``enum`` only on ``write_file``
    path properties (file + aliases with description ``(alias for file)``).
    Other tools must not receive path enums via this helper.
    """
    pinned = json.loads(json.dumps(dict(definition), ensure_ascii=False))
    function_payload = pinned.get("function")
    if not isinstance(function_payload, dict):
        raise ForcedToolSurfaceError("forced_tool_function_missing", "write_file")
    name = str(function_payload.get("name") or "").strip()
    if name != "write_file":
        raise ForcedToolSurfaceError(
            "forced_tool_path_pin_unauthorized",
            f"only write_file may pin path enums, got {name!r}",
        )
    enum_values = [str(path).strip() for path in target_files if str(path or "").strip()]
    enum_values = list(dict.fromkeys(enum_values[:32]))
    if not enum_values:
        return pinned
    parameters = function_payload.get("parameters")
    if not isinstance(parameters, dict):
        return pinned
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        return pinned
    for property_name in _WRITE_FILE_PATH_PROPERTY_NAMES:
        property_schema = properties.get(property_name)
        if isinstance(property_schema, dict):
            property_schema["enum"] = list(enum_values)
    return pinned


def build_forced_tool_surface(
    tool_names: Sequence[str],
    *,
    pin_write_paths: Sequence[str] | None = None,
    include_arg_aliases: bool = _DEFAULT_INCLUDE_ARG_ALIASES,
    deterministic: bool = _DEFAULT_DETERMINISTIC,
) -> list[dict[str, Any]]:
    """Build a forced tool list that is registry-faithful by construction.

    Args:
        tool_names: Canonical tool names in desired order (duplicates ignored).
        pin_write_paths: When set and ``write_file`` is included, pin its path
            properties to these declared targets (qualification-safe only).
        include_arg_aliases: Forwarded to registry schema generation.
        deterministic: Forwarded to registry schema generation.

    Returns:
        OpenAI-format tool definitions.

    Raises:
        ForcedToolSurfaceError: if any name is missing from the registry.
    """
    definitions: list[dict[str, Any]] = []
    seen: set[str] = set()
    paths = [str(path).strip() for path in (pin_write_paths or ()) if str(path or "").strip()]
    for raw_name in tool_names:
        name = str(raw_name or "").strip()
        if not name or name in seen:
            continue
        schema = resolve_registry_tool_schema(
            name,
            include_arg_aliases=include_arg_aliases,
            deterministic=deterministic,
        )
        function_payload = schema.get("function")
        canonical = ""
        if isinstance(function_payload, dict):
            canonical = str(function_payload.get("name") or name).strip()
        if not canonical:
            raise ForcedToolSurfaceError("forced_tool_function_name_missing", name)
        if canonical in seen:
            continue
        if canonical == "write_file" and paths:
            schema = pin_write_file_paths(schema, paths)
        definitions.append(schema)
        seen.add(canonical)
    if not definitions:
        raise ForcedToolSurfaceError("forced_tool_surface_empty")
    return definitions


def assert_registry_faithful_tool_surface(
    tools: Sequence[Mapping[str, Any]],
    *,
    allow_write_file_path_enum: bool = True,
) -> None:
    """Fail-closed check that forced tools match registry (minus write_file path enum).

    Intended for unit tests and optional pre-flight guards. Production
    qualification remains the authoritative gate; this prevents inventing
    schemas before they reach the wire.
    """
    for tool in tools:
        if not isinstance(tool, Mapping):
            raise ForcedToolSurfaceError("forced_tool_not_mapping")
        function_payload = tool.get("function")
        if not isinstance(function_payload, Mapping):
            raise ForcedToolSurfaceError("forced_tool_function_missing")
        name = str(function_payload.get("name") or "").strip()
        if not name:
            raise ForcedToolSurfaceError("forced_tool_function_name_missing")
        expected = resolve_registry_tool_schema(name)
        actual = json.loads(json.dumps(dict(tool), ensure_ascii=False))
        if allow_write_file_path_enum and name == "write_file":
            _strip_path_enums(expected)
            _strip_path_enums(actual)
        if actual != expected:
            raise ForcedToolSurfaceError(
                "forced_tool_registry_drift",
                name,
            )


def _strip_path_enums(definition: dict[str, Any]) -> None:
    function_payload = definition.get("function")
    if not isinstance(function_payload, dict):
        return
    parameters = function_payload.get("parameters")
    if not isinstance(parameters, dict):
        return
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        return
    for property_name in _WRITE_FILE_PATH_PROPERTY_NAMES:
        property_schema = properties.get(property_name)
        if isinstance(property_schema, dict):
            property_schema.pop("enum", None)


def tool_definition_name(definition: Mapping[str, Any] | None) -> str:
    """Extract canonical tool name from an OpenAI-format tool definition."""
    if not isinstance(definition, Mapping):
        return ""
    function_payload = definition.get("function")
    if isinstance(function_payload, Mapping):
        return str(function_payload.get("name") or "").strip()
    return str(definition.get("name") or "").strip()


__all__ = [
    "ForcedToolSurfaceError",
    "assert_registry_faithful_tool_surface",
    "build_forced_tool_surface",
    "pin_write_file_paths",
    "resolve_registry_tool_schema",
    "tool_definition_name",
]
