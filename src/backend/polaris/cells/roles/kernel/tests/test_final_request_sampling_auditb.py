"""Tests for final provider request sampling audit metadata."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from polaris.cells.roles.kernel.internal.llm_caller import context_audit as context_audit_module
from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
    FinalRequestEvidenceCoverageError,
    build_final_provider_request_snapshot,
    build_final_request_context_audit_for_request,
    enforce_final_request_evidence_coverage,
    final_request_evidence_coverage_violation,
)
from polaris.cells.roles.kernel.internal.llm_caller.response_types import PreparedLLMRequest
from polaris.cells.roles.kernel.internal.structured_output_transport import (
    STRUCTURED_OUTPUT_TOOL_NAME,
    resolve_structured_output_transport,
)
from polaris.cells.roles.kernel.public.structured_output_contracts import (
    STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY,
    RoleStructuredOutputContractV1,
)
from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry


def _actual_sibling_exports_v2(
    *,
    dependency_task_id: str = "1",
    parent_external_task_id: str = "TASK-1",
    path: str = "src/models/flavor.rs",
    body: str = "pub enum FlavorProfile { Sweet, Sour }\n",
) -> dict[str, object]:
    body_bytes = body.encode("utf-8")
    module: dict[str, object] = {
        "parent_task_id": dependency_task_id,
        "parent_runtime_task_id": dependency_task_id,
        "parent_external_task_id": parent_external_task_id,
        "source_fact_ref": f"task_runtime.observable_task:{dependency_task_id}",
        "source_fact_hash": "a" * 64,
        "effect_receipt_id": "director-physical-effect-receipt-1",
        "effect_receipt_hash": "b" * 64,
        "effect_receipt_binding_hash": "c" * 64,
        "physical_result_hash": "d" * 64,
        "target_state_hash": "e" * 64,
        "path": path,
        "sha256": hashlib.sha256(body_bytes).hexdigest(),
        "byte_count": len(body_bytes),
        "body": body,
        "guarded_snapshot": {
            "device": 1,
            "inode": 2,
            "mtime_ns": 3,
            "ctime_ns": 4,
            "root_device": 5,
            "root_inode": 6,
        },
    }
    payload: dict[str, object] = {
        "schema_version": "polaris.actual_sibling_exports.evidence.v2",
        "source": "roles.adapters.director.task_runtime_dependency_artifact_snapshot",
        "dependency_task_ids": [dependency_task_id],
        "covered_parent_task_ids": [dependency_task_id],
        "modules": [module],
        "module_count": 1,
        "total_byte_count": len(body_bytes),
    }
    payload["snapshot_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def _actual_sibling_exports_message(payload: dict[str, object], *, include_body: bool = True) -> str:
    module = payload["modules"][0]
    assert isinstance(module, dict)
    lines = [
        (f"polaris.actual_sibling_exports.evidence.v2 snapshot_sha256={payload['snapshot_sha256']}"),
        (
            f"--- parent_task_id={module['parent_task_id']} "
            f"receipt_id={module['effect_receipt_id']} "
            f"path={module['path']} sha256={module['sha256']} ---"
        ),
    ]
    if include_body:
        lines.append(str(module["body"]))
    return "\n".join(lines)


def _audit_required_actual_sibling_payload(
    payload: dict[str, object],
    *,
    message: str,
) -> dict[str, object]:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 4000},
        context={
            "director_execution_strategy": {
                "schema_version": "task.execution_strategy.v1",
                "evidence_requirements": [
                    "pm_task_contract",
                    "chief_engineer_blueprint",
                    "target_files_or_declared_scopes",
                    "actual_sibling_exports",
                ],
            },
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "envelope-hash",
                "pm_contract": {"hash": "pm-hash"},
                "ce_blueprint": {"hash": "ce-hash"},
                "authorization": {
                    "target_files": ["src/main.rs"],
                    "scope_paths": ["src/main.rs"],
                },
            },
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "task_id": "TASK-2",
                "target_files": ["src/main.rs"],
            },
            "ce_blueprint": {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": "ce_TASK-2",
                "target_files": ["src/main.rs"],
                "construction_plan": {"phase": "implementation"},
            },
            "target_files": ["src/main.rs"],
            "actual_sibling_exports": payload,
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "system",
                "content": (
                    "PM Task Contract / 任务合同: TASK-2 src/main.rs. "
                    "Chief Engineer Blueprint / CE 蓝图交接: ce_TASK-2.\n" + message
                ),
            }
        ],
        input_text="test",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )
    return build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )




def test_delivery_contracts_satisfy_architecture_file_plan_requirement_for_retry_context() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 4000},
        context={
            "director_execution_strategy": {
                "schema_version": "director.execution_strategy.v1",
                "evidence_requirements": ["architecture_or_file_plan"],
            },
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "retry-envelope-hash",
            },
            "delivery_plan_document": {
                "schema_version": "polaris.delivery_plan_document.v1",
                "title": "Meteor Wish Queue",
                "capability_plan": ["domain engine owns meteor, wish, queue, and priority behavior"],
                "behavior_plan": ["entrypoint wires existing source modules without redefining owners"],
                "verification_plan": ["node --check src/index.js", "node --test tests/product.test.js"],
            },
            "delivery_depth_contract": {
                "schema_version": "polaris.delivery_depth_contract.v1",
                "level": 3,
                "minimums": {
                    "min_production_source_files": 3,
                    "min_behavior_test_files": 1,
                    "advisory_label": "not projected",
                },
                "acceptance_contract": {
                    "deterministic_checks": ["production_source_files", "behavior_test_files"],
                    "required_behavior_tests": ["normal", "boundary", "invalid"],
                },
                "behavior_contract": {
                    "rule_matrix": ["priority rules are observable from the entrypoint"],
                    "required_behavior_tests": ["normal", "boundary", "invalid"],
                },
            },
            "target_files": ["src/index.js"],
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {
                    "role": "user",
                    "content": (
                        "[mode:materialize]\n"
                        "RETRY: previous Director turn completed without any write/edit receipt.\n"
                        "Allowed target files: src/index.js."
                    ),
                },
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {
                "role": "user",
                "content": (
                    "[mode:materialize]\n"
                    "RETRY: previous Director turn completed without any write/edit receipt.\n"
                    "Allowed target files: src/index.js."
                ),
            },
        ],
        input_text="test",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )

    audit = build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    metadata_summary = audit["request_metadata_summary"]
    assert metadata_summary["has_delivery_plan_document"] is True
    assert metadata_summary["has_delivery_depth_contract"] is True
    assert metadata_summary["delivery_depth_contract_summary"] == {
        "schema_version": "polaris.delivery_depth_contract.v1",
        "level": 3,
        "minimums": {
            "min_production_source_files": 3,
            "min_behavior_test_files": 1,
        },
        "deterministic_checks": ["production_source_files", "behavior_test_files"],
        "required_behavior_test_count": 3,
    }
    assert metadata_summary["has_architecture_or_file_plan"] is True
    assert metadata_summary["architecture_or_file_plan_summary"]["source"] == "delivery_contracts"
    evidence_coverage = audit["final_request_evidence_coverage"]
    assert "architecture_or_file_plan" in evidence_coverage["required_refs"]
    assert "architecture_or_file_plan" in evidence_coverage["included_refs"]
    assert evidence_coverage["structured_evidence"]["architecture_or_file_plan"] is True
    assert evidence_coverage["missing_required_refs"] == []
    assert evidence_coverage["pass"] is True
    enforce_final_request_evidence_coverage(ai_request=ai_request, audit=audit)


def test_final_request_evidence_coverage_tracks_interface_discrepancy_context() -> None:
    interface_delta = {
        "schema_version": "director.interface_delta.v1",
        "task_id": "TASK-2",
        "contract_present": True,
        "diagnostic_paths": ["src/engine/forecast.py"],
        "requested_symbols": ["WeatherKind"],
        "actual_public_symbols_by_path": {"src/models/weather.py": ["WeatherReport"]},
        "diagnostic_count": 1,
    }
    triage_summary = {
        "schema_version": "director.interface_discrepancy_triage.v1",
        "recommended_owner": "director",
        "recommended_route": "director_retry_with_interface_discrepancy_context",
        "director_retry_allowed": True,
        "llm_fallback_blocked": False,
        "macro_blueprint_regeneration_allowed": False,
    }
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 4000},
        context={
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "envelope-hash",
                "audit_policy": {
                    "final_provider_request_required": True,
                    "required_evidence": ["interface_discrepancy_context"],
                },
            },
            "director_interface_discrepancy_retry": {
                "authorized": True,
                "recommended_owner": "director",
                "recommended_route": "director_retry_with_interface_discrepancy_context",
                "interface_discrepancy_evidence": {
                    "schema_version": "director.interface_discrepancy_receipt.v1",
                    "plan_probe_status": "coverage_matched_but_unplannable",
                    "recommended_owner": "director",
                    "recommended_route": "director_retry_with_interface_discrepancy_context",
                    "director_retry_allowed": True,
                    "llm_fallback_blocked": False,
                    "interface_delta": interface_delta,
                    "triage_summary": triage_summary,
                    "diagnostics": [{"code": "unresolved_import_symbol"}],
                    "source_tools": ["deterministic_unresolved_import_symbol_repair"],
                },
            },
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Repair the current task boundary."},
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {"role": "user", "content": "Repair the current task boundary."},
        ],
        input_text="test",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )

    audit = build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    metadata_summary = audit["request_metadata_summary"]
    assert metadata_summary["has_interface_discrepancy_context"] is True
    discrepancy_summary = metadata_summary["interface_discrepancy_context_summary"]
    assert discrepancy_summary["interface_delta_available"] is True
    assert discrepancy_summary["triage_summary_available"] is True
    assert discrepancy_summary["recommended_route"] == "director_retry_with_interface_discrepancy_context"
    assert discrepancy_summary["diagnostic_count"] == 1

    evidence_coverage = audit["final_request_evidence_coverage"]
    assert "interface_discrepancy_context" in evidence_coverage["required_refs"]
    assert "interface_discrepancy_context" in evidence_coverage["included_refs"]
    assert evidence_coverage["structured_evidence"]["interface_discrepancy_context"] is True
    source = next(
        item for item in evidence_coverage["coverage_sources"] if item["ref_type"] == "interface_discrepancy_context"
    )
    assert source["confidence"] == "structured_metadata"
    assert source["hash"]
    slot = next(
        item for item in evidence_coverage["evidence_slots"] if item["ref_type"] == "interface_discrepancy_context"
    )
    assert {key: value for key, value in slot.items() if key != "details"} == {
        "schema_version": "polaris.final_request_evidence_slot.v1",
        "ref_type": "interface_discrepancy_context",
        "required": True,
        "present": True,
        "missing": False,
        "source": "final_provider_request",
        "confidence": "structured_metadata",
        "freshness": "current_turn",
        "hash": source["hash"],
    }
    slot_details = slot["details"]
    assert slot_details["diagnostic_count"] == 1
    assert slot_details["interface_delta_available"] is True
    assert slot_details["triage_summary_available"] is True
    assert slot_details["llm_fallback_blocked"] is False
    assert slot_details["plan_probe_status"] == "coverage_matched_but_unplannable"
    assert slot_details["recommended_owner"] == "director"
    assert slot_details["recommended_route"] == "director_retry_with_interface_discrepancy_context"
    assert slot_details["interface_delta"]["requested_symbols"] == ["WeatherKind"]
    assert slot_details["triage_summary"]["director_retry_allowed"] is True
    assert slot_details["source_tools"] == ["deterministic_unresolved_import_symbol_repair"]
    assert evidence_coverage["missing_required_refs"] == []
    assert evidence_coverage["pass"] is True
    enforce_final_request_evidence_coverage(ai_request=ai_request, audit=audit)


def test_final_request_evidence_coverage_blocks_missing_required_interface_discrepancy_context() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 4000},
        context={
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "envelope-hash",
                "audit_policy": {
                    "final_provider_request_required": True,
                    "required_evidence": ["interface_discrepancy_context"],
                },
            },
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Repair the current task boundary."},
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {"role": "user", "content": "Repair the current task boundary."},
        ],
        input_text="test",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )

    audit = build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    evidence_coverage = audit["final_request_evidence_coverage"]
    assert "interface_discrepancy_context" in evidence_coverage["required_refs"]
    assert "interface_discrepancy_context" in evidence_coverage["missing_required_refs"]
    slot = next(
        item for item in evidence_coverage["evidence_slots"] if item["ref_type"] == "interface_discrepancy_context"
    )
    assert slot["required"] is True
    assert slot["present"] is False
    assert slot["missing"] is True
    assert slot["confidence"] == "absent"
    assert evidence_coverage["pass"] is False
    violation = final_request_evidence_coverage_violation(ai_request=ai_request, audit=audit)
    assert violation is not None
    assert violation["missing_required_refs"] == ["interface_discrepancy_context"]
    slot_only_audit = {
        **audit,
        "final_request_evidence_coverage": {
            **evidence_coverage,
            "missing_required_refs": [],
        },
    }
    slot_only_violation = final_request_evidence_coverage_violation(ai_request=ai_request, audit=slot_only_audit)
    assert slot_only_violation is not None
    assert slot_only_violation["missing_required_refs"] == ["interface_discrepancy_context"]

    try:
        enforce_final_request_evidence_coverage(ai_request=ai_request, audit=audit)
    except FinalRequestEvidenceCoverageError as exc:
        assert exc.violation["missing_required_refs"] == ["interface_discrepancy_context"]
    else:
        raise AssertionError("expected FinalRequestEvidenceCoverageError")


def test_final_request_context_audit_flags_required_tool_pruning() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="pm",
        input="",
        options={"temperature": 0.2, "max_tokens": 2000, "tools": []},
        context={
            "chat_messages": [
                {
                    "role": "user",
                    "content": "PM route audit probe for deterministic contract mode.",
                }
            ],
            "required_tools": ["repo_tree", "read_file"],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "user",
                "content": "PM route audit probe for deterministic contract mode.",
            }
        ],
        input_text="test",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )

    audit = build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="pm", max_context_tokens=128_000),
    )

    evidence_coverage = audit["final_request_evidence_coverage"]
    finding_codes = {item["code"] for item in audit["context_quality"]["findings"]}
    assert evidence_coverage["required_tools"] == ["repo_tree", "read_file"]
    assert evidence_coverage["available_tools"] == []
    assert evidence_coverage["missing_required_tools"] == ["repo_tree", "read_file"]
    assert evidence_coverage["tool_evidence_slots"] == [
        {
            "schema_version": "polaris.final_request_tool_slot.v1",
            "tool_name": "repo_tree",
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
            "present": False,
            "missing": True,
            "source": "final_provider_request.tools",
            "confidence": "absent",
            "freshness": "unknown",
        },
    ]
    assert evidence_coverage["pass"] is False
    assert "missing_required_final_request_tools" in finding_codes


def test_final_request_context_audit_does_not_treat_allowed_tools_as_required() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.2, "max_tokens": 2000, "tools": []},
        context={
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Explain the current workspace status."},
            ],
            "allowed_tools": ["read_file", "repo_tree"],
            "tool_policy": {"allowed_tools": ["search_code"]},
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {"role": "user", "content": "Explain the current workspace status."},
        ],
        input_text="test",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )

    audit = build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    coverage = audit["final_request_evidence_coverage"]
    assert coverage["required_tools"] == []
    assert coverage["missing_required_tools"] == []
    assert coverage["allowed_tools"] == ["read_file", "repo_tree", "repo_rg"]
    assert coverage["tool_surface"]["required_tool_source"] == "explicit_required_tool_fields_only"
    assert "missing_required_final_request_tools" not in {item["code"] for item in audit["context_quality"]["findings"]}


def test_final_request_context_audit_keeps_tool_contract_allowed_and_required_separate() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.2, "max_tokens": 2000, "tools": []},
        context={
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Repair the target file."},
            ],
            "tool_contract": {
                "allowed_tools": ["read_file", "repo_tree"],
                "required_tools": ["write_file"],
            },
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {"role": "user", "content": "Repair the target file."},
        ],
        input_text="test",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )

    audit = build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    coverage = audit["final_request_evidence_coverage"]
    assert coverage["required_tools"] == ["write_file"]
    assert coverage["allowed_tools"] == ["read_file", "repo_tree"]
    assert coverage["missing_required_tools"] == ["write_file"]
    assert coverage["tool_evidence_slots"][0]["tool_name"] == "write_file"
    assert coverage["tool_evidence_slots"][0]["missing"] is True
    assert "write_file" not in coverage["removed_allowed_tools"]


def test_final_request_context_audit_canonicalizes_required_tool_aliases() -> None:
    repo_rg_schema = {
        "type": "function",
        "function": {
            "name": "repo_rg",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    }
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.2, "max_tokens": 2000, "tools": [repo_rg_schema]},
        context={
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Search the repository."},
            ],
            "required_tools": ["search_code"],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {"role": "user", "content": "Search the repository."},
        ],
        input_text="test",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )

    audit = build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    coverage = audit["final_request_evidence_coverage"]
    assert coverage["required_tools"] == ["repo_rg"]
    assert coverage["available_tools"] == ["repo_rg"]
    assert coverage["missing_required_tools"] == []
    assert coverage["tool_surface"]["canonicalized"] is True
    assert "missing_required_final_request_tools" not in {item["code"] for item in audit["context_quality"]["findings"]}


def test_final_request_context_audit_projects_authoritative_tool_registry_coverage() -> None:
    write_schema = ToolSpecRegistry.get_llm_schema(
        "write_file",
        include_arg_aliases=True,
        deterministic=True,
    )
    assert write_schema is not None
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.2, "max_tokens": 2000, "tools": [write_schema]},
        context={
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Write the declared target."},
            ],
            "required_tools": ["write_file"],
        },
    )
    prepared = PreparedLLMRequest(
        messages=list(ai_request.context["chat_messages"]),
        input_text="test",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )

    audit = build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    registry = audit["final_request_evidence_coverage"]["tool_schema_registry_coverage"]
    assert registry == {
        "registry_source": "polaris.kernelone.tool_execution.ToolSpecRegistry",
        "aliases_present": True,
        "arg_aliases_present": True,
        "schema_hash": context_audit_module._stable_digest([write_schema]),
        "missing_schema_tools": [],
    }


def test_final_request_context_audit_does_not_claim_registry_provenance_for_unknown_tool() -> None:
    unknown_schema = {
        "type": "function",
        "function": {
            "name": "unknown_mutation_tool",
            "description": "not registered",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.2, "max_tokens": 2000, "tools": [unknown_schema]},
        context={"chat_messages": [{"role": "system", "content": "You are Director."}]},
    )
    prepared = PreparedLLMRequest(
        messages=list(ai_request.context["chat_messages"]),
        input_text="test",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )

    audit = build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    registry = audit["final_request_evidence_coverage"]["tool_schema_registry_coverage"]
    assert registry["registry_source"] == ""
    assert registry["aliases_present"] is False
    assert registry["arg_aliases_present"] is False
    assert registry["missing_schema_tools"] == ["unknown_mutation_tool"]


def test_final_request_context_audit_separates_non_executable_provider_result_protocol() -> None:
    contract = RoleStructuredOutputContractV1(
        schema_name="chief_engineer_blueprint_portfolio",
        description="Submit the complete Chief Engineer blueprint portfolio.",
        json_schema={
            "type": "object",
            "properties": {
                "construction_plan": {"type": "object"},
                "scope_for_apply": {"type": "array"},
                "risk_flags": {"type": "array"},
            },
            "required": ["construction_plan", "scope_for_apply", "risk_flags"],
            "additionalProperties": False,
        },
    )
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    assert plan is not None
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="chief_engineer",
        input="",
        options={
            "temperature": 0.2,
            "max_tokens": 2000,
            "tools": [plan.tool_definition],
            "tool_choice": plan.tool_choice,
        },
        context={"chat_messages": [{"role": "system", "content": "You are Chief Engineer."}]},
    )
    prepared = PreparedLLMRequest(
        messages=list(ai_request.context["chat_messages"]),
        input_text="test",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
        structured_output_transport=plan,
    )

    audit = build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="chief_engineer", max_context_tokens=128_000),
    )

    coverage = audit["final_request_evidence_coverage"]
    protocol = coverage["provider_protocol_schema_coverage"]
    assert protocol["schema_version"] == "polaris.provider_protocol_schema_coverage.v1"
    assert protocol["active"] is True
    assert protocol["valid"] is True
    assert protocol["tool_name"] == STRUCTURED_OUTPUT_TOOL_NAME
    assert protocol["transport"] == "provider_tool"
    assert protocol["strict"] is True
    assert protocol["executable_tool"] is False
    assert protocol["side_effect"] is False
    assert protocol["tool_lifecycle"] is False
    assert protocol["tool_schema_hash"] == context_audit_module._stable_digest(plan.tool_definition)
    assert protocol["tool_choice_hash"] == context_audit_module._stable_digest(plan.tool_choice)
    registry = coverage["tool_schema_registry_coverage"]
    assert registry == {
        "registry_source": "",
        "aliases_present": False,
        "arg_aliases_present": False,
        "schema_hash": "",
        "missing_schema_tools": [],
    }
    assert ToolSpecRegistry.get_llm_schema(STRUCTURED_OUTPUT_TOOL_NAME) is None


def test_pm_route_probe_final_provider_request_has_no_tools() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="pm",
        input="",
        options={"temperature": 0.2, "max_tokens": 2000, "tools": [], "tool_choice": "none"},
        context={
            "mode": "pm_task_contract_route_probe",
            "deterministic_pm_contracts": True,
            "route_audit_probe": True,
            "task_id": "pm-route-probe",
            "pm_task_id": "pm-route-probe",
            "disable_internal_tool_rounds": True,
            "tool_contract_require_no_tool_calls": True,
            "require_no_tool_calls": True,
            "no_tool_calls": True,
            "tool_contract": {
                "require_no_tool_calls": True,
                "execution_mode": "text_only_probe",
                "source": "pm.route_audit_probe",
            },
            "_transaction_kernel_forced_tool_definitions": [],
            "_transaction_kernel_forced_tool_choice": "none",
            "chat_messages": [
                {"role": "system", "content": "You are PM."},
                {
                    "role": "user",
                    "content": "PM route audit probe for deterministic contract mode.",
                },
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are PM."},
            {
                "role": "user",
                "content": "PM route audit probe for deterministic contract mode.",
            },
        ],
        input_text="test",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )

    snapshot = build_final_provider_request_snapshot(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="pm", max_context_tokens=128_000),
    )

    assert snapshot["role"] == "pm"
    assert snapshot["tool_schema_count"] == 0
    assert snapshot["tools"] == []
    assert snapshot["tool_choice"] == "none"
    coverage = snapshot["final_request_evidence_coverage"]
    assert coverage["required_tools"] == []
    assert coverage["missing_required_tools"] == []


def test_final_request_context_audit_flags_under_applied_execution_strategy() -> None:
    execution_profile = {
        "schema_version": "task.execution_profile.v1",
        "source": "director.tasking",
        "task_type": "bugfix",
        "phase": "repair",
        "temperature": 0.05,
        "temperature_phase": "repair",
        "temperature_source": "task.execution_profile.v1",
    }
    execution_strategy = {
        "schema_version": "task.execution_strategy.v1",
        "source": "director.tasking",
        "temperature": 0.05,
        "temperature_phase": "repair",
        "output_budget_tokens": 96_000,
        "input_budget_tokens": 128_000,
        "prompt_max_chars": 512_000,
    }
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.7, "max_tokens": 4000},
        context={
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Fix src/app.py."},
            ],
            "director_execution_profile": execution_profile,
            "director_execution_strategy": execution_strategy,
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {"role": "user", "content": "Fix src/app.py."},
        ],
        input_text="You are Director.\nFix src/app.py.",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )

    audit = build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="director", max_context_tokens=1_000_000),
    )

    finding_codes = {item["code"] for item in audit["context_quality"]["findings"]}
    findings_by_code = {item["code"]: item for item in audit["context_quality"]["findings"]}
    assert audit["has_execution_strategy"] is True
    assert audit["request_metadata_summary"]["has_execution_strategy"] is True
    assert "execution_profile_temperature_mismatch" in finding_codes
    assert "execution_strategy_output_budget_under_applied" in finding_codes
    assert findings_by_code["execution_strategy_output_budget_under_applied"]["severity"] == "error"
    assert findings_by_code["execution_strategy_output_budget_under_applied"]["budget_ratio"] < 0.05


def test_final_request_context_audit_checks_execution_contract_when_strategy_missing() -> None:
    execution_contract = {
        "schema_version": "task.execution_contract.v1",
        "source": "director.tasking",
        "task_type": "bugfix",
        "phase": "repair",
        "sampling": {
            "temperature": 0.05,
            "temperature_phase": "repair",
            "sampling_mode": "deterministic_precise",
        },
        "context_budget": {
            "output_budget_tokens": 96_000,
            "input_budget_tokens": 128_000,
            "prompt_max_chars": 512_000,
        },
    }
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.7, "max_tokens": 4000},
        context={
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Fix src/app.py."},
            ],
            "task_execution_contract": execution_contract,
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {"role": "user", "content": "Fix src/app.py."},
        ],
        input_text="You are Director.\nFix src/app.py.",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )

    audit = build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="director", max_context_tokens=1_000_000),
    )

    finding_codes = {item["code"] for item in audit["context_quality"]["findings"]}
    findings_by_code = {item["code"]: item for item in audit["context_quality"]["findings"]}
    assert audit["has_execution_contract"] is True
    assert audit["has_execution_strategy"] is False
    assert "execution_profile_temperature_mismatch" in finding_codes
    assert "execution_strategy_output_budget_under_applied" in finding_codes
    assert findings_by_code["execution_strategy_output_budget_under_applied"]["contract_schema"] == (
        "task.execution_contract.v1"
    )
