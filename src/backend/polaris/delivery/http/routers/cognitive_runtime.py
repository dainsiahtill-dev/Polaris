from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from polaris.cells.factory.cognitive_runtime.public import (
    CognitiveRuntimePublicService,
    ExportHandoffPackCommandV1,
    GetHandoffPackQueryV1,
    GetRuntimeReceiptQueryV1,
    LeaseEditScopeCommandV1,
    MapDiffToCellsCommandV1,
    PromoteOrRejectCommandV1,
    RecordRollbackLedgerCommandV1,
    RecordRuntimeReceiptCommandV1,
    RehydrateHandoffPackCommandV1,
    RequestProjectionCompileCommandV1,
    ResolveContextCommandV1,
    ValidateChangeSetCommandV1,
    get_cognitive_runtime_public_service,
)

from ._shared import require_auth

router = APIRouter(
    prefix="/cognitive-runtime",
    tags=["cognitive-runtime"],
    dependencies=[Depends(require_auth)],
)


def _service_dependency() -> CognitiveRuntimePublicService:
    return get_cognitive_runtime_public_service()


def _serialize(payload: Any) -> Any:
    if payload is None:
        return None
    if is_dataclass(payload) and not isinstance(payload, type):
        return asdict(payload)  # type: ignore[arg-type]
    if isinstance(payload, dict):
        return dict(payload)
    if isinstance(payload, (list, tuple)):
        return list(payload)
    return payload


def _unwrap_result(result: Any, field_name: str) -> dict[str, Any]:
    if getattr(result, "ok", False):
        return {"ok": True, field_name: _serialize(getattr(result, field_name))}
    raise HTTPException(
        status_code=404 if "not_found" in str(getattr(result, "error_code", "")) else 400,
        detail={
            "ok": False,
            "error_code": getattr(result, "error_code", "cognitive_runtime_error"),
            "error_message": getattr(result, "error_message", "Cognitive Runtime request failed."),
        },
    )


@router.post("/resolve-context")
def resolve_context(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    command = ResolveContextCommandV1(
        workspace=str(payload.get("workspace") or ""),
        role=str(payload.get("role") or ""),
        query=str(payload.get("query") or ""),
        step=int(payload.get("step") or 0),
        run_id=str(payload.get("run_id") or ""),
        mode=str(payload.get("mode") or ""),
        session_id=str(payload["session_id"]) if payload.get("session_id") else None,
        events_path=str(payload.get("events_path") or ""),
        sources_enabled=tuple(str(item) for item in (payload.get("sources_enabled") or []) if str(item).strip()),
        policy=dict(payload.get("policy") or {}),
    )
    return _unwrap_result(service.resolve_context(command), "snapshot")


@router.post("/lease-edit-scope")
def lease_edit_scope(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    command = LeaseEditScopeCommandV1(
        workspace=str(payload.get("workspace") or ""),
        requested_by=str(payload.get("requested_by") or ""),
        scope_paths=tuple(str(item) for item in (payload.get("scope_paths") or []) if str(item).strip()),
        ttl_seconds=int(payload.get("ttl_seconds") or 1800),
        session_id=str(payload["session_id"]) if payload.get("session_id") else None,
        reason=str(payload.get("reason") or ""),
        metadata=dict(payload.get("metadata") or {}),
    )
    return _unwrap_result(service.lease_edit_scope(command), "lease")


@router.post("/validate-change-set")
def validate_change_set(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    command = ValidateChangeSetCommandV1(
        workspace=str(payload.get("workspace") or ""),
        changed_files=tuple(str(item) for item in (payload.get("changed_files") or []) if str(item).strip()),
        allowed_scope_paths=tuple(
            str(item) for item in (payload.get("allowed_scope_paths") or []) if str(item).strip()
        ),
        evidence_refs=tuple(str(item) for item in (payload.get("evidence_refs") or []) if str(item).strip()),
        require_change=bool(payload.get("require_change", True)),
    )
    result = service.validate_change_set(command)
    if result.validation is None:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error_code": result.error_code or "validate_change_set_failed",
                "error_message": result.error_message or "Change-set validation failed.",
            },
        )
    return {"ok": result.ok, "validation": _serialize(result.validation)}


@router.post("/runtime-receipts")
def record_runtime_receipt(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    command = RecordRuntimeReceiptCommandV1(
        workspace=str(payload.get("workspace") or ""),
        receipt_type=str(payload.get("receipt_type") or ""),
        payload=dict(payload.get("payload") or {}),
        session_id=str(payload["session_id"]) if payload.get("session_id") else None,
        run_id=str(payload["run_id"]) if payload.get("run_id") else None,
        trace_refs=tuple(str(item) for item in (payload.get("trace_refs") or []) if str(item).strip()),
        turn_envelope=dict(payload.get("turn_envelope") or {}),
    )
    return _unwrap_result(service.record_runtime_receipt(command), "receipt")


@router.get("/runtime-receipts/{receipt_id}")
def get_runtime_receipt(
    receipt_id: str,
    workspace: str,
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    return _unwrap_result(
        service.get_runtime_receipt(GetRuntimeReceiptQueryV1(workspace=workspace, receipt_id=receipt_id)),
        "receipt",
    )


@router.post("/handoffs/export")
def export_handoff_pack(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    command = ExportHandoffPackCommandV1(
        workspace=str(payload.get("workspace") or ""),
        session_id=str(payload.get("session_id") or ""),
        run_id=str(payload["run_id"]) if payload.get("run_id") else None,
        reason=str(payload.get("reason") or ""),
        receipt_limit=int(payload.get("receipt_limit") or 20),
        turn_envelope=dict(payload.get("turn_envelope") or {}),
    )
    return _unwrap_result(service.export_handoff_pack(command), "handoff")


@router.post("/handoffs/rehydrate")
def rehydrate_handoff_pack(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    command = RehydrateHandoffPackCommandV1(
        workspace=str(payload.get("workspace") or ""),
        handoff_id=str(payload.get("handoff_id") or ""),
        target_role=str(payload.get("target_role") or ""),
        target_session_id=(str(payload["target_session_id"]) if payload.get("target_session_id") else None),
    )
    return _unwrap_result(service.rehydrate_handoff_pack(command), "rehydration")


@router.post("/map-diff-to-cells")
def map_diff_to_cells(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    command = MapDiffToCellsCommandV1(
        workspace=str(payload.get("workspace") or ""),
        changed_files=tuple(str(item) for item in (payload.get("changed_files") or []) if str(item).strip()),
        graph_catalog_path=str(payload.get("graph_catalog_path") or "docs/graph/catalog/cells.yaml"),
    )
    return _unwrap_result(service.map_diff_to_cells(command), "mapping")


@router.post("/projection-compile")
def request_projection_compile(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    command = RequestProjectionCompileCommandV1(
        workspace=str(payload.get("workspace") or ""),
        requested_by=str(payload.get("requested_by") or ""),
        subject_ref=str(payload.get("subject_ref") or ""),
        changed_files=tuple(str(item) for item in (payload.get("changed_files") or []) if str(item).strip()),
        mapped_cells=tuple(str(item) for item in (payload.get("mapped_cells") or []) if str(item).strip()),
        session_id=str(payload["session_id"]) if payload.get("session_id") else None,
        run_id=str(payload["run_id"]) if payload.get("run_id") else None,
        metadata=dict(payload.get("metadata") or {}),
    )
    return _unwrap_result(service.request_projection_compile(command), "request")


@router.post("/promote-or-reject")
def promote_or_reject(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    command = PromoteOrRejectCommandV1(
        workspace=str(payload.get("workspace") or ""),
        subject_ref=str(payload.get("subject_ref") or ""),
        changed_files=tuple(str(item) for item in (payload.get("changed_files") or []) if str(item).strip()),
        mapped_cells=tuple(str(item) for item in (payload.get("mapped_cells") or []) if str(item).strip()),
        write_gate_allowed=bool(payload.get("write_gate_allowed", False)),
        projection_status=str(payload.get("projection_status") or ""),
        projection_request_id=(str(payload["projection_request_id"]) if payload.get("projection_request_id") else None),
        receipt_refs=tuple(str(item) for item in (payload.get("receipt_refs") or []) if str(item).strip()),
        reasons=tuple(str(item) for item in (payload.get("reasons") or []) if str(item).strip()),
        metadata=dict(payload.get("metadata") or {}),
    )
    result = service.promote_or_reject(command)
    if result.decision is None:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error_code": result.error_code or "promote_or_reject_failed",
                "error_message": result.error_message or "promote_or_reject failed.",
            },
        )
    return {"ok": result.ok, "decision": _serialize(result.decision)}


@router.post("/rollback-ledger")
def record_rollback_ledger(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    command = RecordRollbackLedgerCommandV1(
        workspace=str(payload.get("workspace") or ""),
        subject_ref=str(payload.get("subject_ref") or ""),
        reason=str(payload.get("reason") or ""),
        decision_id=str(payload["decision_id"]) if payload.get("decision_id") else None,
        changed_files=tuple(str(item) for item in (payload.get("changed_files") or []) if str(item).strip()),
        receipt_refs=tuple(str(item) for item in (payload.get("receipt_refs") or []) if str(item).strip()),
        metadata=dict(payload.get("metadata") or {}),
    )
    return _unwrap_result(service.record_rollback_ledger(command), "entry")


@router.get("/handoffs/{handoff_id}")
def get_handoff_pack(
    handoff_id: str,
    workspace: str,
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    return _unwrap_result(
        service.get_handoff_pack(GetHandoffPackQueryV1(workspace=workspace, handoff_id=handoff_id)),
        "handoff",
    )


__all__ = ["_service_dependency", "router"]
