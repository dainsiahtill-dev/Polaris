from __future__ import annotations

from dataclasses import replace

import pytest
from polaris.kernelone.events.final_request_evidence import (
    FINAL_REQUEST_EVIDENCE_AUTHORITY_SCHEMA,
    FINAL_REQUEST_EVIDENCE_SCHEMA,
    RoleFinalRequestEvidenceAnchorV1,
    RoleFinalRequestEvidenceSlotV1,
    RoleFinalRequestPolicyFactsV1,
    RoleFinalRequestPolicyV1,
    attach_final_request_evidence,
    build_final_request_coverage_sources,
    build_final_request_evidence,
    build_final_request_evidence_slots,
    build_final_request_tool_slots,
    canonical_role_final_request_hash,
    canonical_role_final_request_json,
    final_request_evidence_ref_for_coverage_flag,
    final_request_evidence_ref_for_requirement,
    final_request_evidence_refs_for_coverage_flags,
    final_request_evidence_refs_for_metadata_summary,
    final_request_included_evidence_refs,
    final_request_structured_evidence_from_metadata_summary,
    looks_like_ce_blueprint_payload,
    looks_like_failed_gate_evidence_context_payload,
    looks_like_pm_contract_payload,
    looks_like_target_scope_payload,
    looks_like_workspace_quality_evidence_payload,
    missing_required_refs_from_evidence_coverage,
    missing_required_refs_from_evidence_slots,
    missing_required_tools_from_evidence_coverage,
    normalize_context_snapshot_ref,
    redact_provider_transport,
    render_role_final_request_policy_facts,
    role_final_request_policy,
    role_final_request_source_keys,
    structured_context_coverage_flags,
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


def test_build_final_request_coverage_sources_owns_provenance_projection() -> None:
    coverage_sources = build_final_request_coverage_sources(
        refs=[
            "pm_contract",
            "execution_profile",
            "failed_gate_evidence",
            "custom_context",
            "ce_blueprint",
        ],
        included_refs=[
            "pm_contract",
            "execution_profile",
            "failed_gate_evidence",
            "custom_context",
        ],
        workflow_chain={
            "pm_contract_hash": "pm-workflow",
            "ce_blueprint_hash": "ce-workflow",
            "execution_profile_hash": "profile-workflow",
        },
        request_metadata_summary={
            "pm_contract_hash": "pm-summary",
            "has_pm_contract": True,
            "failed_gate_evidence_hash": "gate-summary",
            "failed_gate_evidence_summary": {"exit_code": 1},
            "has_failed_gate_evidence": True,
        },
    )

    assert coverage_sources == [
        {
            "ref_type": "pm_contract",
            "present": True,
            "source": "final_provider_request",
            "confidence": "structured_metadata",
            "freshness": "current_turn",
            "hash": "pm-summary",
        },
        {
            "ref_type": "execution_profile",
            "present": True,
            "source": "final_provider_request",
            "confidence": "structured_metadata",
            "freshness": "current_turn",
            "hash": "profile-workflow",
        },
        {
            "ref_type": "failed_gate_evidence",
            "present": True,
            "source": "final_provider_request",
            "confidence": "structured_metadata",
            "freshness": "current_turn",
            "hash": "gate-summary",
            "details": {"exit_code": 1},
        },
        {
            "ref_type": "custom_context",
            "present": True,
            "source": "final_provider_request",
            "confidence": "text_heuristic",
            "freshness": "current_turn",
        },
        {
            "ref_type": "ce_blueprint",
            "present": False,
            "source": "final_provider_request",
            "confidence": "absent",
            "freshness": "unknown",
            "hash": "ce-workflow",
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
    assert final_request_evidence_ref_for_requirement("pm_raw_intent") == "pm_raw_intent"
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

    refs = final_request_evidence_refs_for_coverage_flags(
        {
            "has_chief_engineer_blueprint": False,
            "has_workspace_quality_evidence": True,
            "has_target_files": True,
            "unknown_flag": True,
        },
        excluded_flags={"has_target_files"},
    )
    assert refs == ["ce_blueprint", "workspace_quality_evidence"]

    included_refs = final_request_evidence_refs_for_coverage_flags(
        {
            "has_chief_engineer_blueprint": False,
            "has_workspace_quality_evidence": True,
            "has_target_files": True,
        },
        require_present=True,
        excluded_flags={"has_target_files"},
    )
    assert included_refs == ["workspace_quality_evidence"]

    metadata_refs = final_request_evidence_refs_for_metadata_summary(
        {
            "has_execution_profile": True,
            "has_execution_strategy": False,
            "has_pm_contract": True,
            "has_chief_engineer_blueprint": True,
            "has_target_scope": True,
            "has_workspace_quality_evidence": True,
            "unknown_flag": True,
        }
    )
    assert metadata_refs == [
        "execution_profile",
        "pm_contract",
        "ce_blueprint",
        "target_files",
        "workspace_quality_evidence",
    ]

    structured = final_request_structured_evidence_from_metadata_summary(
        {
            "has_pm_contract": True,
            "has_chief_engineer_blueprint": False,
            "has_execution_envelope": True,
            "has_failed_gate_evidence": True,
            "has_workspace_quality_evidence": True,
            "has_target_scope": False,
        }
    )
    assert structured["pm_contract"] is True
    assert structured["ce_blueprint"] is False
    assert structured["execution_envelope"] is True
    assert structured["failed_gate_evidence"] is True
    assert structured["failure_evidence"] is True
    assert structured["workspace_quality_evidence"] is True
    assert structured["quality_evidence"] is True
    assert structured["target_files"] is False


def test_final_request_included_evidence_refs_are_kernelone_projection() -> None:
    included_refs = final_request_included_evidence_refs(
        coverage={
            "has_pm_contract": True,
            "has_chief_engineer_blueprint": True,
            "has_target_files": True,
            "has_workspace_quality_evidence": True,
            "unknown_flag": True,
        },
        request_metadata_summary={
            "has_pm_contract": True,
            "has_chief_engineer_blueprint": True,
            "has_target_scope": True,
            "has_execution_profile": True,
        },
        receipt_refs=["receipt-1"],
    )

    assert included_refs == [
        "final_provider_request",
        "workspace_quality_evidence",
        "execution_profile",
        "pm_contract",
        "ce_blueprint",
        "target_files",
        "receipt_store_refs",
    ]


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
    assert not looks_like_workspace_quality_evidence_payload({"message": "quality_errors: ['missing README']"})
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
    assert not looks_like_pm_contract_payload(
        {
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "goal": "Build the package",
            }
        }
    )
    assert not looks_like_pm_contract_payload(
        {
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "task_id": "TASK-1",
                "title": "Build the package",
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
    assert not looks_like_ce_blueprint_payload(
        {
            "ce_blueprint": {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": "ce-1",
                "title": "Implement the package",
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


def test_structured_context_coverage_flags_ignore_prompt_prose() -> None:
    assert structured_context_coverage_flags(
        {
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "target_files": ["src/index.ts"],
            },
            "ce_blueprint": {
                "schema_version": "chief_engineer.blueprint.v1",
                "target_files": ["src/index.ts"],
            },
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "authorization": {
                    "allowed_write_paths": ["src/index.ts"],
                    "allowed_read_paths": ["src/index.ts"],
                },
            },
            "run_ledger_projection": {
                "failure_evidence": [
                    {
                        "schema_version": "polaris.failure_evidence.v1",
                        "failure_class": "tool_dispatch_dropped",
                        "responsible_layer": "platform",
                        "evidence_refs": ["tool_lifecycle:turn-1"],
                    }
                ],
                "workspace_quality_evidence": {
                    "schema_version": "polaris.workspace_quality_evidence.v1",
                    "quality_errors": [{"code": "MISSING_TARGET"}],
                    "deterministic_checks": ["source_target_coverage"],
                },
            },
        }
    ) == {
        "has_pm_contract": True,
        "has_chief_engineer_blueprint": True,
        "has_target_files": True,
        "has_failure_feedback": True,
        "has_workspace_quality_evidence": True,
    }

    assert structured_context_coverage_flags(
        {
            "chat_messages": [
                {
                    "role": "user",
                    "content": "PM task contract target_files src/index.ts Chief Engineer blueprint",
                }
            ],
            "failure_summary": {"message": "failure_class: TOOL_DISPATCH_DROPPED"},
            "quality_summary": {"message": "quality_errors: ['missing README']"},
        }
    ) == {
        "has_pm_contract": False,
        "has_chief_engineer_blueprint": False,
        "has_target_files": False,
        "has_failure_feedback": False,
        "has_workspace_quality_evidence": False,
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
    assert not looks_like_failed_gate_evidence_context_payload({"message": "failure_class: TOOL_DISPATCH_DROPPED"})
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


def test_provider_transport_redaction_preserves_token_semantics_but_redacts_exact_secrets() -> None:
    redacted = redact_provider_transport(
        {
            "max_tokens": 4096,
            "token_budget": 8192,
            "Authorization": "Bearer secret",
            "provider_api_key": "secret-key",
        }
    )
    assert redacted["max_tokens"] == 4096
    assert redacted["token_budget"] == 8192
    assert redacted["Authorization"] == {"redacted": True, "kind": "secret"}
    assert redacted["provider_api_key"] == {"redacted": True, "kind": "secret"}


@pytest.mark.parametrize(
    ("role", "slots", "required_present"),
    [
        ("pm", ("pm_raw_intent",), ("pm_raw_intent",)),
        ("architect", ("pm_raw_intent",), ("pm_raw_intent",)),
        (
            "chief_engineer",
            ("pm_contract", "target_files", "workspace_quality"),
            ("pm_contract", "target_files"),
        ),
        (
            "director",
            ("pm_contract", "ce_blueprint", "target_files", "failure_feedback", "workspace_quality"),
            ("pm_contract", "ce_blueprint", "target_files"),
        ),
        (
            "qa",
            (
                "pm_contract",
                "ce_blueprint",
                "target_files",
                "verifier_receipts",
                "failure_feedback",
                "workspace_quality",
            ),
            ("pm_contract", "ce_blueprint", "target_files", "verifier_receipts", "workspace_quality"),
        ),
    ],
)
def test_role_final_request_policy_is_exact(
    role: str,
    slots: tuple[str, ...],
    required_present: tuple[str, ...],
) -> None:
    policy = role_final_request_policy(role)
    assert policy.role == role
    assert policy.slot_order == slots
    assert policy.required_present_slots == required_present
    record = policy.to_record()
    assert record["policy_hash"] == policy.policy_hash
    assert type(policy).from_record(record) == policy


def test_role_final_request_policy_rejects_unknown_role() -> None:
    with pytest.raises(ValueError, match="unknown_role"):
        role_final_request_policy("resident_agi")

    policy = role_final_request_policy("pm")
    record = policy.to_record()
    record["slot_order"] = ["pm_raw_intent", "extra"]
    with pytest.raises(ValueError, match=r"policy.*mismatch"):
        type(policy).from_record(record)
    with pytest.raises(ValueError, match=r"policy.*schema"):
        type(policy)(
            schema_version="drift.v2",
            role="pm",
            slot_order=("pm_raw_intent",),
            required_present_slots=("pm_raw_intent",),
        )


def test_role_final_request_source_keys_keep_canonical_slot_names() -> None:
    assert role_final_request_source_keys("failure_feedback") == ("failure_feedback", "failed_gate_evidence")
    assert role_final_request_source_keys("workspace_quality") == (
        "workspace_quality",
        "workspace_quality_evidence",
    )


def test_role_final_request_canonical_json_is_utf8_stable_and_strict() -> None:
    value = {"text": "质量", "items": [1, True, None]}
    rendered = canonical_role_final_request_json(value)
    assert rendered == '{"items":[1,true,null],"text":"质量"}'
    assert canonical_role_final_request_hash(value) == canonical_role_final_request_hash(
        {"items": [1, True, None], "text": "质量"}
    )
    with pytest.raises(ValueError, match="canonical_json_unsupported_type"):
        canonical_role_final_request_json({"bad": {"unordered"}})
    with pytest.raises(ValueError, match="canonical_json_unsupported_type"):
        canonical_role_final_request_json({"bad": object()})


def _anchor(slot: str, *, role: str = "chief_engineer") -> RoleFinalRequestEvidenceAnchorV1:
    return RoleFinalRequestEvidenceAnchorV1.create(
        ref_kind=slot,
        canonical_source_ref=f"factory/sources/{slot}",
        canonical_ref=f"runtime/facts/{slot}/item-1.json",
        canonical_hash="e" * 64,
        source_fact_schema="polaris.test_fact.v1",
        source_fact_version="1",
        factory_run_id="factory-run-1",
        run_id="run-1",
        role=role,
        request_freeze_id="freeze-1",
        cutoff_fact_id="cutoff-1",
        cutoff_fact_sequence=1,
        cutoff_fact_hash="a" * 64,
        source_fact_id=f"fact-{slot}",
        source_fact_sequence=2,
        source_fact_hash="c" * 64,
        source_head_sequence=3,
        source_head_hash="d" * 64,
        execution_authority_hash="b" * 64,
    )


def _slot(
    slot: str,
    *,
    role: str = "chief_engineer",
    state: str = "present",
) -> RoleFinalRequestEvidenceSlotV1:
    return RoleFinalRequestEvidenceSlotV1.create(
        ref_kind=slot,
        state=state,
        canonical_source_ref=f"factory/sources/{slot}",
        source_fact_schema="polaris.test_fact.v1",
        source_fact_version="1",
        factory_run_id="factory-run-1",
        run_id="run-1",
        role=role,
        request_freeze_id="freeze-1",
        cutoff_fact_id="cutoff-1",
        cutoff_fact_sequence=1,
        cutoff_fact_hash="a" * 64,
        source_head_sequence=3,
        source_head_hash="d" * 64,
        execution_authority_hash="b" * 64,
        items=(_anchor(slot, role=role),) if state == "present" else (),
    )


def test_role_final_request_policy_facts_validate_order_absence_and_hashes() -> None:
    facts = RoleFinalRequestPolicyFactsV1.create(
        role="chief_engineer",
        slots=(
            _slot("pm_contract"),
            _slot("target_files"),
            _slot("workspace_quality", state="absent_at_request_time"),
        ),
    )
    rendered = render_role_final_request_policy_facts(facts)
    assert '"schema_version":"polaris.role_final_request_evidence_slot.v1"' in rendered
    assert '"schema_version":"polaris.final_request_evidence_anchor.v1"' in rendered
    assert rendered.count('"ref_kind":"pm_contract"') == 2  # slot + one typed item anchor
    assert rendered.index('"ref_kind":"pm_contract"') < rendered.index('"ref_kind":"target_files"')
    assert "stderr" not in rendered
    assert "secret" not in rendered

    with pytest.raises(ValueError, match="slot_order_mismatch"):
        RoleFinalRequestPolicyFactsV1.create(
            role="chief_engineer",
            slots=(facts.slots[1], facts.slots[0], facts.slots[2]),
        )
    with pytest.raises(ValueError, match="required_slot_absent"):
        RoleFinalRequestPolicyFactsV1.create(
            role="chief_engineer",
            slots=(
                _slot("pm_contract", state="absent_at_request_time"),
                _slot("target_files"),
                _slot("workspace_quality", state="absent_at_request_time"),
            ),
        )

    record = facts.slots[0].items[0].to_record()
    record["canonical_hash"] = "0" * 63
    with pytest.raises(ValueError, match=r"canonical_hash.*64"):
        RoleFinalRequestEvidenceAnchorV1.from_record(record)

    facts_record = facts.to_record()
    facts_record["unexpected"] = True
    with pytest.raises(ValueError, match="fields_mismatch"):
        RoleFinalRequestPolicyFactsV1.from_record(facts_record)


def test_role_slot_schema_is_distinct_from_legacy_generic_slot_contract() -> None:
    legacy_slots = build_final_request_evidence_slots(
        coverage_sources=[{"ref_type": "ce_blueprint", "present": False}],
        required_refs=["ce_blueprint"],
        included_refs=[],
        missing_required_refs=["ce_blueprint"],
    )
    assert legacy_slots == [
        {
            "schema_version": "polaris.final_request_evidence_slot.v1",
            "ref_type": "ce_blueprint",
            "required": True,
            "present": False,
            "missing": True,
            "source": "final_provider_request",
            "confidence": "absent",
            "freshness": "unknown",
        }
    ]
    assert missing_required_refs_from_evidence_slots({"evidence_slots": legacy_slots}) == ["ce_blueprint"]

    role_slot = _slot("pm_contract")
    assert role_slot.schema_version == "polaris.role_final_request_evidence_slot.v1"
    assert missing_required_refs_from_evidence_slots({"evidence_slots": [role_slot.to_record()]}) == []

    with pytest.raises(ValueError, match="role_final_request_slot_fields_mismatch"):
        RoleFinalRequestEvidenceSlotV1.from_record(legacy_slots[0])

    role_shape_with_legacy_schema = role_slot.to_record()
    role_shape_with_legacy_schema["schema_version"] = "polaris.final_request_evidence_slot.v1"
    with pytest.raises(ValueError, match="role_final_request_slot_schema_mismatch"):
        RoleFinalRequestEvidenceSlotV1.from_record(role_shape_with_legacy_schema)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("schema_version", 7),
        ("ref_kind", 7),
        ("canonical_ref", 7),
        ("source_fact_id", 7),
        ("role", 7),
        ("canonical_hash", int("1" * 64)),
    ],
)
def test_anchor_record_rejects_non_string_authority_fields(field_name: str, invalid_value: int) -> None:
    record = _anchor("pm_contract").to_record()
    record[field_name] = invalid_value
    with pytest.raises(ValueError, match=rf"{field_name}_must_be_string"):
        RoleFinalRequestEvidenceAnchorV1.from_record(record)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("schema_version", 7),
        ("ref_kind", 7),
        ("canonical_source_ref", 7),
        ("cutoff_fact_id", 7),
        ("role", 7),
        ("source_head_hash", int("1" * 64)),
    ],
)
def test_slot_record_rejects_non_string_authority_fields(field_name: str, invalid_value: int) -> None:
    record = _slot("pm_contract").to_record()
    record[field_name] = invalid_value
    with pytest.raises(ValueError, match=rf"{field_name}_must_be_string"):
        RoleFinalRequestEvidenceSlotV1.from_record(record)


@pytest.mark.parametrize("field_name", ["schema_version", "role"])
def test_policy_facts_record_rejects_non_string_authority_fields(field_name: str) -> None:
    facts = RoleFinalRequestPolicyFactsV1.create(
        role="chief_engineer",
        slots=(
            _slot("pm_contract"),
            _slot("target_files"),
            _slot("workspace_quality", state="absent_at_request_time"),
        ),
    )
    record = facts.to_record()
    record[field_name] = 7
    with pytest.raises(ValueError, match=rf"{field_name}_must_be_string"):
        RoleFinalRequestPolicyFactsV1.from_record(record)


def test_runtime_role_dataclasses_reject_non_string_fields_when_annotations_are_bypassed() -> None:
    anchor = _anchor("pm_contract")
    with pytest.raises(ValueError, match="canonical_ref_must_be_string"):
        replace(anchor, canonical_ref=7)
    with pytest.raises(ValueError, match="canonical_hash_must_be_string"):
        replace(anchor, canonical_hash=int("1" * 64))

    slot = _slot("pm_contract")
    with pytest.raises(ValueError, match="canonical_source_ref_must_be_string"):
        replace(slot, canonical_source_ref=7)
    with pytest.raises(ValueError, match="source_head_hash_must_be_string"):
        replace(slot, source_head_hash=int("1" * 64))

    facts = RoleFinalRequestPolicyFactsV1.create(
        role="chief_engineer",
        slots=(
            _slot("pm_contract"),
            _slot("target_files"),
            _slot("workspace_quality", state="absent_at_request_time"),
        ),
    )
    with pytest.raises(ValueError, match="schema_version_must_be_string"):
        replace(facts, schema_version=7)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("canonical_ref", 7),
        ("canonical_hash", int("1" * 64)),
    ],
)
def test_anchor_create_rejects_non_string_authority_fields(field_name: str, invalid_value: int) -> None:
    kwargs = {
        "ref_kind": "pm_contract",
        "canonical_source_ref": "factory/sources/pm_contract",
        "canonical_ref": "runtime/facts/pm_contract/item-1.json",
        "canonical_hash": "e" * 64,
        "source_fact_schema": "polaris.test_fact.v1",
        "source_fact_version": "1",
        "factory_run_id": "factory-run-1",
        "run_id": "run-1",
        "role": "chief_engineer",
        "request_freeze_id": "freeze-1",
        "cutoff_fact_id": "cutoff-1",
        "cutoff_fact_sequence": 1,
        "cutoff_fact_hash": "a" * 64,
        "source_fact_id": "fact-pm-contract",
        "source_fact_sequence": 2,
        "source_fact_hash": "c" * 64,
        "source_head_sequence": 3,
        "source_head_hash": "d" * 64,
        "execution_authority_hash": "b" * 64,
    }
    kwargs[field_name] = invalid_value
    with pytest.raises(ValueError, match=rf"{field_name}_must_be_string"):
        RoleFinalRequestEvidenceAnchorV1.create(**kwargs)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("canonical_source_ref", 7),
        ("source_head_hash", int("1" * 64)),
        ("execution_authority_hash", int("1" * 64)),
    ],
)
def test_slot_create_rejects_non_string_authority_fields(field_name: str, invalid_value: int) -> None:
    kwargs = {
        "ref_kind": "workspace_quality",
        "state": "absent_at_request_time",
        "canonical_source_ref": "factory/sources/workspace_quality",
        "source_fact_schema": "polaris.test_fact.v1",
        "source_fact_version": "1",
        "factory_run_id": "factory-run-1",
        "run_id": "run-1",
        "role": "chief_engineer",
        "request_freeze_id": "freeze-1",
        "cutoff_fact_id": "cutoff-1",
        "cutoff_fact_sequence": 1,
        "cutoff_fact_hash": "a" * 64,
        "source_head_sequence": 3,
        "source_head_hash": "d" * 64,
        "execution_authority_hash": "b" * 64,
        "items": (),
    }
    kwargs[field_name] = invalid_value
    with pytest.raises(ValueError, match=rf"{field_name}_must_be_string"):
        RoleFinalRequestEvidenceSlotV1.create(**kwargs)


def test_policy_facts_create_rejects_numeric_role() -> None:
    with pytest.raises(ValueError, match="role_must_be_string"):
        RoleFinalRequestPolicyFactsV1.create(
            role=7,
            slots=(
                _slot("pm_contract"),
                _slot("target_files"),
                _slot("workspace_quality", state="absent_at_request_time"),
            ),
        )


@pytest.mark.parametrize("field_name", ["schema_version", "role", "policy_hash"])
def test_role_policy_record_rejects_non_string_authority_fields(field_name: str) -> None:
    record = role_final_request_policy("chief_engineer").to_record()
    record[field_name] = int("1" * 64) if field_name == "policy_hash" else 7
    with pytest.raises(ValueError, match=rf"{field_name}_must_be_string"):
        RoleFinalRequestPolicyV1.from_record(record)


def test_role_policy_runtime_and_lookup_entries_reject_numeric_authority_fields() -> None:
    policy = role_final_request_policy("chief_engineer")
    with pytest.raises(ValueError, match="schema_version_must_be_string"):
        replace(policy, schema_version=7)
    with pytest.raises(ValueError, match="role_must_be_string"):
        role_final_request_policy(7)
    with pytest.raises(ValueError, match="ref_kind_must_be_string"):
        role_final_request_source_keys(7)


def test_slot_rejects_raw_payload_items_and_absent_typed_items() -> None:
    with pytest.raises(ValueError, match="typed_anchor"):
        RoleFinalRequestEvidenceSlotV1.create(
            ref_kind="workspace_quality",
            state="present",
            canonical_source_ref="factory/sources/workspace_quality",
            source_fact_schema="polaris.test_fact.v1",
            source_fact_version="1",
            factory_run_id="factory-run-1",
            run_id="run-1",
            role="chief_engineer",
            request_freeze_id="freeze-1",
            cutoff_fact_id="cutoff-1",
            cutoff_fact_sequence=1,
            cutoff_fact_hash="a" * 64,
            source_head_sequence=3,
            source_head_hash="d" * 64,
            execution_authority_hash="b" * 64,
            items=({"stderr": "secret", "token": "must-not-render"},),
        )
    with pytest.raises(ValueError, match="absent_slot_items_must_be_empty"):
        RoleFinalRequestEvidenceSlotV1.create(
            ref_kind="workspace_quality",
            state="absent_at_request_time",
            canonical_source_ref="factory/sources/workspace_quality",
            source_fact_schema="polaris.test_fact.v1",
            source_fact_version="1",
            factory_run_id="factory-run-1",
            run_id="run-1",
            role="chief_engineer",
            request_freeze_id="freeze-1",
            cutoff_fact_id="cutoff-1",
            cutoff_fact_sequence=1,
            cutoff_fact_hash="a" * 64,
            source_head_sequence=3,
            source_head_hash="d" * 64,
            execution_authority_hash="b" * 64,
            items=(_anchor("workspace_quality"),),
        )


def test_anchor_source_fact_must_belong_to_captured_source_head() -> None:
    with pytest.raises(ValueError, match="source_fact_sequence_exceeds_head"):
        RoleFinalRequestEvidenceAnchorV1.create(
            ref_kind="pm_contract",
            canonical_source_ref="factory/sources/pm_contract",
            canonical_ref="runtime/facts/pm_contract/item-1.json",
            canonical_hash="e" * 64,
            source_fact_schema="polaris.test_fact.v1",
            source_fact_version="1",
            factory_run_id="factory-run-1",
            run_id="run-1",
            role="chief_engineer",
            request_freeze_id="freeze-1",
            cutoff_fact_id="cutoff-1",
            cutoff_fact_sequence=1,
            cutoff_fact_hash="a" * 64,
            source_fact_id="fact-pm-contract",
            source_fact_sequence=4,
            source_fact_hash="c" * 64,
            source_head_sequence=3,
            source_head_hash="d" * 64,
            execution_authority_hash="b" * 64,
        )


@pytest.mark.parametrize(
    ("role", "absent_slots"),
    [
        ("pm", ()),
        ("chief_engineer", ("workspace_quality",)),
        ("director", ("failure_feedback", "workspace_quality")),
        ("qa", ("failure_feedback",)),
    ],
)
def test_role_final_request_dynamic_state_matrix(role: str, absent_slots: tuple[str, ...]) -> None:
    policy = role_final_request_policy(role)
    facts = RoleFinalRequestPolicyFactsV1.create(
        role=role,
        slots=tuple(
            _slot(
                ref_kind,
                role=role,
                state="absent_at_request_time" if ref_kind in absent_slots else "present",
            )
            for ref_kind in policy.slot_order
        ),
    )
    assert tuple(slot.ref_kind for slot in facts.slots) == policy.slot_order
    assert tuple(slot.ref_kind for slot in facts.slots if slot.state == "absent_at_request_time") == absent_slots
