"""Authority helpers for enforcing the final provider-request tool surface.

The semantic turn may start with a broad tool set and later narrow the physical
provider request (for example, a quality-repair turn forced to ``edit_file``).
Only the latter is authoritative for the provider response and subsequent tool
execution.
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry


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
        names = _tool_names(list(getattr(prepared, "native_tool_schemas", None) or []))

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
    active_request: Any,
    prepared: Any,
) -> None:
    """Fail closed when a provider emits a tool outside its final request."""

    requested = _canonical_tool_name(tool_name)
    allowed = final_request_allowed_tool_names(active_request=active_request, prepared=prepared)
    if requested and requested in allowed:
        return
    rendered_allowed = ",".join(sorted(allowed)) if allowed else "<none>"
    raise RuntimeError(
        "provider_tool_surface_violation: "
        f"requested={requested or '<empty>'}; allowed={rendered_allowed}"
    )


__all__ = [
    "assert_tool_in_final_request_surface",
    "final_request_allowed_tool_names",
]
