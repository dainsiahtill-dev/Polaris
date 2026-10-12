"""QA lineage metadata must not erase a committed execution capability."""

from __future__ import annotations

import pytest
from polaris.cells.control_plane.run_ledger.public import build_run_ledger_projection


@pytest.mark.parametrize("scope_issue", [False, True])
def test_qa_scope_metadata_preserves_real_token_binding_and_scope_failures(scope_issue: bool) -> None:
    projection = build_run_ledger_projection(
        [
            {
                "event_type": "gate_evaluated",
                "stage": "real_run_gate",
                "gate": {"name": "workspace_validation", "ok": True},
                "job_token": {
                    "token_id": "committed-token",
                    "contract_hash": "c" * 64,
                    "blueprint_hash": "b" * 64,
                    "stage": "real_run_gate",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                },
            },
            {
                "event_type": "gate_evaluated",
                "stage": "qa",
                "gate": {"name": "qa_role_evidence", "ok": True},
                "job_token": {
                    "run_id": "director-child",
                    "task_id": "TASK-QA",
                    "project_id": "P1",
                    "capability_audit": {"ok": not scope_issue, "issues": ["foreign_scope"] if scope_issue else []},
                },
            },
        ]
    )
    capability = projection["capability"]
    assert capability["latest_token_id"] == "committed-token"
    assert capability["latest_contract_hash"] == "c" * 64
    assert capability["latest_blueprint_hash"] == "b" * 64
    assert capability["job_token_ids"] == ["committed-token"]
    assert capability["ok"] is not scope_issue
