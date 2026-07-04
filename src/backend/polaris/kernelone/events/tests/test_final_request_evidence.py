from __future__ import annotations

from polaris.kernelone.events.final_request_evidence import (
    FINAL_REQUEST_EVIDENCE_AUTHORITY_SCHEMA,
    FINAL_REQUEST_EVIDENCE_SCHEMA,
    attach_final_request_evidence,
    build_final_request_evidence,
    build_final_request_evidence_slots,
    build_final_request_tool_slots,
    final_request_evidence_ref_for_coverage_flag,
    final_request_evidence_ref_for_requirement,
    looks_like_ce_blueprint_payload,
    looks_like_failed_gate_evidence_context_payload,
    looks_like_pm_contract_payload,
    looks_like_target_scope_payload,
    looks_like_workspace_quality_evidence_payload,
    missing_required_refs_from_evidence_coverage,
    missing_required_tools_from_evidence_coverage,
    normalize_context_snapshot_ref,
    summarize_target_scope_evidence_payload,
    summarize_workspace_quality_evidence_context_slot,
    target_scope_evidence_entry,
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


def test_final_request_slot_builders_project_structured_coverage() -> None:
    evidence_slots = build_final_request_evidence_slots(
        coverage_sources=[
            {
                "ref_type": "pm_contract",
                "present": True,
                "source": "final_provider_request",
                "confidence": "structured_metadata",
                "freshness": "current_turn",
                "hash": "pm-hash",
                "details": {"task_id": "TASK-1"},
            },
            {"ref_type": "ce_blueprint", "present": False},
        ],
        required_refs=["pm_contract", "ce_blueprint"],
        included_refs=["pm_contract"],
        missing_required_refs=["ce_blueprint"],
    )
    tool_slots = build_final_request_tool_slots(
        required_tools=["write_file", "read_file"],
        available_tools=["read_file", "read_file"],
        missing_required_tools=["write_file"],
    )

    assert evidence_slots == [
        {
            "schema_version": "polaris.final_request_evidence_slot.v1",
            "ref_type": "pm_contract",
            "required": True,
            "present": True,
            "missing": False,
            "source": "final_provider_request",
            "confidence": "structured_metadata",
            "freshness": "current_turn",
            "hash": "pm-hash",
            "details": {"task_id": "TASK-1"},
        },
        {
            "schema_version": "polaris.final_request_evidence_slot.v1",
            "ref_type": "ce_blueprint",
            "required": True,
            "present": False,
            "missing": True,
            "source": "final_provider_request",
            "confidence": "absent",
            "freshness": "unknown",
        },
    ]
    assert tool_slots == [
        {
            "schema_version": "polaris.final_request_tool_slot.v1",
            "tool_name": "write_file",
            "required": True,
            "present": False,
            "missing": True,
            "source": "final_provider_request.tools",
            "confidence": "absent",
            "freshness": "unknown",
        },
        {
            "schema_version": "polaris.final_request_tool_slot.v1",
            "tool_name": "read_file",
            "required": True,
            "present": True,
            "missing": False,
            "source": "final_provider_request.tools",
            "confidence": "tool_schema",
            "freshness": "current_turn",
        },
    ]


def test_missing_required_readers_prefer_structured_slots_over_legacy_fields() -> None:
    coverage = {
        "missing_required_refs": ["legacy_blueprint"],
        "missing_required_tools": ["legacy_write_file"],
        "evidence_slots": [
            {
                "schema_version": "polaris.final_request_evidence_slot.v1",
                "ref_type": "ce_blueprint",
                "required": True,
                "missing": True,
            }
        ],
        "tool_evidence_slots": [
            {
                "schema_version": "polaris.final_request_tool_slot.v1",
                "tool_name": "write_file",
                "required": True,
                "missing": True,
            }
        ],
    }

    assert missing_required_refs_from_evidence_coverage(coverage) == ["ce_blueprint"]
    assert missing_required_tools_from_evidence_coverage(coverage) == ["write_file"]


def test_missing_required_readers_fallback_to_legacy_fields_when_slots_absent() -> None:
    coverage = {"missing_required_refs": ["ce_blueprint", "target_files"]}
    existing = {"missing_required_tools": ["write_file", "execute_command"]}

    assert missing_required_refs_from_evidence_coverage(coverage, existing) == [
        "ce_blueprint",
        "target_files",
    ]
    assert missing_required_tools_from_evidence_coverage(coverage, existing) == [
        "write_file",
        "execute_command",
    ]


def test_final_request_evidence_ref_helpers_normalize_requirement_and_coverage_aliases() -> None:
    assert final_request_evidence_ref_for_requirement("pm_task_contract") == "pm_contract"
    assert final_request_evidence_ref_for_requirement("cross_file_interface_contract") == "module_interface_contract"
    assert final_request_evidence_ref_for_requirement("verification_failure_evidence") == "failed_gate_evidence"
    assert final_request_evidence_ref_for_requirement("custom_ref") == "custom_ref"
    assert final_request_evidence_ref_for_requirement("CustomRef") == "CustomRef"

    assert final_request_evidence_ref_for_coverage_flag("has_chief_engineer_blueprint") == "ce_blueprint"
    assert final_request_evidence_ref_for_coverage_flag("has_workspace_quality_evidence") == (
        "workspace_quality_evidence"
    )
    assert final_request_evidence_ref_for_coverage_flag("unknown_flag") == ""


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


def test_contract_and_scope_predicates_require_structured_payloads() -> None:
    assert looks_like_pm_contract_payload(
        {
            "schema_version": "pm.task_contract.v1",
            "task_id": "TASK-1",
            "target_files": ["src/index.py"],
        }
    )
    assert looks_like_pm_contract_payload(
        {
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "goal": "Build the package",
            }
        }
    )
    assert not looks_like_pm_contract_payload({"pm_contract": {"note": "PM Task Contract"}})

    assert looks_like_ce_blueprint_payload(
        {
            "schema_version": "chief_engineer.blueprint.v1",
            "blueprint_id": "ce-1",
            "construction_plan": {"phase": "implement"},
        }
    )
    assert looks_like_ce_blueprint_payload(
        {
            "ce_blueprint": {
                "schema_version": "chief_engineer.blueprint.v1",
                "target_files": ["src/index.py"],
            }
        }
    )
    assert not looks_like_ce_blueprint_payload({"ce_blueprint": {"note": "Chief Engineer Blueprint"}})

    assert looks_like_target_scope_payload({"target_files": ["src/index.py"]})
    assert looks_like_target_scope_payload({"authorization": {"allowed_write_paths": ["src/index.py"]}})
    assert not looks_like_target_scope_payload({"target_files": "src/index.py"})
    assert not looks_like_target_scope_payload({"target_files": []})

    entry = target_scope_evidence_entry(
        "execution_contract",
        {
            "targets": ["src/index.py"],
            "declared_scopes": ["src"],
            "allowed_paths": ["src/index.py"],
            "allowed_read_paths": ["src"],
        },
    )
    assert entry == {
        "source": "execution_contract",
        "target_files": ["src/index.py"],
        "scope_paths": ["src"],
        "allowed_write_paths": ["src/index.py"],
        "allowed_read_paths": ["src"],
    }
    summary = summarize_target_scope_evidence_payload(
        {"schema_version": "polaris.target_scope.evidence.v1", "sources": [entry]}
    )
    payload_hash = summary.pop("payload_hash")
    assert len(payload_hash) == 64
    assert summary == {
        "schema_version": "polaris.target_scope.evidence.context_slot.v1",
        "source_schema_version": "polaris.target_scope.evidence.v1",
        "source_count": 1,
        "target_file_count": 1,
        "scope_path_count": 1,
        "allowed_write_path_count": 1,
        "allowed_read_path_count": 1,
        "sources": [
            {
                "source": "execution_contract",
                "target_file_count": 1,
                "scope_path_count": 1,
                "allowed_write_path_count": 1,
                "allowed_read_path_count": 1,
            }
        ],
    }


def test_failed_gate_context_payload_uses_structured_payload() -> None:
    assert looks_like_failed_gate_evidence_context_payload(
        {"schema_version": "polaris.failed_gate_evidence.context_slot.v1"}
    )
    assert looks_like_failed_gate_evidence_context_payload(
        {
            "items": [
                {
                    "schema_version": "failure_evidence.v1",
                    "failure_class": "TOOL_DISPATCH_DROPPED",
                }
            ]
        }
    )
    assert looks_like_failed_gate_evidence_context_payload({"failed_required_modalities": ["command"]})
    assert looks_like_failed_gate_evidence_context_payload({"exit_code": 1, "command": "npm test"})
    assert not looks_like_failed_gate_evidence_context_payload("failure_class: TOOL_DISPATCH_DROPPED")
    assert not looks_like_failed_gate_evidence_context_payload(
        {"message": "failure_class: TOOL_DISPATCH_DROPPED"}
    )
    assert not looks_like_failed_gate_evidence_context_payload({"stderr": "test failed"})
    assert not looks_like_failed_gate_evidence_context_payload({"command": "npm test"})
    assert not looks_like_failed_gate_evidence_context_payload({"items": ["failure_class: TOOL_DISPATCH_DROPPED"]})


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
