"""TransactionKernel tool-surface planning.

This module owns the role-turn tool schema reduction policy used before a
TransactionKernel turn is executed. It is deliberately pure with respect to I/O:
callers provide request/profile/context evidence and receive planned tool
definitions plus audit metadata. Projection feedback and RoleTurnResult mapping
stay in the application facade.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from polaris.cells.roles.kernel.internal.kernel.delivery_mode import (
    _context_requests_materialize_delivery,
    _text_requests_materialize_delivery,
)
from polaris.cells.roles.kernel.internal.kernel.request_tool_gating import (
    request_forces_no_transaction_tools,
    tool_contract_requires_no_tools,
)
from polaris.cells.roles.kernel.internal.kernel.tool_policy import _apply_runtime_tool_policy
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (
    build_native_tool_schemas,
    build_tool_filter_audit,
    ensure_director_first_call_materialization_scope,
    extract_declared_step_target_files,
    extract_write_tool_pin_target_files,
    pin_write_tool_file_param_to_targets,
    resolve_from_scratch_write_target,
    resolve_repair_edit_target,
    restrict_tool_definitions_to_edit,
    restrict_tool_definitions_to_write,
    should_use_weak_director_slim_tool_schema,
)
from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest

logger = logging.getLogger(__name__)

ToolSurfaceMode = Literal["turn", "stream"]


@dataclass(frozen=True, slots=True)
class TransactionToolSurfacePlan:
    """Planned tool schema and audit facts for one TransactionKernel turn.

    Attributes:
        tool_definitions: Provider-native tool schemas after all reductions.
        runtime_tool_policy_audit: Cognitive/ContextGateway reduction audit.
        tool_filter_audit: Prompt/tool-filter conflict audit, when a filter ran.
        conflict_error: Human-readable error when a prompt-required tool was removed.
    """

    tool_definitions: list[dict[str, Any]]
    runtime_tool_policy_audit: dict[str, Any]
    tool_filter_audit: dict[str, Any] | None = None
    conflict_error: str | None = None


def plan_transaction_tool_surface(
    *,
    role: str,
    profile: RoleProfile,
    request: RoleTurnRequest,
    context_result: Any,
    messages: list[dict[str, Any]],
    workspace: str,
    mode: ToolSurfaceMode,
) -> TransactionToolSurfacePlan:
    """Plan the native tool surface for a TransactionKernel turn.

    The planner can only reduce or pin the tool surface derived from the role
    profile. It never grants new tools. Complexity is linear in the number of
    tool schemas plus the prompt messages inspected by ``build_tool_filter_audit``.
    """

    context_override = getattr(request, "context_override", None)
    transaction_tools_disabled = tool_contract_requires_no_tools(request) or request_forces_no_transaction_tools(request)
    tool_definitions = [] if transaction_tools_disabled else build_native_tool_schemas(profile)
    tool_definitions, runtime_tool_policy_audit = _apply_runtime_tool_policy(
        request=request,
        context_result=context_result,
        tool_definitions=tool_definitions,
    )

    pin_targets = (
        extract_declared_step_target_files(context_override)
        if mode == "stream"
        else extract_write_tool_pin_target_files(context_override)
    )
    if pin_targets:
        tool_definitions = pin_write_tool_file_param_to_targets(tool_definitions, pin_targets)

    original_definitions: list[dict[str, Any]] | None = None
    filter_reason = ""
    from_scratch_target = resolve_from_scratch_write_target(context_override, workspace)
    if from_scratch_target:
        original_definitions = list(tool_definitions)
        filter_reason = "from_scratch_write_target"
        tool_definitions = restrict_tool_definitions_to_write(tool_definitions)
        tool_definitions = ensure_director_first_call_materialization_scope(
            role=role,
            context_override=context_override,
            workspace=workspace,
            tool_definitions=tool_definitions,
            from_scratch_target=from_scratch_target,
            materialize_requested=(
                _context_requests_materialize_delivery(context_override)
                or _text_requests_materialize_delivery(getattr(request, "message", None))
            ),
            transaction_tools_disabled=transaction_tools_disabled,
        )
        logger.info(
            "first-turn minimal execution schema for from-scratch leaf step: target=%s",
            from_scratch_target,
        )
    else:
        repair_target = resolve_repair_edit_target(context_override, workspace)
        if repair_target:
            original_definitions = list(tool_definitions)
            filter_reason = "repair_preserve_edit_target"
            tool_definitions = restrict_tool_definitions_to_edit(tool_definitions)
            logger.info("repair-turn edit-only for existing target: target=%s", repair_target)
        elif should_use_weak_director_slim_tool_schema(
            role=role,
            profile=profile,
            context_override=context_override,
            workspace=workspace,
            tool_definitions=tool_definitions,
        ):
            original_definitions = list(tool_definitions)
            filter_reason = "weak_director_slim_tool_schema"
            tool_definitions = restrict_tool_definitions_to_write(tool_definitions)
            logger.info(
                "weak-director slim tool schema enabled: role=%s model=%s",
                role,
                getattr(profile, "model", ""),
            )

    from polaris.cells.roles.kernel.internal.kernel.tool_policy import _apply_forced_transaction_tool_definitions

    tool_definitions = _apply_forced_transaction_tool_definitions(tool_definitions, context_override)

    tool_filter_audit: dict[str, Any] | None = None
    conflict_error: str | None = None
    if original_definitions is not None:
        tool_filter_audit = build_tool_filter_audit(
            filter_reason=filter_reason,
            original_tool_definitions=original_definitions,
            filtered_tool_definitions=tool_definitions,
            messages=messages,
        )
        if tool_filter_audit.get("status") == "conflict":
            removed_required = ", ".join(tool_filter_audit.get("removed_prompt_required_tool_names") or [])
            conflict_error = f"Tool schema filter conflict: removed prompt-required tools: {removed_required}"

    return TransactionToolSurfacePlan(
        tool_definitions=tool_definitions,
        runtime_tool_policy_audit=runtime_tool_policy_audit,
        tool_filter_audit=tool_filter_audit,
        conflict_error=conflict_error,
    )
