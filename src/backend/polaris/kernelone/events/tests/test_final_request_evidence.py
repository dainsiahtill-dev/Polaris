from __future__ import annotations

from polaris.kernelone.events.final_request_evidence import (
    FINAL_REQUEST_EVIDENCE_SCHEMA,
    attach_final_request_evidence,
    build_final_request_evidence,
)


def test_build_final_request_evidence_projects_audit_refs_without_prompt_content() -> None:
    audit = {
        "schema_version": "llm.final_request_context_audit.v1",
        "final_request_token_estimate": 8192,
        "context_window_utilization": 0.25,
        "context_underutilized": False,
        "coverage": {"has_pm_contract": True},
        "final_request_evidence_coverage": {
            "request_hash": "request-hash-1",
            "pass": False,
            "missing_required_refs": ["ce_blueprint"],
            "missing_required_tools": ["write_file"],
        },
    }

    evidence = build_final_request_evidence(
        {
            "metadata": {
                "context_snapshot_ref": "runtime/contexts/ab/cdef.json",
                "final_request_context_audit": audit,
                "messages": [{"role": "user", "content": "do not project this"}],
            }
        }
    )

    assert evidence["schema_version"] == FINAL_REQUEST_EVIDENCE_SCHEMA
    assert evidence["context_snapshot_ref"] == "runtime/contexts/ab/cdef.json"
    assert evidence["final_request_context_audit_present"] is True
    assert evidence["request_hash"] == "request-hash-1"
    assert evidence["final_request_evidence_coverage_pass"] is False
    assert evidence["missing_required_refs"] == ["ce_blueprint"]
    assert evidence["missing_required_tools"] == ["write_file"]
    assert "do not project this" not in str(evidence)


def test_attach_final_request_evidence_adds_stable_top_level_audit_refs() -> None:
    audit = {
        "schema_version": "llm.final_request_context_audit.v1",
        "final_request_evidence_coverage": {"request_hash": "request-hash-2", "pass": True},
    }
    payload: dict[str, object] = {"event": "llm_call_start"}

    evidence = attach_final_request_evidence(
        payload,
        {
            "context_snapshot_ref": "runtime/contexts/11/2222.json",
            "metadata": {"final_request_context_audit": audit},
        },
    )

    assert evidence["context_snapshot_ref"] == "runtime/contexts/11/2222.json"
    assert payload["context_snapshot_ref"] == "runtime/contexts/11/2222.json"
    assert payload["final_request_context_audit"] == audit
    assert isinstance(payload["final_request_evidence"], dict)
    assert isinstance(payload["audit_refs"], dict)
    assert payload["audit_refs"]["request_hash"] == "request-hash-2"


def test_build_final_request_evidence_preserves_existing_lightweight_projection() -> None:
    evidence = build_final_request_evidence(
        {
            "final_request_evidence": {
                "context_snapshot_ref": "runtime/contexts/44/5555.json",
                "final_request_context_audit_present": True,
                "final_request_context_audit_hash": "audit-hash-light",
                "final_request_evidence_coverage_pass": False,
                "request_hash": "request-hash-light",
                "missing_required_refs": ["ce_blueprint"],
                "missing_required_tools": ["repo_tree"],
            }
        }
    )

    assert evidence["context_snapshot_ref"] == "runtime/contexts/44/5555.json"
    assert evidence["final_request_context_audit_present"] is True
    assert evidence["final_request_context_audit_hash"] == "audit-hash-light"
    assert evidence["final_request_evidence_coverage_pass"] is False
    assert evidence["request_hash"] == "request-hash-light"
    assert evidence["missing_required_refs"] == ["ce_blueprint"]
    assert evidence["missing_required_tools"] == ["repo_tree"]
