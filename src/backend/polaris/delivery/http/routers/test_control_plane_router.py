from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.cells.control_plane.run_ledger.public import AppendRunLedgerEventCommandV1, append_run_ledger_event
from polaris.delivery.http.error_handlers import setup_exception_handlers
from polaris.delivery.http.routers import control_plane
from polaris.delivery.http.routers._shared import require_auth


def _build_client(workspace: Path) -> TestClient:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(control_plane.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(settings=SimpleNamespace(workspace=str(workspace)))
    return TestClient(app)


def test_control_plane_run_provenance_bundle_endpoint_returns_bundle(tmp_path: Path) -> None:
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "qa_verifier",
                "gate": {"name": "qa_verifier", "ok": True, "summary": "qa passed"},
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "task_id": "TASK-1",
                    "project_id": "P1",
                    "contract_hash": "pm-hash",
                    "blueprint_hash": "ce-hash",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {
                    "final_request_context_audit": {
                        "schema_version": "llm.final_request_context_audit.v1",
                        "final_request_evidence_coverage": {
                            "schema_version": "polaris.final_request_evidence_coverage.v1",
                            "request_hash": "provider-request-hash",
                            "workflow_chain": {
                                "pm_contract_hash": "pm-hash",
                                "ce_blueprint_hash": "ce-hash",
                                "handoff_decision_hash": "handoff-hash",
                                "execution_profile_hash": "profile-hash",
                                "execution_envelope_hash": "envelope-hash",
                            },
                        },
                    }
                },
            },
        )
    )
    client = _build_client(tmp_path)

    response = client.get("/v2/control-plane/ledger/provenance", params={"run_id": "run-1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "polaris.run_provenance_bundle.v1"
    assert payload["run_id"] == "run-1"
    assert payload["pm_contract_hash"] == "pm-hash"
    assert payload["ce_blueprint_hash"] == "ce-hash"
    assert payload["handoff_decision_hash"] == "handoff-hash"
    assert payload["execution_envelope_hash"] == "envelope-hash"
    assert payload["final_provider_request_hashes"] == ["provider-request-hash"]


def test_control_plane_run_provenance_bundle_endpoint_requires_run_id(tmp_path: Path) -> None:
    client = _build_client(tmp_path)

    response = client.get("/v2/control-plane/ledger/provenance")

    assert response.status_code == 400
    assert "run_id must be a non-empty string" in response.text
