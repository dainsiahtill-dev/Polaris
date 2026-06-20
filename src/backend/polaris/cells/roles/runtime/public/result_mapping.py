"""Contract result-mapping helpers for `roles.runtime` cell.

Lossless split: this module holds the pure helpers that translate
``RoleTurnResult`` runtime outputs into ``RoleExecutionResultV1`` contract
results and copy/patch their metadata. The bodies were moved verbatim from
``public/service.py`` and are re-exported there to preserve the public surface.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.cells.roles.profile.public.service import RoleTurnResult
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1


def _extract_tool_calls(result: RoleTurnResult) -> tuple[str, ...]:
    names: list[str] = []
    for item in list(result.tool_calls or []):
        if not isinstance(item, dict):
            continue
        token = str(item.get("name") or item.get("tool") or "").strip()
        if token:
            names.append(token)
    return tuple(names)


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


def _contract_result_metadata(result: RoleTurnResult) -> dict[str, Any]:
    metadata = _copy_result_metadata(result.metadata)
    tool_results = _copy_tool_result_metadata(result.tool_results)
    if tool_results and "tool_results" not in metadata:
        metadata["tool_results"] = tool_results
    batch_receipt = _copy_batch_receipt_metadata(result.batch_receipt)
    if batch_receipt and "batch_receipt" not in metadata:
        metadata["batch_receipt"] = batch_receipt
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
    error_message = str(result.error or result.tool_execution_error or "").strip()
    ok = not bool(error_message)
    status = "ok" if ok else "failed"
    if not result.is_complete and ok:
        status = "in_progress"
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
        error_code=None if ok else "role_runtime_error",
        error_message=None if ok else (error_message or "unknown runtime error"),
        turn_history=list(result.turn_history) if result.turn_history else [],
    )
