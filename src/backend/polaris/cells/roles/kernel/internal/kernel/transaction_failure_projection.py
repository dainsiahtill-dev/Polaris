"""TransactionKernel failure projection helpers.

This module owns deterministic projection of adapter-level failures around a
TransactionKernel invocation. It records ContextOS projection feedback, appends
tool-dispatch-dropped evidence, and maps failures to RoleTurnResult or stream
event shapes without executing tools or invoking providers.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from polaris.cells.roles.kernel.internal.kernel.role_result_projection import role_turn_error_result
from polaris.cells.roles.kernel.internal.kernel.tool_dispatch_projection import (
    append_tool_dispatch_dropped_control_plane_events,
    llm_metadata_from_ledger_on_error,
)
from polaris.cells.roles.kernel.internal.transaction.tool_surface import TransactionToolSurfacePlan
from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest, RoleTurnResult
from polaris.kernelone.events.uep_publisher import UEPEventPublisher

logger = logging.getLogger(__name__)


def build_tool_filter_conflict_result(
    *,
    profile: RoleProfile,
    fingerprint: Any,
    tool_surface: TransactionToolSurfacePlan,
    context_gateway: Any,
    context_result: Any,
) -> RoleTurnResult:
    """Build a non-streaming RoleTurnResult for a tool-filter conflict."""

    record_projection_failure(
        context_gateway=context_gateway,
        context_result=context_result,
        reason="tool-filter conflict",
    )
    metadata = _profile_error_metadata(
        profile=profile,
        base={"tool_filter_audit": tool_surface.tool_filter_audit or {}},
    )
    return role_turn_error_result(
        error=tool_surface.conflict_error or "tool filter conflict",
        execution_stats={
            "duration_ms": 0,
            "llm_calls": 0,
            "tool_calls": 0,
            "transaction_kernel": True,
            "tool_filter_blocked": True,
            "tool_filter_status": "conflict",
            **tool_surface.runtime_tool_policy_audit,
        },
        metadata=metadata,
        profile=profile,
        fingerprint=fingerprint,
    )


async def publish_tool_filter_conflict_stream_event(
    *,
    workspace: str,
    role: str,
    stream_run_id: str,
    turn_id: str,
    tool_surface: TransactionToolSurfacePlan,
    context_gateway: Any,
    context_result: Any,
    uep_publisher: UEPEventPublisher,
) -> dict[str, Any]:
    """Publish and return a stream error event for a tool-filter conflict."""

    record_projection_failure(
        context_gateway=context_gateway,
        context_result=context_result,
        reason="stream tool-filter conflict",
    )
    error_event: dict[str, Any] = {
        "type": "error",
        "error": tool_surface.conflict_error,
        "error_type": "tool_schema_filter_conflict",
        "turn_id": turn_id,
        "metadata": {"tool_filter_audit": tool_surface.tool_filter_audit or {}},
    }
    await uep_publisher.publish_stream_event(
        workspace=workspace or os.getcwd(),
        run_id=stream_run_id,
        role=role,
        event_type="error",
        payload=error_event,
    )
    return error_event


def build_transaction_execution_error_result(
    *,
    exc: Exception,
    role: str,
    profile: RoleProfile,
    request: RoleTurnRequest,
    fingerprint: Any,
    turn_id: str,
    messages: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
    tool_filter_audit: dict[str, Any] | None,
    context_gateway: Any,
    context_result: Any,
    workspace: str,
) -> RoleTurnResult:
    """Build a non-streaming RoleTurnResult for a TransactionKernel exception."""

    metadata = build_transaction_exception_metadata(
        exc=exc,
        role=role,
        profile=profile,
        request=request,
        turn_id=turn_id,
        messages=messages,
        tool_definitions=tool_definitions,
        tool_filter_audit=tool_filter_audit,
        context_gateway=context_gateway,
        context_result=context_result,
        workspace=workspace,
        projection_reason="TransactionKernel error",
        dropped_ledger_reason="failed to append tool_dispatch_dropped ledger event",
    )
    return role_turn_error_result(
        error=f"TransactionKernel execution failed: {exc}",
        metadata=metadata,
        profile=profile,
        fingerprint=fingerprint,
    )


def build_transaction_exception_metadata(
    *,
    exc: Exception,
    role: str,
    profile: RoleProfile,
    request: RoleTurnRequest,
    turn_id: str,
    messages: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
    tool_filter_audit: dict[str, Any] | None,
    context_gateway: Any,
    context_result: Any,
    workspace: str,
    projection_reason: str,
    dropped_ledger_reason: str,
) -> dict[str, Any]:
    """Record projection failure and build metadata for a TransactionKernel exception."""

    record_projection_failure(
        context_gateway=context_gateway,
        context_result=context_result,
        reason=projection_reason,
    )
    metadata = llm_metadata_from_ledger_on_error(
        getattr(exc, "turn_ledger", None),
        messages=messages,
        tool_definitions=tool_definitions,
    )
    if bool(metadata.get("tool_dispatch_dropped")):
        try:
            append_tool_dispatch_dropped_control_plane_events(
                role=role,
                workspace=workspace,
                profile=profile,
                request=request,
                turn_id=turn_id,
                error_metadata=metadata,
                reason=str(exc),
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.debug(dropped_ledger_reason, exc_info=True)
    metadata = _profile_error_metadata(profile=profile, base=metadata)
    if tool_filter_audit is not None:
        metadata["tool_filter_audit"] = tool_filter_audit
    return metadata


def record_projection_failure(
    *,
    context_gateway: Any,
    context_result: Any,
    reason: str,
) -> None:
    """Record failed projection feedback without letting telemetry break execution."""

    try:
        context_gateway.record_projection_outcome(
            success=False,
            tokens_used=int(getattr(context_result, "token_estimate", 0) or 0),
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        logger.debug("Projection outcome feedback failed after %s", reason, exc_info=True)


def _profile_error_metadata(*, profile: RoleProfile, base: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(base)
    if profile.provider_id:
        metadata["provider_id"] = str(profile.provider_id).strip()
    if profile.model:
        metadata["model"] = str(profile.model).strip()
    return metadata
