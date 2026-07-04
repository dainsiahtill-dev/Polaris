from __future__ import annotations

from polaris.kernelone.events.final_request_evidence import (
    FINAL_REQUEST_EVIDENCE_AUTHORITY_SCHEMA,
    FINAL_REQUEST_EVIDENCE_SCHEMA,
    attach_final_request_evidence,
    build_final_request_evidence,
    looks_like_workspace_quality_evidence_payload,
    normalize_context_snapshot_ref,
    summarize_workspace_quality_evidence_context_slot,
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
                "context_snapshot_ref": "runtime/contexts/ab/abcdef123456abcdef123456.json",
                "final_request_context_audit": audit,
                "messages": [{"role": "user", "content": "do not project this"}],
            }
        }
    )

    assert evidence["schema_version"] == FINAL_REQUEST_EVIDENCE_SCHEMA
    assert evidence["context_snapshot_ref"] == "abcdef123456abcdef123456"
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
            "context_snapshot_ref": "runtime/contexts/11/111111222222333333444444.json",
            "metadata": {"final_request_context_audit": audit},
        },
    )

    assert evidence["context_snapshot_ref"] == "111111222222333333444444"
    assert payload["context_snapshot_ref"] == "111111222222333333444444"
    assert payload["final_request_context_audit"] == audit
    assert isinstance(payload["final_request_evidence"], dict)
    assert isinstance(payload["audit_refs"], dict)
    assert payload["audit_refs"]["request_hash"] == "request-hash-2"
    assert payload["audit_refs"]["final_request_evidence_authority_hash"]


def test_build_final_request_evidence_derives_missing_refs_from_structured_slots() -> None:
    audit = {
        "schema_version": "llm.final_request_context_audit.v1",
        "final_request_evidence_coverage": {
            "request_hash": "request-hash-slots",
            "pass": False,
            "role_id": "director",
            "expected_role_id": "director",
            "role_identity_ok": True,
            "required_refs": ["pm_contract", "ce_blueprint"],
            "included_refs": ["pm_contract"],
            "evidence_slots": [
                {
                    "schema_version": "polaris.final_request_evidence_slot.v1",
                    "ref_type": "ce_blueprint",
                    "required": True,
                    "missing": True,
                },
                {
                    "schema_version": "polaris.final_request_evidence_slot.v1",
                    "ref_type": "optional_context",
                    "required": False,
                    "missing": True,
                },
            ],
            "tool_evidence_slots": [
                {
                    "schema_version": "polaris.final_request_tool_slot.v1",
                    "tool_name": "write_file",
                    "required": True,
                    "missing": True,
                }
            ],
        },
    }

    evidence = build_final_request_evidence(
        {
            "metadata": {
                "context_snapshot_ref": "runtime/contexts/aa/aaaabbbbccccddddeeeeffff.json",
                "final_request_context_audit": audit,
            }
        }
    )

    assert evidence["missing_required_refs"] == ["ce_blueprint"]
    assert evidence["missing_required_tools"] == ["write_file"]
    assert evidence["final_request_evidence_authority"]["missing_required_refs"] == ["ce_blueprint"]
    assert evidence["final_request_evidence_authority"]["missing_required_tools"] == ["write_file"]


def test_workspace_quality_context_slot_uses_structured_payload() -> None:
    payload = {
        "schema_version": "polaris.workspace_quality_evidence.v1",
        "source": "artifact_quality_evidence",
        "all_checks_passed": "false",
        "quality_errors": ["missing README", "test failed"],
        "deterministic_checks": "py_compile, min_files:3",
        "failed_required_modalities": ["command"],
        "missing_required_modalities": "browser; screenshot",
    }

    assert looks_like_workspace_quality_evidence_payload(payload)
    assert not looks_like_workspace_quality_evidence_payload(
        {"message": "quality_errors: ['missing README']"}
    )
    assert summarize_workspace_quality_evidence_context_slot(payload) == {
        "schema_version": "polaris.workspace_quality_evidence.context_slot.v1",
        "source_schema_version": "polaris.workspace_quality_evidence.v1",
        "source": "artifact_quality_evidence",
        "all_checks_passed": False,
        "quality_error_count": 2,
        "deterministic_check_count": 2,
        "failed_required_modalities": ["command"],
        "missing_required_modalities": ["browser", "screenshot"],
    }


def test_build_final_request_evidence_preserves_existing_lightweight_projection() -> None:
    evidence = build_final_request_evidence(
        {
            "final_request_evidence": {
                "context_snapshot_ref": "runtime/contexts/44/444444555555666666777777.json",
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

    assert evidence["context_snapshot_ref"] == "444444555555666666777777"
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

def test_context_snapshot_ref_normalizes_hash_and_rejects_invalid_refs() -> None:
    assert normalize_context_snapshot_ref("aabbccddeeff001122334455") == "aabbccddeeff001122334455"
    assert (
        normalize_context_snapshot_ref("runtime/contexts/aa/AABBCCDDEEFF001122334455.json")
        == "aabbccddeeff001122334455"
    )
    assert normalize_context_snapshot_ref("snapshot://not-a-context-store-hash") == ""
    assert normalize_context_snapshot_ref("runtime/contexts/aa/short.json") == ""
