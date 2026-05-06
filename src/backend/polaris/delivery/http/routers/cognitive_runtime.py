from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter, Depends
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
from polaris.delivery.http.schemas.common import (
    CognitiveRuntimeActionResponse,
    CognitiveRuntimeDecisionResponse,
    CognitiveRuntimeValidationResponse,
    HandoffPackResponse,
    RuntimeReceiptResponse,
)

from ._shared import StructuredHTTPException, require_auth

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
        return asdict(payload)
    if isinstance(payload, dict):
        return dict(payload)
    if isinstance(payload, (list, tuple)):
        return list(payload)
    return payload


def _unwrap_result(result: Any, field_name: str) -> dict[str, Any]:
    if getattr(result, "ok", False):
        return {"ok": True, field_name: _serialize(getattr(result, field_name))}
    raise StructuredHTTPException(
        status_code=404 if "not_found" in str(getattr(result, "error_code", "")) else 400,
        code=getattr(result, "error_code", "RUNTIME_ERROR"),
        message=getattr(result, "error_message", "Cognitive Runtime request failed."),
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
        raise StructuredHTTPException(
            status_code=400,
            code=result.error_code or "VALIDATE_CHANGE_SET_FAILED",
            message=result.error_message or "Change-set validation failed.",
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
        raise StructuredHTTPException(
            status_code=400,
            code=result.error_code or "PROMOTE_OR_REJECT_FAILED",
            message=result.error_message or "promote_or_reject failed.",
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


# --- V2 namespace aliases (backward-compatible) ---


@router.post(
    "/v2/cognitive-runtime/resolve-context",
    dependencies=[Depends(require_auth)],
    response_model=CognitiveRuntimeActionResponse,
)
def v2_resolve_context(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    """Resolve runtime context for a role and query."""
    return resolve_context(payload, service)


@router.post(
    "/v2/cognitive-runtime/lease-edit-scope",
    dependencies=[Depends(require_auth)],
    response_model=CognitiveRuntimeActionResponse,
)
def v2_lease_edit_scope(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    """Lease an edit scope for safe file modifications."""
    return lease_edit_scope(payload, service)


@router.post(
    "/v2/cognitive-runtime/validate-change-set",
    dependencies=[Depends(require_auth)],
    response_model=CognitiveRuntimeValidationResponse,
)
def v2_validate_change_set(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    """Validate a change set against allowed scope and evidence."""
    return validate_change_set(payload, service)


@router.post(
    "/v2/cognitive-runtime/runtime-receipts",
    dependencies=[Depends(require_auth)],
    response_model=CognitiveRuntimeActionResponse,
)
def v2_record_runtime_receipt(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    """Record a runtime receipt for audit."""
    return record_runtime_receipt(payload, service)


@router.get(
    "/v2/cognitive-runtime/runtime-receipts/{receipt_id}",
    dependencies=[Depends(require_auth)],
    response_model=RuntimeReceiptResponse,
)
def v2_get_runtime_receipt(
    receipt_id: str,
    workspace: str,
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    """Retrieve a runtime receipt by ID."""
    return get_runtime_receipt(receipt_id, workspace, service)


@router.post(
    "/v2/cognitive-runtime/handoffs/export",
    dependencies=[Depends(require_auth)],
    response_model=CognitiveRuntimeActionResponse,
)
def v2_export_handoff_pack(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    """Export a handoff pack for cross-session continuity."""
    return export_handoff_pack(payload, service)


@router.post(
    "/v2/cognitive-runtime/handoffs/rehydrate",
    dependencies=[Depends(require_auth)],
    response_model=CognitiveRuntimeActionResponse,
)
def v2_rehydrate_handoff_pack(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    """Rehydrate a handoff pack into a target session."""
    return rehydrate_handoff_pack(payload, service)


@router.post(
    "/v2/cognitive-runtime/map-diff-to-cells",
    dependencies=[Depends(require_auth)],
    response_model=CognitiveRuntimeActionResponse,
)
def v2_map_diff_to_cells(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    """Map a file diff to affected cells in the graph catalog."""
    return map_diff_to_cells(payload, service)


@router.post(
    "/v2/cognitive-runtime/projection-compile",
    dependencies=[Depends(require_auth)],
    response_model=CognitiveRuntimeActionResponse,
)
def v2_request_projection_compile(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    """Request a projection compile for changed files and cells."""
    return request_projection_compile(payload, service)


@router.post(
    "/v2/cognitive-runtime/promote-or-reject",
    dependencies=[Depends(require_auth)],
    response_model=CognitiveRuntimeDecisionResponse,
)
def v2_promote_or_reject(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    """Promote or reject a change set based on gates and receipts."""
    return promote_or_reject(payload, service)


@router.post(
    "/v2/cognitive-runtime/rollback-ledger",
    dependencies=[Depends(require_auth)],
    response_model=CognitiveRuntimeActionResponse,
)
def v2_record_rollback_ledger(
    payload: dict[str, Any],
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    """Record a rollback ledger entry for audit."""
    return record_rollback_ledger(payload, service)


@router.get(
    "/v2/cognitive-runtime/handoffs/{handoff_id}",
    dependencies=[Depends(require_auth)],
    response_model=HandoffPackResponse,
)
def v2_get_handoff_pack(
    handoff_id: str,
    workspace: str,
    service: CognitiveRuntimePublicService = Depends(_service_dependency),
) -> dict[str, Any]:
    """Retrieve a handoff pack by ID."""
    return get_handoff_pack(handoff_id, workspace, service)


__all__ = ["_service_dependency", "router"]
