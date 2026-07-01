"""Request-level tool-gating predicates for RoleExecutionKernel.

Formal runtime gates are driven by platform metadata, not evaluator prompt
markers embedded in user-visible text. Internal evaluation harnesses may convert
their own case format into these metadata fields before invoking the role kernel.
"""

from __future__ import annotations

from polaris.cells.roles.profile.public.service import RoleTurnRequest

_NO_TOOL_KEYS: tuple[str, ...] = (
    "tool_contract_require_no_tool_calls",
    "require_no_tool_calls",
    "no_tool_calls",
)

_SINGLE_BATCH_KEYS: tuple[str, ...] = (
    "tool_contract_single_batch",
    "single_batch_execution",
    "single_batch",
)


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _truthy_key(mapping: dict[str, object], keys: tuple[str, ...]) -> bool:
    return any(bool(mapping.get(key)) for key in keys)


def _nested_tool_contract(mapping: dict[str, object]) -> dict[str, object]:
    return _mapping(mapping.get("tool_contract") or mapping.get("platform_tool_contract"))


def tool_contract_requires_no_tools(request: RoleTurnRequest) -> bool:
    """Return True when the platform tool contract forbids tool calls."""
    metadata = _mapping(getattr(request, "metadata", None))
    context = _mapping(getattr(request, "context_override", None))
    context_metadata = _mapping(context.get("metadata"))
    return (
        _truthy_key(metadata, _NO_TOOL_KEYS)
        or _truthy_key(context_metadata, _NO_TOOL_KEYS)
        or _truthy_key(_nested_tool_contract(metadata), _NO_TOOL_KEYS)
        or _truthy_key(_nested_tool_contract(context), _NO_TOOL_KEYS)
    )


def tool_contract_requires_single_batch(context_override: object) -> bool:
    """Return True when the platform tool contract pins a single-batch turn."""
    context = _mapping(context_override)
    context_metadata = _mapping(context.get("metadata"))
    contract = _nested_tool_contract(context)
    metadata_contract = _nested_tool_contract(context_metadata)
    execution_mode = str(contract.get("execution_mode") or metadata_contract.get("execution_mode") or "").strip()
    return (
        _truthy_key(context, _SINGLE_BATCH_KEYS)
        or _truthy_key(context_metadata, _SINGLE_BATCH_KEYS)
        or _truthy_key(contract, _SINGLE_BATCH_KEYS)
        or _truthy_key(metadata_contract, _SINGLE_BATCH_KEYS)
        or execution_mode in {"single_batch", "single-batch"}
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
