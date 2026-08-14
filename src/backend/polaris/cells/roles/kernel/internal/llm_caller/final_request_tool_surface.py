"""Authority helpers for enforcing the final provider-request tool surface.

The semantic turn may start with a broad tool set and later narrow the physical
provider request (for example, a quality-repair turn forced to ``edit_file``).
Only the latter is authoritative for the provider response and subsequent tool
execution.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

_PROVIDER_TOOL_SURFACE_VIOLATION_PREFIX = "provider_tool_surface_violation:"
_PROVIDER_TOOL_SURFACE_ERROR_WRAPPERS = (
    "LLM call failed: ",
    "single_batch_contract_violation_retry_failed: retry stream error: ",
)


def _canonical_tool_name(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    return str(ToolSpecRegistry.get_canonical(token) or token).strip().lower()


def _tool_names(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    names: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        raw_name = function.get("name") if isinstance(function, dict) else item.get("name")
        if canonical := _canonical_tool_name(raw_name):
            names.add(canonical)
    return names


def _forced_tool_name(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    function = value.get("function")
    if not isinstance(function, dict):
        return ""
    return _canonical_tool_name(function.get("name"))


def _tool_arguments(value: Any) -> dict[str, Any] | None:
    """Decode provider-native tool arguments without mutating the envelope."""

    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, Mapping):
        return None
    return {str(key): item for key, item in decoded.items()}


def _edit_file_invalid_reason(value: Any) -> str:
    """Return why an ``edit_file`` call cannot produce a physical mutation.

    This guard runs before DEO admission.  The low-level editor deliberately
    preserves empty/malformed edits as non-fatal no-ops so they cannot poison
    Run Ledger integrity.  A mutation-required LLM turn, however, must not
    accept such a no-op as a successful physical effect receipt.
    """

    arguments = _tool_arguments(value)
    if arguments is None:
        return "invalid_arguments"

    aliases = ToolSpecRegistry.get_all_specs().get("edit_file", {}).get("arg_aliases", {})
    if isinstance(aliases, Mapping):
        for raw_alias, raw_canonical in aliases.items():
            alias = str(raw_alias or "")
            canonical = str(raw_canonical or "")
            if canonical and canonical not in arguments and alias in arguments:
                arguments[canonical] = arguments[alias]

    blocks = arguments.get("blocks")
    if isinstance(blocks, str) and blocks.strip():
        return ""

    has_range = (
        isinstance(arguments.get("start_line"), int)
        and isinstance(arguments.get("end_line"), int)
        and "content" in arguments
    )
    if has_range:
        return ""

    search = arguments.get("search")
    if isinstance(search, str) and search:
        return "" if "replace" in arguments else "missing_replace"
    if "search" in arguments:
        return "empty_search"
    return "missing_edit_mode"


def final_request_allowed_tool_names(*, active_request: Any, prepared: Any) -> frozenset[str]:
    """Return the exact canonical tool authority of the physical request.

    ``active_request`` is the object actually dispatched to the provider.  The
    prepared semantic request is only a fallback for test doubles and older
    callers that do not expose an ``options`` mapping.
    """

    active_options = getattr(active_request, "options", None)
    options = active_options if isinstance(active_options, dict) else {}
    names = _tool_names(options.get("tools"))

    prepared_options = getattr(prepared, "request_options", None)
    fallback_options = prepared_options if isinstance(prepared_options, dict) else {}
    if not names and "tools" not in options:
        names = _tool_names(fallback_options.get("tools"))
    if not names and "tools" not in options and "tools" not in fallback_options:
        names = _tool_names(getattr(prepared, "native_tool_schemas", None))

    forced = _forced_tool_name(options.get("tool_choice")) or _forced_tool_name(
        fallback_options.get("tool_choice")
    )
    if forced:
        # A named provider tool choice is a stricter authority than merely
        # exposing several definitions.  A response using another tool must
        # never be executed.
        return frozenset({forced})
    return frozenset(names)


def assert_tool_in_final_request_surface(
    *,
    tool_name: Any,
    tool_arguments: Any = None,
    active_request: Any,
    prepared: Any,
) -> None:
    """Fail closed when a provider emits a tool outside its final request."""

    requested = _canonical_tool_name(tool_name)
    allowed = final_request_allowed_tool_names(active_request=active_request, prepared=prepared)
    if requested and requested in allowed:
        if requested == "edit_file" and tool_arguments is not None:
            invalid = _edit_file_invalid_reason(tool_arguments)
            if invalid:
                rendered_allowed = ",".join(sorted(allowed))
                raise RuntimeError(
                    f"{_PROVIDER_TOOL_SURFACE_VIOLATION_PREFIX} "
                    f"requested={requested}; allowed={rendered_allowed}; invalid={invalid}"
                )
        return
    rendered_allowed = ",".join(sorted(allowed)) if allowed else "<none>"
    raise RuntimeError(
        f"{_PROVIDER_TOOL_SURFACE_VIOLATION_PREFIX} "
        f"requested={requested or '<empty>'}; allowed={rendered_allowed}"
    )


def is_provider_tool_surface_violation(value: Any) -> bool:
    """Classify the fail-closed guard through known transport wrappers.

    The invoker converts a guarded physical-response failure into its public
    ``LLM call failed: ...`` envelope before DecisionCaller raises it back to
    the transaction controller.  Unwrap only those platform-owned prefixes;
    arbitrary text containing the marker must not acquire recovery authority.
    """

    rendered = str(value or "").strip()
    while rendered:
        if rendered.startswith(_PROVIDER_TOOL_SURFACE_VIOLATION_PREFIX):
            return True
        for wrapper in _PROVIDER_TOOL_SURFACE_ERROR_WRAPPERS:
            if rendered.startswith(wrapper):
                rendered = rendered[len(wrapper) :].lstrip()
                break
        else:
            return False
    return False


__all__ = [
    "assert_tool_in_final_request_surface",
    "final_request_allowed_tool_names",
    "is_provider_tool_surface_violation",
]
