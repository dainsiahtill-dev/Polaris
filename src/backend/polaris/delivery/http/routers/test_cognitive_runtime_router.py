from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.delivery.http.routers import cognitive_runtime
from polaris.domain.cognitive_runtime import (
    ContextHandoffPack,
    ContextSnapshot,
    DiffCellMapping,
    EditScopeLease,
    HandoffRehydration,
    ProjectionCompileRequest,
    PromotionDecisionRecord,
    RollbackLedgerEntry,
    RuntimeReceipt,
)


class _FakeService:
    def resolve_context(self, command):
        return type(
            "Result",
            (),
            {
                "ok": True,
                "snapshot": ContextSnapshot(
                    workspace=command.workspace,
                    role=command.role,
                    query=command.query,
                    run_id=command.run_id,
                    step=command.step,
                    mode=command.mode,
                    rendered_prompt="resolved prompt",
                ),
            },
        )()

    def lease_edit_scope(self, command):
        return type(
            "Result",
            (),
            {
                "ok": True,
                "lease": EditScopeLease(
                    lease_id="lease-1",
                    workspace=command.workspace,
                    requested_by=command.requested_by,
                    scope_paths=command.scope_paths,
                    issued_at="2026-03-26T00:00:00+00:00",
                    expires_at="2026-03-26T00:30:00+00:00",
                ),
            },
        )()

    def validate_change_set(self, command):
        return type(
            "Result",
            (),
            {
                "ok": True,
                "validation": {
                    "workspace": command.workspace,
                    "changed_files": list(command.changed_files),
                    "allowed_scope_paths": list(command.allowed_scope_paths),
                },
            },
        )()

    def record_runtime_receipt(self, command):
        return type(
            "Result",
            (),
            {
                "ok": True,
                "receipt": RuntimeReceipt(
                    receipt_id="receipt-1",
                    receipt_type=command.receipt_type,
                    workspace=command.workspace,
                    created_at="2026-03-26T00:00:00+00:00",
                    payload=dict(command.payload),
                    session_id=command.session_id,
                    turn_envelope=None,
                ),
            },
        )()

    def get_runtime_receipt(self, query):
        return type(
            "Result",
            (),
            {
                "ok": True,
                "receipt": RuntimeReceipt(
                    receipt_id=query.receipt_id,
                    receipt_type="scope_lease",
                    workspace=query.workspace,
                    created_at="2026-03-26T00:00:00+00:00",
                    payload={"ok": True},
                ),
            },
        )()

    def export_handoff_pack(self, command):
        return type(
            "Result",
            (),
            {
                "ok": True,
                "handoff": ContextHandoffPack(
                    handoff_id="handoff-1",
                    workspace=command.workspace,
                    created_at="2026-03-26T00:00:00+00:00",
                    session_id=command.session_id,
                ),
            },
        )()

    def map_diff_to_cells(self, command):
        return type(
            "Result",
            (),
            {
                "ok": True,
                "mapping": DiffCellMapping(
                    mapping_id="map-1",
                    workspace=command.workspace,
                    created_at="2026-03-26T00:00:00+00:00",
                    graph_catalog_path=command.graph_catalog_path,
                    changed_files=tuple(command.changed_files),
                    matched_cells=("roles.runtime",),
                    unmapped_files=(),
                    file_to_cells={str(command.changed_files[0]): ("roles.runtime",)},
                ),
            },
        )()

    def request_projection_compile(self, command):
        return type(
            "Result",
            (),
            {
                "ok": True,
                "request": ProjectionCompileRequest(
                    request_id="compile-1",
                    workspace=command.workspace,
                    created_at="2026-03-26T00:00:00+00:00",
                    requested_by=command.requested_by,
                    subject_ref=command.subject_ref,
                    status="queued",
                    changed_files=tuple(command.changed_files),
                    mapped_cells=tuple(command.mapped_cells),
                ),
            },
        )()

    def promote_or_reject(self, command):
        return type(
            "Result",
            (),
            {
                "ok": bool(command.write_gate_allowed),
                "decision": PromotionDecisionRecord(
                    decision_id="decision-1",
                    workspace=command.workspace,
                    created_at="2026-03-26T00:00:00+00:00",
                    subject_ref=command.subject_ref,
                    decision="promote" if command.write_gate_allowed else "reject",
                    reasons=tuple(command.reasons),
                    mapped_cells=tuple(command.mapped_cells),
                    changed_files=tuple(command.changed_files),
                ),
            },
        )()

    def record_rollback_ledger(self, command):
        return type(
            "Result",
            (),
            {
                "ok": True,
                "entry": RollbackLedgerEntry(
                    rollback_id="rollback-1",
                    workspace=command.workspace,
                    created_at="2026-03-26T00:00:00+00:00",
                    subject_ref=command.subject_ref,
                    reason=command.reason,
                    decision_id=command.decision_id,
                    changed_files=tuple(command.changed_files),
                    receipt_refs=tuple(command.receipt_refs),
                ),
            },
        )()

    def get_handoff_pack(self, query):
        return type(
            "Result",
            (),
            {
                "ok": True,
                "handoff": ContextHandoffPack(
                    handoff_id=query.handoff_id,
                    workspace=query.workspace,
                    created_at="2026-03-26T00:00:00+00:00",
                    session_id="session-1",
                ),
            },
        )()

    def rehydrate_handoff_pack(self, command):
        return type(
            "Result",
            (),
            {
                "ok": True,
                "rehydration": HandoffRehydration(
                    rehydration_id="rehydration-1",
                    handoff_id=command.handoff_id,
                    workspace=command.workspace,
                    created_at="2026-03-26T00:00:00+00:00",
                    target_role=command.target_role,
                    target_session_id=command.target_session_id,
                    current_goal="stabilize context runtime",
                    run_card={"current_goal": "stabilize context runtime"},
                    decision_log=({"decision_id": "decision-1", "summary": "prefer state-first continuity"},),
                    source_spans=("ep-1:t41:t44",),
                    context_override={
                        "state_first_context_os": {
                            "mode": "state_first_context_os.handoff_rehydrate",
                            "run_card": {"current_goal": "stabilize context runtime"},
                        }
                    },
                    metadata_patch={"handoff_rehydrated": True},
                ),
            },
        )()


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(cognitive_runtime.router)
    app.dependency_overrides[cognitive_runtime._service_dependency] = _FakeService
    app.dependency_overrides[cognitive_runtime.require_auth] = lambda: None
    return TestClient(app)


def test_router_resolve_context_and_receipt_round_trip() -> None:
    client = _build_client()
    response = client.post(
        "/cognitive-runtime/resolve-context",
        json={
            "workspace": "C:/workspace",
            "role": "director",
            "query": "summarize",
            "step": 1,
            "run_id": "run-1",
            "mode": "chat",
        },
    )
    assert response.status_code == 200
    assert response.json()["snapshot"]["rendered_prompt"] == "resolved prompt"

    receipt_response = client.post(
        "/cognitive-runtime/runtime-receipts",
        json={
            "workspace": "C:/workspace",
            "receipt_type": "scope_lease",
            "payload": {"ok": True},
        },
    )
    assert receipt_response.status_code == 200
    assert receipt_response.json()["receipt"]["receipt_id"] == "receipt-1"

    get_response = client.get(
        "/cognitive-runtime/runtime-receipts/receipt-1",
        params={"workspace": "C:/workspace"},
    )
    assert get_response.status_code == 200
    assert get_response.json()["receipt"]["payload"]["ok"] is True


def test_router_handoff_export_and_fetch() -> None:
    client = _build_client()
    response = client.post(
        "/cognitive-runtime/handoffs/export",
        json={
            "workspace": "C:/workspace",
            "session_id": "session-1",
        },
    )
    assert response.status_code == 200
    assert response.json()["handoff"]["handoff_id"] == "handoff-1"

    get_response = client.get(
        "/cognitive-runtime/handoffs/handoff-1",
        params={"workspace": "C:/workspace"},
    )
    assert get_response.status_code == 200
    assert get_response.json()["handoff"]["session_id"] == "session-1"

    rehydrate_response = client.post(
        "/cognitive-runtime/handoffs/rehydrate",
        json={
            "workspace": "C:/workspace",
            "handoff_id": "handoff-1",
            "target_role": "writer",
            "target_session_id": "session-writer-1",
        },
    )
    assert rehydrate_response.status_code == 200
    assert rehydrate_response.json()["rehydration"]["rehydration_id"] == "rehydration-1"
    assert (
        rehydrate_response.json()["rehydration"]["context_override"]["state_first_context_os"]["mode"]
        == "state_first_context_os.handoff_rehydrate"
    )


def test_router_phase2_endpoints() -> None:
    client = _build_client()

    mapping_response = client.post(
        "/cognitive-runtime/map-diff-to-cells",
        json={
            "workspace": "C:/workspace",
            "changed_files": ["polaris/cells/roles/runtime/public/service.py"],
        },
    )
    assert mapping_response.status_code == 200
    assert mapping_response.json()["mapping"]["mapping_id"] == "map-1"

    compile_response = client.post(
        "/cognitive-runtime/projection-compile",
        json={
            "workspace": "C:/workspace",
            "requested_by": "director",
            "subject_ref": "task-1",
            "changed_files": ["polaris/cells/roles/runtime/public/service.py"],
            "mapped_cells": ["roles.runtime"],
        },
    )
    assert compile_response.status_code == 200
    assert compile_response.json()["request"]["request_id"] == "compile-1"

    decision_response = client.post(
        "/cognitive-runtime/promote-or-reject",
        json={
            "workspace": "C:/workspace",
            "subject_ref": "task-1",
            "changed_files": ["polaris/cells/roles/runtime/public/service.py"],
            "mapped_cells": ["roles.runtime"],
            "write_gate_allowed": True,
            "projection_status": "queued",
            "reasons": ["all_checks_passed"],
        },
    )
    assert decision_response.status_code == 200
    assert decision_response.json()["ok"] is True
    assert decision_response.json()["decision"]["decision_id"] == "decision-1"

    rollback_response = client.post(
        "/cognitive-runtime/rollback-ledger",
        json={
            "workspace": "C:/workspace",
            "subject_ref": "task-1",
            "reason": "operator_requested",
            "decision_id": "decision-1",
            "changed_files": ["polaris/cells/roles/runtime/public/service.py"],
            "receipt_refs": ["receipt-1"],
        },
    )
    assert rollback_response.status_code == 200
    assert rollback_response.json()["entry"]["rollback_id"] == "rollback-1"
