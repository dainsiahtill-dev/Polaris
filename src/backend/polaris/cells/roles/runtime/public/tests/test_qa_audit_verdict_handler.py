"""Tests for QA audit verdict capability result projection."""

from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.qa.audit_verdict.public.contracts import QaAuditResultV1
from polaris.cells.roles.runtime.internal.capability.handlers.qa_audit_verdict_handler import (
    QaAuditVerdictHandler,
)
from polaris.cells.roles.runtime.public.contracts import RoleCapabilityDescriptor


def test_qa_audit_handler_projects_canonical_classification_metadata() -> None:
    capability = RoleCapabilityDescriptor(
        capability_id="issue_audit_verdict",
        owner_cell="qa.audit_verdict",
        contract_name="RunQaAuditCommandV1",
        effect="read",
        allowed_roles=("qa",),
        endpoint_ref="polaris.cells.qa.audit_verdict.public.contracts.RunQaAuditCommandV1",
    )
    command = SimpleNamespace(
        runtime_object=SimpleNamespace(
            identity=SimpleNamespace(role_id="qa"),
            capability_ports=SimpleNamespace(get=lambda _capability_id: capability),
        ),
        invocation=SimpleNamespace(invocation_id="invocation-1", capability_id="issue_audit_verdict"),
        payload={"evidence_paths": ("runtime/evidence/qa.jsonl",)},
    )
    raw = QaAuditResultV1(
        ok=False,
        task_id="task-1",
        workspace="/workspace",
        verdict="FAIL",
        findings=("missing target",),
        metadata={
            "failure_class": "INCOMPLETE_MATERIALIZATION",
            "responsible_layer": "director",
            "repairable_by_director": True,
            "qa_verdict_content_hash": "hash-1",
            "qa_verdict_envelope": {
                "schema_version": "qa.verdict_envelope.v1",
                "task_id": "task-1",
                "classification": {"failure_class": "INCOMPLETE_MATERIALIZATION"},
            },
        },
    )

    result = QaAuditVerdictHandler().map_result(raw, command)

    assert result.ok is False
    assert result.error_code == "qa_audit_rejected"
    assert result.metadata["failure_class"] == "INCOMPLETE_MATERIALIZATION"
    assert result.metadata["responsible_layer"] == "director"
    assert result.metadata["repairable_by_director"] is True
    assert result.metadata["qa_verdict_content_hash"] == "hash-1"
    assert result.metadata["qa_verdict_envelope"]["schema_version"] == "qa.verdict_envelope.v1"
