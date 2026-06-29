from __future__ import annotations

from polaris.kernelone.events.final_request_evidence import (
    FINAL_REQUEST_EVIDENCE_AUTHORITY_SCHEMA,
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
            "role_id": "director",
            "expected_role_id": "director",
            "role_identity_ok": True,
            "required_refs": ["pm_contract", "ce_blueprint", "target_files"],
            "included_refs": ["final_provider_request", "pm_contract", "target_files"],
            "missing_required_refs": ["ce_blueprint"],
            "required_tools": ["write_file", "read_file"],
            "available_tools": ["read_file"],
            "missing_required_tools": ["write_file"],
            "unexpected_tool_pruning": [
                {
                    "tool": "write_file",
                    "reason": "required_tool_missing_from_final_provider_request",
                }
            ],
            "tool_schema_registry_coverage": {"missing_schema_tools": ["write_file"]},
            "workflow_chain": {"pm_contract_hash": "pm-hash-1"},
            "coverage_ratio": 0.6,
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
    assert evidence["role_id"] == "director"
    assert evidence["expected_role_id"] == "director"
    assert evidence["role_identity_ok"] is True
    assert evidence["required_refs"] == ["pm_contract", "ce_blueprint", "target_files"]
    assert evidence["included_refs"] == ["final_provider_request", "pm_contract", "target_files"]
    assert evidence["missing_required_refs"] == ["ce_blueprint"]
    assert evidence["required_tools"] == ["write_file", "read_file"]
    assert evidence["available_tools"] == ["read_file"]
    assert evidence["missing_required_tools"] == ["write_file"]
    assert evidence["unexpected_tool_pruning"][0]["tool"] == "write_file"
    assert evidence["tool_schema_registry_coverage"] == {"missing_schema_tools": ["write_file"]}
    assert evidence["workflow_chain"] == {"pm_contract_hash": "pm-hash-1"}
    assert evidence["coverage_ratio"] == 0.6
    assert evidence["final_request_evidence_authority"]["schema_version"] == FINAL_REQUEST_EVIDENCE_AUTHORITY_SCHEMA
    assert evidence["final_request_evidence_authority"]["missing_required_refs"] == ["ce_blueprint"]
    assert evidence["final_request_evidence_authority_hash"]
    assert "do not project this" not in str(evidence)


def test_attach_final_request_evidence_adds_stable_top_level_audit_refs() -> None:
    audit = {
        "schema_version": "llm.final_request_context_audit.v1",
        "final_request_evidence_coverage": {
            "request_hash": "request-hash-2",
            "pass": True,
            "role_id": "chief_engineer",
            "expected_role_id": "chief_engineer",
            "role_identity_ok": True,
        },
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
    assert payload["audit_refs"]["final_request_evidence_authority_hash"]


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
                "final_request_evidence_authority": {
                    "schema_version": FINAL_REQUEST_EVIDENCE_AUTHORITY_SCHEMA,
                    "request_hash": "request-hash-light",
                    "role_id": "director",
                    "expected_role_id": "chief_engineer",
                    "role_identity_ok": False,
                    "required_refs": ["pm_contract", "ce_blueprint"],
                    "included_refs": ["pm_contract"],
                    "missing_required_refs": ["ce_blueprint"],
                    "required_tools": ["repo_tree"],
                    "available_tools": [],
                    "missing_required_tools": ["repo_tree"],
                    "final_request_evidence_authority_hash": "authority-hash-light",
                    "pass": False,
                },
            }
        }
    )

    assert evidence["context_snapshot_ref"] == "runtime/contexts/44/5555.json"
    assert evidence["final_request_context_audit_present"] is True
    assert evidence["final_request_context_audit_hash"] == "audit-hash-light"
    assert evidence["final_request_evidence_coverage_pass"] is False
    assert evidence["request_hash"] == "request-hash-light"
    assert evidence["role_id"] == "director"
    assert evidence["expected_role_id"] == "chief_engineer"
    assert evidence["role_identity_ok"] is False
    assert evidence["required_refs"] == ["pm_contract", "ce_blueprint"]
    assert evidence["included_refs"] == ["pm_contract"]
    assert evidence["missing_required_refs"] == ["ce_blueprint"]
    assert evidence["required_tools"] == ["repo_tree"]
    assert evidence["available_tools"] == []
    assert evidence["missing_required_tools"] == ["repo_tree"]
    assert evidence["final_request_evidence_authority_hash"]
