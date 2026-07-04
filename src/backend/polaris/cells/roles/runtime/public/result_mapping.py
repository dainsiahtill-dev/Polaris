"""Contract result-mapping helpers for `roles.runtime` cell.

Lossless split: this module holds the pure helpers that translate
``RoleTurnResult`` runtime outputs into ``RoleExecutionResultV1`` contract
results and copy/patch their metadata. The bodies were moved verbatim from
``public/service.py`` and are re-exported there to preserve the public surface.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.cells.control_plane.run_ledger.public import (
    FailureClassV1,
    build_tool_dispatch_dropped_lifecycle_from_observed_calls,
    failure_evidence_from_lifecycle_receipt,
    is_failure_class,
    native_tool_call_envelope_refs_from_metadata,
    normalize_failure_class,
    project_tool_lifecycle_receipt_to_metadata,
    tool_call_lifecycle_receipts_from_metadata,
)
from polaris.cells.roles.profile.public.service import RoleTurnResult
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1


def _tool_name_from_call(item: Mapping[str, Any]) -> str:
    function = item.get("function")
    if isinstance(function, Mapping):
        name = str(function.get("name") or "").strip()
        if name:
            return name
    for key in ("name", "tool", "tool_name", "toolName", "function_name", "functionName"):
        name = str(item.get(key) or "").strip()
        if name:
            return name
    return ""


def _native_tool_call_envelopes(result: RoleTurnResult) -> tuple[Mapping[str, Any], ...]:
    metadata = result.metadata if isinstance(result.metadata, Mapping) else {}
    return native_tool_call_envelope_refs_from_metadata(metadata)


def _lifecycle_receipt_from_metadata(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    receipts = tool_call_lifecycle_receipts_from_metadata(metadata)
    if receipts:
        return dict(receipts[0])
    return None


def _lifecycle_failure_evidence_from_metadata(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    lifecycle = _lifecycle_receipt_from_metadata(metadata)
    if lifecycle is None:
        return None
    evidence = failure_evidence_from_lifecycle_receipt(lifecycle)
    return dict(evidence) if isinstance(evidence, Mapping) and evidence.get("failure_class") else None


def _extract_tool_calls(result: RoleTurnResult) -> tuple[str, ...]:
    names: list[str] = []
    for item in list(result.tool_calls or []):
        if not isinstance(item, Mapping):
            continue
        token = _tool_name_from_call(item)
        if token:
            names.append(token)
    if names:
        return tuple(names)
    for envelope in _native_tool_call_envelopes(result):
        token = str(envelope.get("tool_name") or "").strip()
        if token:
            names.append(token)
    return tuple(names)


def _has_tool_dispatch_evidence(result: RoleTurnResult) -> bool:
    if result.tool_results:
        return True
    receipt = result.batch_receipt
    if not isinstance(receipt, Mapping):
        return False
    for key in ("results", "raw_results", "effect_receipts"):
        value = receipt.get(key)
        if isinstance(value, list) and value:
            return True
    return False


def _tool_dispatch_dropped_error(result: RoleTurnResult) -> str:
    metadata = result.metadata if isinstance(result.metadata, Mapping) else {}
    lifecycle_failure = _lifecycle_failure_evidence_from_metadata(metadata)
    if lifecycle_failure is not None and is_failure_class(
        lifecycle_failure.get("failure_class"),
        FailureClassV1.TOOL_DISPATCH_DROPPED,
    ):
        return "tool_dispatch_dropped: required or native tool calls had no dispatch/effect receipt"
    tool_calls = _extract_tool_calls(result)
    native_envelopes = _native_tool_call_envelopes(result)
    if (not tool_calls and not native_envelopes) or _has_tool_dispatch_evidence(result):
        return ""
    return "tool_dispatch_dropped: native tool calls observed but no tool dispatch/effect receipt was committed"


def _tool_lifecycle_failure_error(result: RoleTurnResult) -> tuple[str, str]:
    metadata = result.metadata if isinstance(result.metadata, Mapping) else {}
    lifecycle_failure = _lifecycle_failure_evidence_from_metadata(metadata)
    if lifecycle_failure is None:
        return "", ""
    failure_class = normalize_failure_class(lifecycle_failure.get("failure_class"))
    if not failure_class or is_failure_class(failure_class, FailureClassV1.TOOL_DISPATCH_DROPPED):
        return "", ""
    error_code = str(failure_class).strip().lower()
    return error_code, f"{error_code}: tool lifecycle reported {failure_class}"


def _extract_artifacts(result: RoleTurnResult) -> tuple[str, ...]:
    payload = result.structured_output if isinstance(result.structured_output, dict) else {}
    values = payload.get("artifacts")
    if not isinstance(values, list):
        return ()
    return tuple(str(item).strip() for item in values if str(item).strip())


def _copy_result_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(metadata or {})


def _copy_tool_result_metadata(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    copied: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, Mapping):
            copied.append(dict(item))
    return copied


def _copy_batch_receipt_metadata(receipt: Any) -> dict[str, Any] | None:
    if isinstance(receipt, Mapping):
        return dict(receipt)
    model_dump = getattr(receipt, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    return None


def _copy_final_request_metadata_from_turn_events(items: Any) -> dict[str, Any]:
    if not isinstance(items, list):
        return {}
    copied: dict[str, Any] = {}
    for item in reversed(items):
        if not isinstance(item, Mapping):
            continue
        candidates = [item]
        metadata = item.get("metadata")
        if isinstance(metadata, Mapping):
            candidates.append(metadata)
        data = item.get("data")
        if isinstance(data, Mapping):
            candidates.append(data)
            data_metadata = data.get("metadata")
            if isinstance(data_metadata, Mapping):
                candidates.append(data_metadata)
        for candidate in candidates:
            final_audit = candidate.get("final_request_context_audit")
            if isinstance(final_audit, Mapping) and "final_request_context_audit" not in copied:
                copied["final_request_context_audit"] = dict(final_audit)
            context_os_audit = candidate.get("context_os_audit")
            if isinstance(context_os_audit, Mapping) and "context_os_audit" not in copied:
                copied["context_os_audit"] = dict(context_os_audit)
            context_ref = str(candidate.get("context_snapshot_ref") or "").strip()
            if context_ref and "context_snapshot_ref" not in copied:
                copied["context_snapshot_ref"] = context_ref
        if {
            "final_request_context_audit",
            "context_snapshot_ref",
        }.issubset(copied):
            break
    return copied


def _contract_result_metadata(result: RoleTurnResult) -> dict[str, Any]:
    metadata = _copy_result_metadata(result.metadata)
    if isinstance(result.structured_output, Mapping) and "structured_output" not in metadata:
        metadata["structured_output"] = dict(result.structured_output)
    tool_results = _copy_tool_result_metadata(result.tool_results)
    if tool_results and "tool_results" not in metadata:
        metadata["tool_results"] = tool_results
    batch_receipt = _copy_batch_receipt_metadata(result.batch_receipt)
    if batch_receipt and "batch_receipt" not in metadata:
        metadata["batch_receipt"] = batch_receipt
    event_metadata = _copy_final_request_metadata_from_turn_events(result.turn_events_metadata)
    for key, value in event_metadata.items():
        metadata.setdefault(key, value)
    lifecycle = _lifecycle_receipt_from_metadata(metadata)
    if lifecycle is not None:
        project_tool_lifecycle_receipt_to_metadata(metadata, lifecycle)
    dropped_error = _tool_dispatch_dropped_error(result)
    if dropped_error:
        dropped_tool_calls = _extract_tool_calls(result)
        native_envelopes = _native_tool_call_envelopes(result)
        dropped_lifecycle = build_tool_dispatch_dropped_lifecycle_from_observed_calls(
            tool_names=dropped_tool_calls,
            native_tool_call_envelopes=native_envelopes,
            run_id="",
            task_id="",
            turn_id="",
            role="",
            reason=dropped_error,
        )
        if lifecycle is None:
            project_tool_lifecycle_receipt_to_metadata(metadata, dropped_lifecycle)
    return metadata


def _with_result_metadata_patch(
    result: RoleExecutionResultV1,
    patch: Mapping[str, Any],
) -> RoleExecutionResultV1:
    metadata = _copy_result_metadata(result.metadata)
    metadata.update(dict(patch))
    return RoleExecutionResultV1(
        ok=result.ok,
        status=result.status,
        role=result.role,
        workspace=result.workspace,
        task_id=result.task_id,
        session_id=result.session_id,
        run_id=result.run_id,
        output=result.output,
        thinking=result.thinking,
        tool_calls=result.tool_calls,
        artifacts=result.artifacts,
        usage=result.usage,
        metadata=metadata,
        error_code=result.error_code,
        error_message=result.error_message,
        turn_history=list(result.turn_history),
    )


def _extract_turn_envelope_metadata(result: RoleExecutionResultV1) -> dict[str, Any]:
    metadata = _copy_result_metadata(result.metadata)
    envelope = metadata.get("turn_envelope")
    if isinstance(envelope, Mapping):
        return dict(envelope)
    turn_id = str(metadata.get("turn_id") or "").strip()
    if not turn_id:
        return {}
    return {
        "turn_id": turn_id,
        "session_id": str(result.session_id or "").strip() or None,
        "run_id": str(result.run_id or "").strip() or None,
        "role": str(result.role or "").strip() or None,
        "task_id": str(result.task_id or "").strip() or None,
    }


def _to_contract_result(
    *,
    role: str,
    workspace: str,
    task_id: str | None,
    session_id: str | None,
    run_id: str | None,
    result: RoleTurnResult,
) -> RoleExecutionResultV1:
    dispatch_error = _tool_dispatch_dropped_error(result)
    lifecycle_error_code, lifecycle_error = _tool_lifecycle_failure_error(result)
    error_message = str(result.error or result.tool_execution_error or dispatch_error or lifecycle_error or "").strip()
    ok = not bool(error_message)
    status = "ok" if ok else "failed"
    if not result.is_complete and ok:
        status = "in_progress"
    error_code = "tool_dispatch_dropped" if dispatch_error else (lifecycle_error_code or "role_runtime_error")
    return RoleExecutionResultV1(
        ok=ok,
        status=status,
        role=role,
        workspace=workspace,
        task_id=task_id,
        session_id=session_id,
        run_id=run_id,
        output=str(result.content or ""),
        thinking=result.thinking,
        tool_calls=_extract_tool_calls(result),
        artifacts=_extract_artifacts(result),
        usage=dict(result.execution_stats or {}),
        metadata=_contract_result_metadata(result),
        error_code=None if ok else error_code,
        error_message=None if ok else (error_message or "unknown runtime error"),
        turn_history=list(result.turn_history) if result.turn_history else [],
    )
