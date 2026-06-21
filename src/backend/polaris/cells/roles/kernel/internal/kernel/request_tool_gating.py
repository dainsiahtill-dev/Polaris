"""Request-level tool-gating predicates for RoleExecutionKernel.

Holds the bodies of ``RoleExecutionKernel._benchmark_requires_no_tools`` and
``RoleExecutionKernel._request_forces_no_transaction_tools`` extracted verbatim
(behavior-preserving) into free functions. The class methods become thin
delegating shims.

FROZEN behavior notes (do NOT change):
- ``benchmark_requires_no_tools`` preserves the metadata flag check, the
  ``[Benchmark Tool Contract]`` marker gate, and the exact lowered-substring
  matches verbatim.
- ``request_forces_no_transaction_tools`` preserves the context-override
  short-circuits (``disable_internal_tool_rounds``, ``propose_patch``
  delivery-mode, the empty forced-definitions + ``none`` forced-choice case) and
  the ``[mode:propose]`` + "do not call tools" message gate verbatim.
- Both are pure functions of the request; they take no kernel reference. They
  remain reachable through the bound ``kernel._<name>`` shims (called from
  ``turn_execution``).
"""

from __future__ import annotations

from polaris.cells.roles.profile.public.service import RoleTurnRequest


def benchmark_requires_no_tools(request: RoleTurnRequest) -> bool:
    """Return True when benchmark contract explicitly forbids tool calls."""
    metadata = dict(getattr(request, "metadata", {}) or {})
    if bool(metadata.get("benchmark_require_no_tool_calls")):
        return True

    message = str(getattr(request, "message", "") or "")
    if "[Benchmark Tool Contract]" not in message:
        return False

    lowered = message.lower()
    return (
        "do not call any tools for this case." in lowered
        or "do not call any tools" in lowered
        or 'require_no_tool_calls": true' in lowered
        or "require_no_tool_calls: true" in lowered
    )


def request_forces_no_transaction_tools(request: RoleTurnRequest) -> bool:
    """Return True when this turn must be handled as text-only output."""
    context_override = getattr(request, "context_override", None)
    context = context_override if isinstance(context_override, dict) else {}
    if bool(context.get("disable_internal_tool_rounds")):
        return True
    if str(context.get("delivery_mode") or "").strip().lower() == "propose_patch":
        return True
    forced_defs = context.get("_transaction_kernel_forced_tool_definitions")
    forced_choice = str(context.get("_transaction_kernel_forced_tool_choice") or "").strip().lower()
    if isinstance(forced_defs, list) and not forced_defs and forced_choice == "none":
        return True

    message = str(getattr(request, "message", "") or "").lower()
    return "[mode:propose]" in message and "do not call tools" in message
