"""Tool-dispatch failure projection for Role Kernel execution."""

from __future__ import annotations

from typing import Any

from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.roles.kernel.internal.kernel.task_boundary import append_director_task_boundary_verdict
from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest
from polaris.kernelone.audit.context_os_prompt import summarize_context_os_audit_from_ledger


def tool_schema_names_for_error_audit(tool_definitions: list[dict[str, Any]]) -> list[str]:
    """Return stable native tool names for degraded provider-request audit evidence."""
    names: list[str] = []
    for definition in tool_definitions:
        function_payload = definition.get("function") if isinstance(definition, dict) else None
        if not isinstance(function_payload, dict):
            continue
        name = str(function_payload.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def llm_metadata_from_ledger_on_error(
    ledger: Any,
    *,
    messages: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Project final-request audit evidence when TransactionKernel raises."""

    metadata: dict[str, Any] = {}
    calls = getattr(ledger, "llm_calls", None)
    if isinstance(calls, list):
        for call in reversed(calls):
            if not isinstance(call, dict):
                continue
            raw_metadata = call.get("metadata")
            if not isinstance(raw_metadata, dict):
                continue
            for key in (
                "final_request_context_audit",
                "context_snapshot_ref",
                "context_snapshot_degraded",
                "context_snapshot_degraded_reason",
                "context_tokens_after",
                "contextTokens",
                "usage",
                "usage_source",
            ):
                if key in raw_metadata and key not in metadata:
                    value = raw_metadata.get(key)
                    metadata[key] = dict(value) if isinstance(value, dict) else value
            if metadata:
                break
    context_os_audit_summary = summarize_context_os_audit_from_ledger(ledger)
    if context_os_audit_summary:
        metadata.setdefault("context_os_audit", context_os_audit_summary)
    anomaly_flags = getattr(ledger, "anomaly_flags", None)
    tool_dispatch_dropped_class = FailureClassV1.TOOL_DISPATCH_DROPPED.value
    if isinstance(anomaly_flags, list) and anomaly_flags:
        metadata["anomaly_flags"] = [dict(item) for item in anomaly_flags if isinstance(item, dict)]
        if any(str(item.get("type") or "") == tool_dispatch_dropped_class for item in metadata["anomaly_flags"]):
            metadata["tool_dispatch_dropped"] = True
    metadata["transaction_kernel_error_audit_available"] = bool(
        isinstance(metadata.get("final_request_context_audit"), dict)
        or str(metadata.get("context_snapshot_ref") or "").strip()
    )
    if not metadata["transaction_kernel_error_audit_available"]:
        metadata["provider_request_snapshot_degraded"] = True
        metadata["provider_request_snapshot_degraded_reason"] = "transaction_kernel_failed_without_llm_metadata"
        metadata["provider_request_assembly_projection"] = {
            "schema_version": "llm.provider_request_assembly_projection.v1",
            "source": "roles.kernel.transaction_error_path",
            "message_count": len(messages),
            "tool_schema_count": len(tool_definitions),
            "tool_names": tool_schema_names_for_error_audit(tool_definitions),
        }
    return metadata


def append_tool_dispatch_dropped_control_plane_events(
    *,
    role: str,
    profile: RoleProfile,
    request: RoleTurnRequest,
    workspace: str,
    turn_id: str,
    error_metadata: dict[str, Any],
    reason: str,
) -> None:
    """Commit dropped native tool-call facts to the control-plane ledger."""

    from polaris.cells.control_plane.run_ledger.public import (
        AppendRunLedgerEventCommandV1,
        append_run_ledger_event,
        build_tool_call_lifecycle_receipt,
    )

    native_count = 1
    decoded_count = 0
    dispatched_count = 0
    provider_response_hash = ""
    for flag in error_metadata.get("anomaly_flags", []):
        if isinstance(flag, dict) and str(flag.get("type") or "") == FailureClassV1.TOOL_DISPATCH_DROPPED.value:
            native_count = max(1, int(flag.get("native_tool_calls_count") or 1))
            decoded_count = max(0, int(flag.get("decoded_tool_calls_count") or 0))
            dispatched_count = max(0, int(flag.get("dispatched_tool_calls_count") or 0))
            provider_response_hash = str(flag.get("provider_response_hash") or "").strip()
            break
    lifecycle = build_tool_call_lifecycle_receipt(
        run_id=str(request.run_id or turn_id),
        task_id=str(request.task_id or ""),
        turn_id=turn_id,
        role=str(getattr(profile, "role_id", "") or role or ""),
        provider_response_hash=provider_response_hash,
        native_tool_calls_count=native_count,
        decoded_tool_calls_count=decoded_count,
        dispatched_tool_calls_count=dispatched_count,
        receipts=[],
        dispatch_status="dropped",
        failure_class=FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        reason=reason,
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=workspace,
            run_id=str(request.run_id or turn_id),
            event={
                "event_type": "tool_call_lifecycle",
                "stage": "director_tool_dispatch",
                "task_id": str(request.task_id or ""),
                "run_id": str(request.run_id or turn_id),
                "tool_call_lifecycle_receipt": lifecycle.to_dict(),
                "job_token": {
                    "run_id": str(request.run_id or turn_id),
                    "task_id": str(request.task_id or ""),
                    "project_id": str(request.task_id or "unknown"),
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
            },
        )
    )
    append_director_task_boundary_verdict(
        role=role,
        workspace=workspace,
        task_id=str(request.task_id or ""),
        run_id=str(request.run_id or turn_id),
        context_override=getattr(request, "context_override", None),
        tool_results=[],
        tool_dispatch={
            "status": "dropped",
            "dropped": True,
            "native_tool_calls_count": native_count,
            "provider_response_hash": provider_response_hash,
        },
        evidence_refs=[str(error_metadata.get("context_snapshot_ref") or "").strip()],
    )
