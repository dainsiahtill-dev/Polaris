"""Tests for final provider request sampling audit metadata."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from polaris.cells.control_plane.run_ledger.public import FailureClassV1
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




def test_final_request_context_audit_includes_sampling_profile() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.05, "max_tokens": 4000},
        context={
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Fix the regression."},
            ],
            "director_execution_profile": {
                "schema_version": "task.execution_profile.v1",
                "source": "director.tasking",
                "task_type": "bugfix",
                "phase": "repair",
                "sampling_mode": "deterministic_precise",
                "temperature_phase": "repair",
                "temperature_source": "task.execution_profile.v1",
            },
            "task_execution_contract": {
                "schema_version": "task.execution_contract.v1",
                "source": "director.tasking",
                "task_type": "bugfix",
                "phase": "repair",
                "language": "python",
                "output_contract_id": "director.patch_file.v1",
                "sampling": {
                    "temperature": 0.05,
                    "temperature_phase": "repair",
                    "sampling_mode": "deterministic_precise",
                },
                "context_budget": {
                    "output_budget_tokens": 64000,
                    "input_budget_tokens": 96000,
                    "prompt_max_chars": 384000,
                },
                "delivery_contract": {
                    "primary_entities": ["planet", "weather"],
                    "rule_count": 3,
                    "edge_case_count": 2,
                    "level": 2,
                },
                "quality_contract": {
                    "quality_gates": ["pytest"],
                    "deterministic_checks": ["python_compile"],
                },
                "audit_contract": {"contract_hash": "contract-123"},
            },
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {"role": "user", "content": "Fix the regression."},
        ],
        input_text="You are Director.\nFix the regression.",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )

    audit = build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(max_context_tokens=8192),
    )

    assert audit["sampling"] == {
        "temperature": 0.05,
        "max_tokens": 4000,
        "temperature_source": "task.execution_profile.v1",
        "temperature_phase": "repair",
        "sampling_mode": "deterministic_precise",
        "task_type": "bugfix",
        "phase": "repair",
        "execution_profile_schema": "task.execution_profile.v1",
        "execution_profile_source": "director.tasking",
        "execution_contract_schema": "task.execution_contract.v1",
        "execution_contract_source": "director.tasking",
    }
    assert audit["has_execution_contract"] is True
    assert audit["execution_contract_summary"]["schema_version"] == "task.execution_contract.v1"
    assert audit["execution_contract_summary"]["delivery_contract"]["primary_entities"] == ["planet", "weather"]
    assert audit["execution_contract_summary"]["audit_contract"]["contract_hash"] == "contract-123"
    assert audit["execution_contract_hash"]


def test_final_provider_request_snapshot_preserves_alias_expanded_forced_tool_schema() -> None:
    tool_schema = {
        "type": "function",
        "function": {
            "name": "write_file",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "path": {"type": "string"},
                    "targetPath": {"type": "string"},
                    "content": {"type": "string"},
                    "body": {"type": "string"},
                    "newText": {"type": "string"},
                },
                "required": [],
            },
        },
    }
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={
            "temperature": 0.0,
            "max_tokens": 4000,
            "tools": [tool_schema],
            "tool_choice": {"type": "function", "function": {"name": "write_file"}},
        },
        context={
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Materialize the requested file."},
            ],
            "_transaction_kernel_forced_tool_definitions": [tool_schema],
            "_transaction_kernel_forced_tool_choice": {"type": "function", "function": {"name": "write_file"}},
            "required_tools": ["write_file"],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {"role": "user", "content": "Materialize the requested file."},
        ],
        input_text="You are Director.\nMaterialize the requested file.",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )

    snapshot = build_final_provider_request_snapshot(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    assert snapshot["tool_schema_count"] == 1
    assert snapshot["tools"][0]["name"] == "write_file"
    assert {"file", "path", "targetPath", "content", "body", "newText"} <= set(snapshot["tools"][0]["argument_keys"])
    assert snapshot["final_request_evidence_coverage"]["missing_required_tools"] == []


def test_final_request_context_audit_flags_empty_run_card_message() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="chief_engineer",
        input="",
        options={"temperature": 0.2, "max_tokens": 8000},
        context={
            "chat_messages": [
                {"role": "system", "content": "You are Chief Engineer."},
                {"role": "system", "name": "run_card", "content": "【Run Card】"},
                {"role": "user", "content": "Produce the construction blueprint."},
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Chief Engineer."},
            {"role": "system", "name": "run_card", "content": "【Run Card】"},
            {"role": "user", "content": "Produce the construction blueprint."},
        ],
        input_text="You are Chief Engineer.\n【Run Card】\nProduce the construction blueprint.",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )

    audit = build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="chief_engineer", max_context_tokens=128_000),
    )

    finding_codes = {item["code"] for item in audit["context_quality"]["findings"]}
    assert "empty_run_card_message" in finding_codes


def test_final_request_context_audit_does_not_treat_role_name_as_ce_blueprint() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="pm",
        input="",
        options={"temperature": 0.2, "max_tokens": 4000},
        context={
            "chat_messages": [
                {"role": "system", "content": "You are PM in the PM -> Chief Engineer -> Director chain."},
                {
                    "role": "user",
                    "content": (
                        "Plan a JavaScript project. The Chief Engineer will create a blueprint later, "
                        "but no CE handoff evidence exists yet."
                    ),
                },
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are PM in the PM -> Chief Engineer -> Director chain."},
            {
                "role": "user",
                "content": (
                    "Plan a JavaScript project. The Chief Engineer will create a blueprint later, "
                    "but no CE handoff evidence exists yet."
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
        profile=SimpleNamespace(role_id="pm", max_context_tokens=128_000),
    )

    assert audit["coverage"]["has_chief_engineer_blueprint"] is False


def test_final_request_context_audit_does_not_treat_ce_output_contract_as_existing_blueprint() -> None:
    content = (
        "You are Chief Engineer. Return exactly one JSON object. "
        "Required keys: construction_plan, scope_for_apply, risk_flags. "
        "Task target_files: src/engine/rules.js and src/engine/runner.js."
    )
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="chief_engineer",
        input="",
        options={"temperature": 0.2, "max_tokens": 4000},
        context={"chat_messages": [{"role": "system", "content": content}]},
    )
    prepared = PreparedLLMRequest(
        messages=[{"role": "system", "content": content}],
        input_text="test",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )

    audit = build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="chief_engineer", max_context_tokens=128_000),
    )

    assert audit["coverage"]["has_chief_engineer_blueprint"] is False


def test_final_provider_snapshot_includes_execution_profile_summary_and_hash() -> None:
    execution_profile = {
        "schema_version": "task.execution_profile.v1",
        "source": "director.tasking",
        "task_type": "implement",
        "phase": "materialize",
        "project_type": "frontend_app",
        "language": "typescript",
        "runtime": "browser",
        "framework": "react",
        "sampling_mode": "precise",
        "temperature_phase": "implementation",
        "temperature_source": "task.execution_profile.v1",
        "output_contract_id": "director.patch_file.v1",
        "target_files": ["src/App.tsx"],
        "selected_libraries": [{"name": "react"}],
    }
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={
            "temperature": 0.15,
            "max_tokens": 5000,
            "reasoning_budget_tokens": 2_048,
            "response_format": {"type": "json_object"},
        },
        context={
            "mode": "chat",
            "native_tool_mode": "native_tools",
            "response_format_mode": "native_json_schema",
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Implement src/App.tsx."},
            ],
            "director_execution_profile": execution_profile,
            "task_metadata": {"task_id": "task-1", "pm_task_id": "pm-1"},
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {"role": "user", "content": "Implement src/App.tsx."},
        ],
        input_text="You are Director.\nImplement src/App.tsx.",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )

    snapshot = build_final_provider_request_snapshot(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="director", max_context_tokens=32768),
    )

    metadata_summary = snapshot["request_metadata_summary"]
    assert metadata_summary["reasoning_budget_tokens"] == 2_048
    assert metadata_summary["schema_version"] == "llm.request_metadata_summary.v1"
    assert metadata_summary["has_execution_profile"] is True
    assert metadata_summary["has_language_guidance"] is True
    assert metadata_summary["has_output_contract"] is True
    assert metadata_summary["execution_profile_hash"]
    assert metadata_summary["task_metadata_hash"]
    assert metadata_summary["execution_profile_summary"] == {
        "schema_version": "task.execution_profile.v1",
        "source": "director.tasking",
        "task_type": "implement",
        "phase": "materialize",
        "project_type": "frontend_app",
        "language": "typescript",
        "runtime": "browser",
        "framework": "react",
        "sampling_mode": "precise",
        "temperature_phase": "implementation",
        "temperature_source": "task.execution_profile.v1",
        "output_contract_id": "director.patch_file.v1",
        "target_files_count": 1,
        "selected_libraries_count": 1,
    }
    assert snapshot["sampling"]["max_tokens"] == 5000
    assert (
        snapshot["final_request_context_audit"]["execution_profile_hash"] == metadata_summary["execution_profile_hash"]
    )
    assert snapshot["final_request_context_audit"]["has_language_guidance"] is True
    assert snapshot["final_request_context_audit"]["has_output_contract"] is True


def test_final_request_context_audit_includes_execution_envelope_coverage() -> None:
    execution_profile = {
        "schema_version": "task.execution_profile.v1",
        "source": "director.tasking",
        "task_type": "implement",
        "phase": "materialize",
        "language": "python",
        "target_files": ["src/main.py"],
    }
    execution_strategy = {
        "schema_version": "task.execution_strategy.v1",
        "source": "director.tasking",
        "temperature": 0.1,
        "temperature_phase": "implementation",
        "output_budget_tokens": 48000,
        "input_budget_tokens": 48000,
        "prompt_max_chars": 192000,
        "evidence_requirements": [
            "pm_task_contract",
            "chief_engineer_blueprint",
            "target_files_or_declared_scopes",
            "execution_envelope",
        ],
    }
    execution_envelope = {
        "schema_version": "polaris.execution_envelope.v1",
        "run_id": "run-1",
        "task_id": "TASK-1",
        "trace_id": "trace-1",
        "pm_contract": {"ref": "tasks/plan.json", "hash": "pm-hash"},
        "ce_blueprint": {"ref": "runtime/contracts/ce.json", "hash": "ce-hash"},
        "handoff_decision": {"ref": "runtime/contracts/handoff.json", "hash": "handoff-hash", "allowed": True},
        "execution_profile": {"ref": "runtime/contracts/profile.json", "hash": "profile-hash"},
        "authorization": {
            "target_files": ["src/main.py"],
            "scope_paths": ["src/main.py"],
            "allowed_write_paths": ["src/main.py"],
        },
        "audit_policy": {
            "required_evidence": [
                "pm_task_contract",
                "chief_engineer_blueprint",
                "target_files_or_declared_scopes",
            ]
        },
        "budget_policy": {"output_budget_tokens": 48000},
        "envelope_hash": "envelope-hash",
    }
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 48000},
        context={
            "chat_messages": [
                {
                    "role": "user",
                    "content": (
                        "PM Task Contract / 任务合同: TASK-1 target_files src/main.py "
                        "Acceptance criteria / 验收标准: python src/main.py. "
                        "Chief Engineer Blueprint / CE 蓝图交接: blueprint_id ce_TASK-1 "
                        "construction_plan scope_for_apply."
                    ),
                }
            ],
            "director_execution_profile": execution_profile,
            "director_execution_strategy": execution_strategy,
            "director_execution_envelope": execution_envelope,
            "execution_envelope_hash": "envelope-hash",
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "task_id": "TASK-1",
                "target_files": ["src/main.py"],
                "acceptance": ["python src/main.py"],
            },
            "ce_blueprint": {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": "ce_TASK-1",
                "target_files": ["src/main.py"],
                "construction_plan": {"phase": "implement"},
            },
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "user",
                "content": (
                    "PM Task Contract / 任务合同: TASK-1 target_files src/main.py "
                    "Chief Engineer Blueprint / CE 蓝图交接: blueprint_id ce_TASK-1 construction_plan scope_for_apply."
                ),
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
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    evidence_coverage = audit["final_request_evidence_coverage"]
    assert audit["has_execution_envelope"] is True
    assert audit["execution_envelope_hash"] == "envelope-hash"
    assert evidence_coverage["workflow_chain"] == {
        "pm_contract_hash": "pm-hash",
        "ce_blueprint_hash": "ce-hash",
        "handoff_decision_hash": "handoff-hash",
        "execution_profile_hash": audit["execution_profile_hash"],
        "execution_envelope_hash": "envelope-hash",
    }
    assert evidence_coverage["missing_required_refs"] == []
    assert evidence_coverage["pass"] is True


def test_final_request_evidence_tracks_module_interface_contract() -> None:
    direct_exports = _actual_sibling_exports_v2(
        path="src/models/weather.py",
        body="class WeatherReport:\n    pass\n\ndef forecast_for():\n    return WeatherReport()\n",
    )
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 48000},
        context={
            "director_execution_strategy": {
                "schema_version": "task.execution_strategy.v1",
                "evidence_requirements": [
                    "pm_task_contract",
                    "chief_engineer_blueprint",
                    "target_files_or_declared_scopes",
                    "module_interface_contract",
                    "actual_sibling_exports",
                ],
            },
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "envelope-hash",
                "pm_contract": {"hash": "pm-hash"},
                "ce_blueprint": {"hash": "ce-hash"},
                "authorization": {
                    "target_files": ["src/models/weather.py", "src/engine/forecast.py"],
                    "scope_paths": ["src/models/weather.py", "src/engine/forecast.py"],
                },
            },
            "ce_blueprint": {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": "ce_TASK-1",
                "module_interface_contract": {
                    "schema_version": "chief_engineer.module_interface_contract.v1",
                    "source": "chief_engineer.generate_task_blueprint",
                    "authority": "handoff_guidance_not_scope_authority",
                    "language": "python",
                    "actual_interface_snapshot_sources": ["workspace_symbol_index"],
                    "actual_interface_snapshot_file_count": 1,
                    "modules": [
                        {
                            "path": "src/models/weather.py",
                            "role": "domain_model",
                            "actual_public_symbols": ["WeatherReport", "forecast_for"],
                            "planned_public_symbols": ["WeatherReport", "forecast_for"],
                            "symbol_source": "actual_export_summary",
                        },
                        {
                            "path": "src/engine/forecast.py",
                            "role": "core_engine",
                            "planned_public_symbols": ["ForecastEngine"],
                        },
                    ],
                    "rules": ["Every symbol imported from a sibling target module must be defined by that module."],
                },
            },
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "task_id": "TASK-1",
                "target_files": ["src/models/weather.py", "src/engine/forecast.py"],
            },
            "actual_sibling_exports": direct_exports,
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "system",
                "content": (
                    "PM Task Contract / 任务合同: TASK-1 target_files src/models/weather.py, "
                    "src/engine/forecast.py. Chief Engineer Blueprint / CE 蓝图交接: "
                    "blueprint_id ce_TASK-1 construction_plan.\n" + _actual_sibling_exports_message(direct_exports)
                ),
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
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    assert audit["coverage"]["has_module_interface_contract"] is True
    evidence_coverage = audit["final_request_evidence_coverage"]
    assert "module_interface_contract" in evidence_coverage["required_refs"]
    assert "module_interface_contract" in evidence_coverage["included_refs"]
    assert "actual_sibling_exports" in evidence_coverage["required_refs"]
    assert "actual_sibling_exports" in evidence_coverage["included_refs"]
    assert evidence_coverage["structured_evidence"]["module_interface_contract"] is True
    assert evidence_coverage["structured_evidence"]["actual_sibling_exports"] is True
    assert evidence_coverage["missing_required_refs"] == []
    assert evidence_coverage["pass"] is True
    metadata = audit["request_metadata_summary"]
    assert metadata["module_interface_contract_summary"]["actual_export_module_count"] == 1
    assert metadata["actual_sibling_exports_summary"]["module_count"] == 1
    module_slot = next(
        item for item in evidence_coverage["evidence_slots"] if item["ref_type"] == "module_interface_contract"
    )
    exports_slot = next(
        item for item in evidence_coverage["evidence_slots"] if item["ref_type"] == "actual_sibling_exports"
    )
    assert module_slot["details"]["module_count"] == 2
    assert module_slot["details"]["actual_export_module_count"] == 1
    assert exports_slot["details"]["module_count"] == 1


def test_final_request_evidence_tracks_direct_actual_sibling_exports_payload() -> None:
    direct_exports = _actual_sibling_exports_v2(
        path="src/models/stall.ts",
        body="export class Stall {}\nexport function createStall() { return new Stall(); }\n",
    )
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 48000},
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
            "ce_blueprint": {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": "ce_TASK-2",
                "construction_plan": {"phase": "implement"},
                "target_files": ["src/main.ts"],
            },
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "task_id": "TASK-2",
                "target_files": ["src/main.ts"],
            },
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "envelope-hash",
                "pm_contract": {"hash": "pm-hash"},
                "ce_blueprint": {"hash": "ce-hash"},
                "authorization": {
                    "target_files": ["src/main.ts"],
                    "scope_paths": ["src/main.ts"],
                },
            },
            "actual_sibling_exports": direct_exports,
            "metadata": {"actual_sibling_exports": direct_exports},
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "system",
                "content": (
                    "PM Task Contract / 任务合同: TASK-2 target_files src/main.ts. "
                    "Chief Engineer Blueprint / CE 蓝图交接: blueprint_id ce_TASK-2.\n"
                    + _actual_sibling_exports_message(direct_exports)
                ),
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
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    evidence_coverage = audit["final_request_evidence_coverage"]
    assert audit["coverage"]["has_actual_sibling_exports"] is True
    assert "actual_sibling_exports" in evidence_coverage["included_refs"]
    assert evidence_coverage["structured_evidence"]["actual_sibling_exports"] is True
    assert evidence_coverage["missing_required_refs"] == []
    assert audit["request_metadata_summary"]["actual_sibling_exports_summary"] == direct_exports


def test_final_request_rejects_legacy_schema_only_actual_sibling_payload() -> None:
    payload: dict[str, object] = {
        "schema_version": "polaris.actual_sibling_exports.evidence.v1",
        "modules": [{"path": "src/models/flavor.rs"}],
        "module_count": 1,
    }

    audit = _audit_required_actual_sibling_payload(
        payload,
        message="Actual workspace physical source evidence: src/models/flavor.rs.",
    )

    evidence = audit["final_request_evidence_coverage"]
    assert "actual_sibling_exports" in evidence["missing_required_refs"]
    assert evidence["pass"] is False
    assert audit["coverage"]["has_actual_sibling_exports"] is False


def test_final_request_rejects_actual_sibling_payload_covering_wrong_parent() -> None:
    payload = _actual_sibling_exports_v2(dependency_task_id="1")
    payload["covered_parent_task_ids"] = ["9"]
    payload_without_hash = dict(payload)
    payload_without_hash.pop("snapshot_sha256")
    payload["snapshot_sha256"] = hashlib.sha256(
        json.dumps(payload_without_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    audit = _audit_required_actual_sibling_payload(
        payload,
        message=_actual_sibling_exports_message(payload),
    )

    evidence = audit["final_request_evidence_coverage"]
    assert "actual_sibling_exports" in evidence["missing_required_refs"]
    assert evidence["pass"] is False


def test_final_request_rejects_tampered_actual_sibling_body_hash() -> None:
    payload = _actual_sibling_exports_v2()
    modules = payload["modules"]
    assert isinstance(modules, list)
    module = modules[0]
    assert isinstance(module, dict)
    module["body"] = "pub enum Invented { Wrong }\n"
    payload_without_hash = dict(payload)
    payload_without_hash.pop("snapshot_sha256")
    payload["snapshot_sha256"] = hashlib.sha256(
        json.dumps(payload_without_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    audit = _audit_required_actual_sibling_payload(
        payload,
        message=_actual_sibling_exports_message(payload),
    )

    evidence = audit["final_request_evidence_coverage"]
    assert "actual_sibling_exports" in evidence["missing_required_refs"]
    assert evidence["pass"] is False


def test_final_request_rejects_actual_sibling_metadata_without_message_body() -> None:
    payload = _actual_sibling_exports_v2()

    audit = _audit_required_actual_sibling_payload(
        payload,
        message=_actual_sibling_exports_message(payload, include_body=False),
    )

    evidence = audit["final_request_evidence_coverage"]
    assert "actual_sibling_exports" in evidence["missing_required_refs"]
    assert evidence["pass"] is False


def test_final_request_evidence_reports_missing_module_interface_contract() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 48000},
        context={
            "director_execution_strategy": {
                "schema_version": "task.execution_strategy.v1",
                "evidence_requirements": [
                    "pm_task_contract",
                    "chief_engineer_blueprint",
                    "target_files_or_declared_scopes",
                    "module_interface_contract",
                ],
            },
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "envelope-hash",
                "pm_contract": {"hash": "pm-hash"},
                "ce_blueprint": {"hash": "ce-hash"},
                "authorization": {
                    "target_files": ["src/models/Fairy.ts"],
                    "scope_paths": ["src/models/Fairy.ts"],
                },
            },
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "task_id": "TASK-1",
                "target_files": ["src/models/Fairy.ts"],
            },
            "ce_blueprint": {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": "ce_TASK-1",
                "target_files": ["src/models/Fairy.ts"],
                "construction_plan": {"phase": "quality_repair"},
            },
            "failed_gate_evidence": {
                "schema_version": "polaris.failed_gate_evidence.v1",
                "source": "run_ledger.verifier",
                "command": "npm run test",
                "exit_code": 1,
                "diagnostics": [{"code": "E_ASSERT", "path": "tests/product.test.js"}],
            },
            "workspace_quality_evidence": {
                "schema_version": "polaris.workspace_quality_evidence.v1",
                "source": "factory_workspace_quality",
                "all_checks_passed": False,
                "quality_errors": [{"code": "behavior_assertion", "path": "tests/product.test.js"}],
                "failed_required_modalities": ["command"],
            },
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "system",
                "content": (
                    "PM Task Contract / 任务合同: TASK-1 target_files src/models/weather.py, "
                    "src/engine/forecast.py. Chief Engineer Blueprint / CE 蓝图交接: "
                    "blueprint_id ce_TASK-1 construction_plan."
                ),
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
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    assert audit["coverage"]["has_module_interface_contract"] is False
    evidence_coverage = audit["final_request_evidence_coverage"]
    assert "module_interface_contract" in evidence_coverage["missing_required_refs"]
    assert evidence_coverage["pass"] is False


def test_final_request_evidence_rejects_text_only_interface_contract_keywords() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 48000},
        context={
            "director_execution_strategy": {
                "schema_version": "task.execution_strategy.v1",
                "evidence_requirements": [
                    "module_interface_contract",
                    "actual_sibling_exports",
                ],
            },
            "chat_messages": [
                {
                    "role": "system",
                    "content": (
                        "Text only: module_interface_contract public_symbols "
                        "actual_public_symbols cross_file_interface_contract."
                    ),
                },
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "system",
                "content": (
                    "Text only: module_interface_contract public_symbols "
                    "actual_public_symbols cross_file_interface_contract."
                ),
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
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    assert audit["coverage"]["has_module_interface_contract"] is False
    assert audit["coverage"]["has_actual_sibling_exports"] is False
    evidence_coverage = audit["final_request_evidence_coverage"]
    assert evidence_coverage["structured_evidence"]["module_interface_contract"] is False
    assert evidence_coverage["structured_evidence"]["actual_sibling_exports"] is False
    assert "module_interface_contract" in evidence_coverage["missing_required_refs"]
    assert "actual_sibling_exports" in evidence_coverage["missing_required_refs"]


def test_final_request_evidence_aliases_verification_failure_and_architecture_plan() -> None:
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
                    "failed_gate_or_verification_evidence",
                    "architecture_or_file_plan",
                    "execution_envelope",
                ],
            },
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "envelope-hash",
                "pm_contract": {"hash": "pm-hash"},
                "ce_blueprint": {"hash": "ce-hash"},
                "authorization": {
                    "target_files": ["src/models/Fairy.ts"],
                    "scope_paths": ["src/models/Fairy.ts"],
                },
            },
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "task_id": "TASK-1",
                "target_files": ["src/models/Fairy.ts"],
            },
            "ce_blueprint": {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": "ce_TASK-1",
                "target_files": ["src/models/Fairy.ts"],
                "construction_plan": {"phase": "quality_repair"},
            },
            "failed_gate_evidence": {
                "schema_version": "polaris.failed_gate_evidence.v1",
                "source": "run_ledger.verifier",
                "command": "npm run test",
                "exit_code": 1,
                "diagnostics": [{"code": "E_ASSERT", "path": "tests/product.test.js"}],
            },
            "workspace_quality_evidence": {
                "schema_version": "polaris.workspace_quality_evidence.v1",
                "source": "factory_workspace_quality",
                "all_checks_passed": False,
                "quality_errors": [{"code": "behavior_assertion", "path": "tests/product.test.js"}],
                "failed_required_modalities": ["command"],
            },
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "system",
                "content": (
                    "PM Task Contract / 任务合同: TASK-1 target_files src/models/Fairy.ts. "
                    "Chief Engineer Blueprint / CE 蓝图交接: blueprint_id ce_TASK-1 construction_plan. "
                    "MATERIALIZATION QUALITY REPAIR MODE. Quality errors: step verify failed "
                    "(exit 1): npm run test :: failure excerpt: not ok 2 - Fairy mood starts cheerful."
                ),
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
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    evidence_coverage = audit["final_request_evidence_coverage"]
    assert evidence_coverage["missing_required_refs"] == []
    assert evidence_coverage["pass"] is True
    assert "failed_gate_evidence" in evidence_coverage["included_refs"]
    assert "ce_blueprint" in evidence_coverage["included_refs"]
    assert "architecture_or_file_plan" in evidence_coverage["required_refs"]
    assert "architecture_or_file_plan" in evidence_coverage["included_refs"]
    assert audit["coverage"]["has_workspace_quality_evidence"] is True
    assert audit["coverage"]["has_architecture_or_file_plan"] is True


def test_first_pass_tests_accept_actual_parent_artifacts_without_failed_gate() -> None:
    direct_exports = _actual_sibling_exports_v2()
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
                    "target_files": ["tests/product.rs", "README.md"],
                    "scope_paths": ["tests/product.rs", "README.md"],
                },
            },
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "task_id": "TASK-3",
                "target_files": ["tests/product.rs", "README.md"],
            },
            "ce_blueprint": {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": "ce_TASK-3",
                "target_files": ["tests/product.rs", "README.md"],
                "construction_plan": {"phase": "verification"},
            },
            "target_files": ["tests/product.rs", "README.md"],
            "actual_sibling_exports": direct_exports,
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "system",
                "content": (
                    "PM Task Contract / 任务合同: TASK-3 tests/product.rs README.md. "
                    "Chief Engineer Blueprint / CE 蓝图交接: ce_TASK-3 verification. "
                    + _actual_sibling_exports_message(direct_exports)
                ),
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
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    evidence_coverage = audit["final_request_evidence_coverage"]
    assert evidence_coverage["pass"] is True, evidence_coverage["missing_required_refs"]
    assert evidence_coverage["missing_required_refs"] == []
    assert "actual_sibling_exports" in evidence_coverage["required_refs"]
    assert "actual_sibling_exports" in evidence_coverage["included_refs"]
    assert "failed_gate_evidence" not in evidence_coverage["required_refs"]
    assert audit["coverage"]["has_actual_sibling_exports"] is True


def test_final_request_evidence_role_defaults_use_canonical_ref_helper(monkeypatch) -> None:
    original_ref_helper = context_audit_module.final_request_evidence_ref_for_requirement
    observed_requirements: list[str] = []

    def recording_ref_helper(value: object) -> str:
        observed_requirements.append(str(value))
        return original_ref_helper(value)

    monkeypatch.setattr(
        context_audit_module,
        "final_request_evidence_ref_for_requirement",
        recording_ref_helper,
    )

    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 4000},
        context={
            "director_execution_profile": {
                "schema_version": "task.execution_profile.v1",
                "source": "director.tasking",
            },
            "director_execution_strategy": {
                "schema_version": "task.execution_strategy.v1",
                "source": "director.tasking",
            },
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "envelope-hash",
            },
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "task_id": "TASK-1",
                "target_files": ["src/main.py"],
            },
            "ce_blueprint": {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": "ce_TASK-1",
                "target_files": ["src/main.py"],
                "construction_plan": {"phase": "implement"},
            },
            "interface_discrepancy_context": {
                "schema_version": "polaris.interface_discrepancy_context.evidence.v1",
                "diagnostics": [{"symbol": "WeatherKind"}],
            },
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Implement TASK-1."},
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {"role": "user", "content": "Implement TASK-1."},
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
    assert {
        "execution_profile",
        "execution_strategy",
        "execution_envelope",
        "interface_discrepancy_context",
    } <= set(observed_requirements)
    assert evidence_coverage["required_refs"] == [
        "pm_contract",
        "ce_blueprint",
        "target_files",
        "execution_profile",
        "execution_strategy",
        "execution_envelope",
        "interface_discrepancy_context",
    ]


def test_final_request_evidence_accepts_structured_failure_and_quality_slots_without_keywords() -> None:
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
                    "failed_gate_or_verification_evidence",
                    "workspace_quality_evidence",
                    "execution_envelope",
                ],
            },
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "envelope-hash",
                "pm_contract": {"hash": "pm-hash"},
                "ce_blueprint": {"hash": "ce-hash"},
                "authorization": {
                    "target_files": ["src/models/Fairy.ts"],
                    "scope_paths": ["src/models/Fairy.ts"],
                },
            },
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "task_id": "TASK-1",
                "target_files": ["src/models/Fairy.ts"],
            },
            "ce_blueprint": {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": "ce_TASK-1",
                "target_files": ["src/models/Fairy.ts"],
            },
            "failed_gate_evidence": {
                "schema_version": "polaris.failed_gate_evidence.v1",
                "source": "run_ledger.verifier",
                "command": "npm test",
                "exit_code": 1,
                "diagnostics": [{"code": "E_ASSERT", "path": "tests/product.test.js"}],
                "failed_checks": ["behavior"],
            },
            "workspace_quality_evidence": {
                "schema_version": "polaris.workspace_quality_evidence.v1",
                "source": "factory_workspace_quality",
                "all_checks_passed": False,
                "quality_errors": [{"code": "behavior_assertion"}],
                "deterministic_checks": ["js_syntax"],
                "failed_required_modalities": ["command"],
            },
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "system",
                "content": "PM Task Contract / 任务合同: TASK-1 target_files src/models/Fairy.ts. CE blueprint ready.",
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
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    assert audit["coverage"]["has_failure_feedback"] is True
    assert audit["coverage"]["has_workspace_quality_evidence"] is True
    metadata_summary = audit["request_metadata_summary"]
    assert metadata_summary["has_failed_gate_evidence"] is True
    assert metadata_summary["failed_gate_evidence_summary"]["diagnostic_count"] == 1
    assert metadata_summary["has_workspace_quality_evidence"] is True
    assert metadata_summary["workspace_quality_evidence_summary"]["quality_error_count"] == 1

    evidence_coverage = audit["final_request_evidence_coverage"]
    assert evidence_coverage["missing_required_refs"] == []
    assert evidence_coverage["pass"] is True
    assert evidence_coverage["structured_evidence"]["failed_gate_evidence"] is True
    assert evidence_coverage["structured_evidence"]["workspace_quality_evidence"] is True
    for ref_type in ("failed_gate_evidence", "workspace_quality_evidence"):
        source = next(item for item in evidence_coverage["coverage_sources"] if item["ref_type"] == ref_type)
        assert source["confidence"] == "structured_metadata"
        assert source["hash"]
        slot = next(item for item in evidence_coverage["evidence_slots"] if item["ref_type"] == ref_type)
        assert slot["confidence"] == "structured_metadata"
        assert slot["hash"] == source["hash"]
        assert slot["details"]
    enforce_final_request_evidence_coverage(ai_request=ai_request, audit=audit)


def test_final_request_evidence_accepts_context_evidence_slots_without_keywords() -> None:
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
                    "failure_evidence",
                    "quality_evidence",
                    "execution_envelope",
                ],
            },
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "envelope-hash",
                "pm_contract": {"hash": "pm-hash"},
                "ce_blueprint": {"hash": "ce-hash"},
                "authorization": {
                    "target_files": ["src/models/Fairy.ts"],
                    "scope_paths": ["src/models/Fairy.ts"],
                },
            },
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "task_id": "TASK-1",
                "target_files": ["src/models/Fairy.ts"],
            },
            "ce_blueprint": {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": "ce_TASK-1",
                "target_files": ["src/models/Fairy.ts"],
            },
            "context_evidence_slots": [
                {
                    "schema_version": "polaris.context_evidence_slot.v1",
                    "ref_type": "failure_evidence",
                    "payload": {
                        "schema_version": "polaris.verifier_failure_evidence.v1",
                        "source": "run_ledger.verifier",
                        "command": "node --test",
                        "exit_code": 1,
                        "diagnostics": [{"code": "ASSERTION", "path": "tests/product.test.js"}],
                    },
                },
                {
                    "schema_version": "polaris.context_evidence_slot.v1",
                    "ref_type": "quality_evidence",
                    "payload": {
                        "schema_version": "polaris.workspace_quality_evidence.v1",
                        "source": "artifact_quality",
                        "all_checks_passed": False,
                        "quality_errors": [{"code": "ASSERTION"}],
                        "deterministic_checks": ["js_syntax"],
                    },
                },
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "system",
                "content": "PM Task Contract / 任务合同: TASK-1 target_files src/models/Fairy.ts. CE blueprint ready.",
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
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    evidence_coverage = audit["final_request_evidence_coverage"]
    assert "failed_gate_evidence" in evidence_coverage["required_refs"]
    assert "workspace_quality_evidence" in evidence_coverage["required_refs"]
    assert "failed_gate_evidence" in evidence_coverage["included_refs"]
    assert "workspace_quality_evidence" in evidence_coverage["included_refs"]
    assert evidence_coverage["missing_required_refs"] == []
    assert evidence_coverage["structured_evidence"]["failed_gate_evidence"] is True
    assert evidence_coverage["structured_evidence"]["failure_evidence"] is True
    assert evidence_coverage["structured_evidence"]["workspace_quality_evidence"] is True
    assert evidence_coverage["structured_evidence"]["quality_evidence"] is True
    assert audit["request_metadata_summary"]["failed_gate_evidence_summary"]["diagnostic_count"] == 1
    assert audit["request_metadata_summary"]["workspace_quality_evidence_summary"]["quality_error_count"] == 1
    enforce_final_request_evidence_coverage(ai_request=ai_request, audit=audit)


def test_final_request_evidence_accepts_run_ledger_failure_evidence_without_keywords() -> None:
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
                    "failed_gate_or_verification_evidence",
                    "execution_envelope",
                ],
            },
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "envelope-hash",
                "pm_contract": {"hash": "pm-hash"},
                "ce_blueprint": {"hash": "ce-hash"},
                "authorization": {
                    "target_files": ["src/models/Fairy.ts"],
                    "scope_paths": ["src/models/Fairy.ts"],
                },
            },
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "task_id": "TASK-1",
                "target_files": ["src/models/Fairy.ts"],
            },
            "ce_blueprint": {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": "ce_TASK-1",
                "target_files": ["src/models/Fairy.ts"],
            },
            "run_ledger_projection": {
                "schema_version": "polaris.run_ledger_projection.v1",
                "failure_evidence": [
                    {
                        "schema_version": "polaris.failure_evidence.v1",
                        "source": "tool_lifecycle",
                        "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
                        "responsible_layer": "platform",
                        "repairable_by_director": False,
                        "requires_ce_replan": False,
                        "requires_pm_revision": False,
                        "evidence_refs": ["tool_lifecycle:turn-1"],
                    },
                    {
                        "schema_version": "polaris.failure_evidence.v1",
                        "source": "tool_lifecycle",
                        "failure_class": FailureClassV1.MISSING_EFFECT_RECEIPT.value,
                        "responsible_layer": "tool_executor",
                        "repairable_by_director": True,
                        "requires_ce_replan": False,
                        "requires_pm_revision": False,
                        "evidence_refs": ["effect_receipt:missing-1"],
                    },
                ],
            },
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "system",
                "content": "Contract TASK-1 covers src/models/Fairy.ts. Blueprint ce_TASK-1 is attached.",
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
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    metadata_summary = audit["request_metadata_summary"]
    assert metadata_summary["has_failed_gate_evidence"] is True
    assert (
        metadata_summary["failed_gate_evidence_summary"]["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    )
    assert metadata_summary["failed_gate_evidence_summary"]["failure_classes"] == [
        FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        FailureClassV1.MISSING_EFFECT_RECEIPT.value,
    ]
    assert metadata_summary["failed_gate_evidence_summary"]["failure_evidence_count"] == 2
    assert metadata_summary["failed_gate_evidence_summary"]["responsible_layer"] == "platform"
    assert metadata_summary["failed_gate_evidence_summary"]["evidence_refs"] == [
        "tool_lifecycle:turn-1",
        "effect_receipt:missing-1",
    ]

    evidence_coverage = audit["final_request_evidence_coverage"]
    assert evidence_coverage["missing_required_refs"] == []
    assert evidence_coverage["pass"] is True
    assert evidence_coverage["structured_evidence"]["failed_gate_evidence"] is True
    enforce_final_request_evidence_coverage(ai_request=ai_request, audit=audit)


def test_final_request_evidence_accepts_structured_architecture_plan_without_keywords() -> None:
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
                    "architecture_or_file_plan",
                ],
            },
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "envelope-hash",
                "pm_contract": {"hash": "pm-hash"},
                "ce_blueprint": {"hash": "ce-hash"},
            },
            "ce_blueprint": {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": "ce_TASK-1",
                "target_files": ["src/main.ts"],
                "execution_checklist": ["Implement src/main.ts against the PM contract."],
                "architecture_decisions": [
                    {
                        "concern": "entrypoint",
                        "decision": "Keep CLI wiring in src/main.ts and domain logic in src/models.",
                    }
                ],
            },
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "task_id": "TASK-1",
                "target_files": ["src/main.ts"],
            },
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "system",
                "content": (
                    "PM Task Contract / 任务合同: TASK-1 target_files src/main.ts. "
                    "Chief Engineer Blueprint / CE 蓝图交接: blueprint_id ce_TASK-1."
                ),
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
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    evidence_coverage = audit["final_request_evidence_coverage"]
    assert audit["has_architecture_or_file_plan"] is True
    assert audit["coverage"]["has_architecture_or_file_plan"] is True
    assert "architecture_or_file_plan" in evidence_coverage["included_refs"]
    assert evidence_coverage["structured_evidence"]["architecture_or_file_plan"] is True
    assert evidence_coverage["missing_required_refs"] == []
    assert evidence_coverage["pass"] is True


def test_final_request_evidence_rejects_text_only_architecture_plan_keywords() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 4000},
        context={
            "director_execution_strategy": {
                "schema_version": "task.execution_strategy.v1",
                "evidence_requirements": ["architecture_or_file_plan"],
            },
            "chat_messages": [
                {
                    "role": "system",
                    "content": "Text only construction_plan file plan scope_for_apply module_boundaries.",
                },
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "system",
                "content": "Text only construction_plan file plan scope_for_apply module_boundaries.",
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
        profile=SimpleNamespace(role_id="director", max_context_tokens=128_000),
    )

    assert audit["coverage"]["has_architecture_or_file_plan"] is False
    evidence_coverage = audit["final_request_evidence_coverage"]
    assert evidence_coverage["structured_evidence"]["architecture_or_file_plan"] is False
    assert "architecture_or_file_plan" in evidence_coverage["missing_required_refs"]


def test_final_request_evidence_rejects_text_only_context_architecture_plan_payload() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 4000},
        context={
            "director_execution_strategy": {
                "schema_version": "task.execution_strategy.v1",
                "evidence_requirements": ["architecture_or_file_plan"],
            },
            "architecture_or_file_plan": "construction_plan file plan scope_for_apply module_boundaries",
        },
    )
    prepared = PreparedLLMRequest(
        messages=[{"role": "system", "content": "No structured architecture plan is present."}],
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
    assert audit["coverage"]["has_architecture_or_file_plan"] is False
    assert evidence_coverage["structured_evidence"]["architecture_or_file_plan"] is False
    assert "architecture_or_file_plan" in evidence_coverage["missing_required_refs"]


def test_final_request_evidence_coverage_is_ref_based_and_redacted_when_underutilized() -> None:
    secret = "sk-test-context-secret-should-not-leak"
    execution_profile = {
        "schema_version": "task.execution_profile.v1",
        "source": "director.tasking",
        "task_type": "implement",
        "phase": "materialize",
        "language": "python",
        "target_files": ["src/main.py"],
    }
    execution_strategy = {
        "schema_version": "task.execution_strategy.v1",
        "source": "director.tasking",
        "evidence_requirements": [
            "pm_task_contract",
            "chief_engineer_blueprint",
            "target_files_or_declared_scopes",
            "execution_profile",
            "execution_strategy",
            "execution_envelope",
        ],
    }
    execution_envelope = {
        "schema_version": "polaris.execution_envelope.v1",
        "run_id": "run-1",
        "task_id": "TASK-1",
        "trace_id": "trace-1",
        "pm_contract": {"ref": "tasks/plan.json", "hash": "pm-hash"},
        "ce_blueprint": {"ref": "runtime/contracts/ce.json", "hash": "ce-hash"},
        "handoff_decision": {
            "ref": "runtime/contracts/handoff.json",
            "hash": "handoff-hash",
            "allowed": True,
        },
        "execution_profile": {"ref": "runtime/contracts/profile.json", "hash": "profile-hash"},
        "authorization": {
            "target_files": ["src/main.py"],
            "scope_paths": ["src/main.py"],
            "allowed_write_paths": ["src/main.py"],
        },
        "audit_policy": {"final_provider_request_required": True},
        "envelope_hash": "envelope-hash",
    }
    message = (
        "PM Task Contract / 任务合同: TASK-1 target_files src/main.py. "
        "Chief Engineer Blueprint / CE 蓝图交接: blueprint_id ce_TASK-1. "
        f"Private diagnostic token: {secret}."
    )
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 48000},
        context={
            "chat_messages": [{"role": "user", "content": message}],
            "director_execution_profile": execution_profile,
            "director_execution_strategy": execution_strategy,
            "director_execution_envelope": execution_envelope,
            "execution_envelope_hash": "envelope-hash",
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "task_id": "TASK-1",
                "target_files": ["src/main.py"],
            },
            "ce_blueprint": {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": "ce_TASK-1",
                "target_files": ["src/main.py"],
                "construction_plan": {"phase": "implement"},
            },
        },
    )
    prepared = PreparedLLMRequest(
        messages=[{"role": "user", "content": message}],
        input_text="test",
        context_result=None,
        context_summary="test",
        request_options=dict(ai_request.options),
        ai_request=ai_request,
    )

    profile = SimpleNamespace(role_id="director", max_context_tokens=1_000_000)
    audit = build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=profile,
    )

    evidence_coverage = audit["final_request_evidence_coverage"]
    finding_codes = {item["code"] for item in audit["context_quality"]["findings"]}
    assert audit["context_underutilized"] is True
    assert evidence_coverage["missing_required_refs"] == []
    assert evidence_coverage["coverage_ratio"] == 1.0
    assert evidence_coverage["pass"] is True
    assert evidence_coverage["redaction_safety"]["safe"] is True
    assert evidence_coverage["redaction_safety"]["message_content_embedded"] is False
    assert evidence_coverage["redaction_safety"]["evidence_coverage_embeds_content"] is False
    assert "underutilized_with_missing_context" not in finding_codes
    assert audit["context_quality"]["context_needs_review"] is False
    enforce_final_request_evidence_coverage(ai_request=ai_request, audit=audit)

    snapshot = build_final_provider_request_snapshot(
        ai_request=ai_request,
        prepared=prepared,
        profile=profile,
    )
    assert secret not in json.dumps(snapshot, ensure_ascii=False)


def test_final_request_context_audit_tracks_receipt_store_refs() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 48000},
        context={
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {
                    "role": "system",
                    "name": "chief_engineer_blueprint",
                    "content": "[chief_engineer_blueprint stored - receipt://chief_engineer_blueprint]",
                    "receipt_refs": ["chief_engineer_blueprint"],
                },
                {"role": "user", "content": "Implement the scoped change."},
            ],
            "run_ledger_projection": {
                "ref": "runtime/run-ledger/latest.json",
                "receipt_refs": ["quality_gate_receipt"],
            },
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {
                "role": "system",
                "name": "chief_engineer_blueprint",
                "content": "[chief_engineer_blueprint stored - receipt://chief_engineer_blueprint]",
                "receipt_refs": ["chief_engineer_blueprint"],
            },
            {"role": "user", "content": "Implement the scoped change."},
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
    assert evidence_coverage["ledger_evidence"]["receipt_refs"] == [
        "quality_gate_receipt",
        "chief_engineer_blueprint",
    ]
    assert "receipt_store_refs" in evidence_coverage["included_refs"]


def test_final_request_context_audit_ignores_receipt_refs_in_message_text() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 48000},
        context={
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {
                    "role": "system",
                    "name": "chief_engineer_blueprint",
                    "content": "[chief_engineer_blueprint stored - receipt://chief_engineer_blueprint]",
                },
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {
                "role": "system",
                "name": "chief_engineer_blueprint",
                "content": "[chief_engineer_blueprint stored - receipt://chief_engineer_blueprint]",
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

    evidence_coverage = audit["final_request_evidence_coverage"]
    assert evidence_coverage["ledger_evidence"]["receipt_refs"] == []
    assert "receipt_store_refs" not in evidence_coverage["included_refs"]


def test_final_request_context_audit_reads_nested_run_ledger_evidence_policy() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 48000},
        context={
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Continue the task-boundary repair."},
            ],
            "run_ledger_projection": {
                "ref": "runtime/run-ledger/latest.json",
                "evidence_policy": {
                    "missing_required_modalities": ["browser"],
                    "failed_required_modalities": ["task_boundary"],
                },
            },
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {"role": "user", "content": "Continue the task-boundary repair."},
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

    ledger_evidence = audit["final_request_evidence_coverage"]["ledger_evidence"]
    assert ledger_evidence["run_ledger_ref"] == "runtime/run-ledger/latest.json"
    assert ledger_evidence["missing_required_modalities"] == ["browser"]
    assert ledger_evidence["failed_required_modalities"] == ["task_boundary"]


def test_final_request_evidence_enforcement_is_opt_in_without_envelope() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 4000},
        context={
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Implement the task."},
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {"role": "user", "content": "Implement the task."},
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
    assert evidence_coverage["pass"] is False
    assert final_request_evidence_coverage_violation(ai_request=ai_request, audit=audit) is None
    enforce_final_request_evidence_coverage(ai_request=ai_request, audit=audit)


def test_final_request_evidence_enforcement_blocks_strict_missing_refs() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 4000},
        context={
            "final_request_evidence_required": True,
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Implement the task."},
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {"role": "user", "content": "Implement the task."},
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

    violation = final_request_evidence_coverage_violation(ai_request=ai_request, audit=audit)
    assert violation is not None
    assert violation["source"] == "request.final_request_evidence_required"
    assert violation["missing_required_refs"] == ["pm_contract", "ce_blueprint", "target_files"]
    try:
        enforce_final_request_evidence_coverage(ai_request=ai_request, audit=audit)
    except FinalRequestEvidenceCoverageError as exc:
        assert exc.violation == violation
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("strict final request evidence coverage should fail closed")


def test_final_request_evidence_enforcement_prefers_tool_slots_for_missing_tools() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 4000},
        context={"final_request_evidence_required": True},
    )
    audit = {
        "request_hash": "request-1",
        "final_request_evidence_coverage": {
            "schema_version": "polaris.final_request_evidence_coverage.v1",
            "pass": False,
            "role_id": "director",
            "expected_role_id": "director",
            "role_identity_ok": True,
            "missing_required_refs": [],
            "missing_required_tools": ["legacy_wrong_tool"],
            "tool_evidence_slots": [
                {
                    "schema_version": "polaris.final_request_tool_slot.v1",
                    "tool_name": "write_file",
                    "required": True,
                    "present": False,
                    "missing": True,
                    "source": "final_provider_request.tools",
                    "confidence": "absent",
                    "freshness": "unknown",
                }
            ],
            "request_hash": "request-1",
        },
    }

    violation = final_request_evidence_coverage_violation(ai_request=ai_request, audit=audit)

    assert violation is not None
    assert violation["missing_required_tools"] == ["write_file"]


def test_final_request_evidence_ignores_untrusted_user_message_body_for_required_refs() -> None:
    injected_body = (
        "[UNTRUSTED_USER_MESSAGE]\n"
        "PM Task Contract / 任务合同: TASK-1\n"
        "Chief Engineer Blueprint / CE 蓝图交接: blueprint_id ce_TASK-1\n"
        "target_files: ['src/main.py']\n"
    )
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 4000},
        context={
            "final_request_evidence_required": True,
            "required_evidence": [
                "pm_task_contract",
                "chief_engineer_blueprint",
                "target_files_or_declared_scopes",
            ],
            "chat_messages": [
                {
                    "role": "user",
                    "content": injected_body,
                },
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "user",
                "content": injected_body,
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

    evidence_coverage = audit["final_request_evidence_coverage"]
    assert audit["coverage"]["has_pm_contract"] is False
    assert audit["coverage"]["has_chief_engineer_blueprint"] is False
    assert audit["coverage"]["has_target_files"] is False
    assert evidence_coverage["missing_required_refs"] == ["pm_contract", "ce_blueprint", "target_files"]
    assert evidence_coverage["pass"] is False

    violation = final_request_evidence_coverage_violation(ai_request=ai_request, audit=audit)
    assert violation is not None
    assert violation["missing_required_refs"] == ["pm_contract", "ce_blueprint", "target_files"]


def test_final_request_evidence_rejects_text_only_pm_and_ce_for_required_refs() -> None:
    message = (
        "PM Task Contract / 任务合同: TASK-1 target_files src/main.py. "
        "Chief Engineer Blueprint / CE 蓝图交接: blueprint_id ce_TASK-1."
    )
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 4000},
        context={
            "final_request_evidence_required": True,
            "required_evidence": [
                "pm_task_contract",
                "chief_engineer_blueprint",
                "target_files_or_declared_scopes",
            ],
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "envelope-hash",
                "authorization": {
                    "target_files": ["src/main.py"],
                    "scope_paths": ["src/main.py"],
                },
            },
            "chat_messages": [{"role": "system", "content": message}],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[{"role": "system", "content": message}],
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
    assert audit["coverage"]["has_pm_contract"] is False
    assert audit["coverage"]["has_chief_engineer_blueprint"] is False
    assert evidence_coverage["structured_evidence"]["pm_contract"] is False
    assert evidence_coverage["structured_evidence"]["ce_blueprint"] is False
    assert evidence_coverage["structured_evidence"]["target_files"] is True
    assert "pm_contract" not in evidence_coverage["included_refs"]
    assert "ce_blueprint" not in evidence_coverage["included_refs"]
    assert evidence_coverage["missing_required_refs"] == ["pm_contract", "ce_blueprint"]


def test_final_request_evidence_rejects_weak_pm_and_ce_context_dicts() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 4000},
        context={
            "final_request_evidence_required": True,
            "required_evidence": [
                "pm_task_contract",
                "chief_engineer_blueprint",
                "target_files_or_declared_scopes",
            ],
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "envelope-hash",
                "authorization": {
                    "target_files": ["src/main.py"],
                    "scope_paths": ["src/main.py"],
                },
            },
            "pm_contract": {
                "note": "PM Task Contract / 任务合同: TASK-1 target_files src/main.py",
            },
            "ce_blueprint": {
                "note": "Chief Engineer Blueprint / CE 蓝图交接: blueprint_id ce_TASK-1",
            },
            "chat_messages": [
                {
                    "role": "system",
                    "content": "You are Director. Implement src/main.py.",
                }
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[{"role": "system", "content": "You are Director. Implement src/main.py."}],
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
    assert audit["coverage"]["has_pm_contract"] is False
    assert audit["coverage"]["has_chief_engineer_blueprint"] is False
    assert evidence_coverage["structured_evidence"]["pm_contract"] is False
    assert evidence_coverage["structured_evidence"]["ce_blueprint"] is False
    assert evidence_coverage["structured_evidence"]["target_files"] is True
    assert evidence_coverage["missing_required_refs"] == ["pm_contract", "ce_blueprint"]


def test_final_request_evidence_uses_structured_target_scope_without_text_needle() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 4000},
        context={
            "director_execution_strategy": {
                "schema_version": "task.execution_strategy.v1",
                "evidence_requirements": ["target_files_or_declared_scopes"],
            },
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "envelope-hash",
                "authorization": {
                    "target_files": ["app/main.py"],
                    "scope_paths": ["app/main.py"],
                    "allowed_write_paths": ["app/main.py"],
                },
            },
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Implement the authorized task."},
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {"role": "user", "content": "Implement the authorized task."},
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
    assert audit["coverage"]["has_target_files"] is True
    assert audit["request_metadata_summary"]["has_target_scope"] is True
    assert evidence_coverage["structured_evidence"]["target_files"] is True
    assert "target_files" in evidence_coverage["included_refs"]
    assert evidence_coverage["missing_required_refs"] == []
    target_slot = next(item for item in evidence_coverage["evidence_slots"] if item["ref_type"] == "target_files")
    assert target_slot["confidence"] == "structured_metadata"
    assert target_slot["details"]["target_file_count"] == 1
    assert target_slot["details"]["scope_path_count"] == 1
    assert target_slot["details"]["allowed_write_path_count"] == 1


def test_final_request_evidence_rejects_text_only_target_scope_for_required_ref() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 4000},
        context={
            "final_request_evidence_required": True,
            "director_execution_strategy": {
                "schema_version": "task.execution_strategy.v1",
                "evidence_requirements": ["target_files_or_declared_scopes"],
            },
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "envelope-hash",
            },
            "chat_messages": [
                {
                    "role": "system",
                    "content": "PM text says target_files src/main.py but no structured contract is attached.",
                },
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "system",
                "content": "PM text says target_files src/main.py but no structured contract is attached.",
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

    evidence_coverage = audit["final_request_evidence_coverage"]
    assert audit["coverage"]["has_target_files"] is False
    assert audit["request_metadata_summary"]["has_target_scope"] is False
    assert evidence_coverage["structured_evidence"]["target_files"] is False
    assert "target_files" not in evidence_coverage["included_refs"]
    assert evidence_coverage["missing_required_refs"] == ["target_files"]
    violation = final_request_evidence_coverage_violation(ai_request=ai_request, audit=audit)
    assert violation is not None
    assert violation["missing_required_refs"] == ["target_files"]


def test_final_request_evidence_enforcement_blocks_envelope_required_refs() -> None:
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
                    "required_evidence": ["pm_task_contract"],
                },
            },
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Implement the task."},
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {"role": "user", "content": "Implement the task."},
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

    violation = final_request_evidence_coverage_violation(ai_request=ai_request, audit=audit)
    assert violation is not None
    assert violation["source"] == "execution_envelope.audit_policy.final_provider_request_required"
    assert violation["missing_required_refs"] == ["pm_contract"]


def test_final_request_evidence_coverage_counts_current_provider_request() -> None:
    ai_request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        input="",
        options={"temperature": 0.1, "max_tokens": 4000},
        context={
            "final_request_evidence_required": True,
            "required_evidence": ["final_provider_request"],
            "director_execution_envelope": {
                "schema_version": "polaris.execution_envelope.v1",
                "envelope_hash": "envelope-hash",
                "authorization": {
                    "target_files": ["src/main.py"],
                    "scope_paths": ["src/main.py"],
                },
            },
            "pm_contract": {
                "schema_version": "pm.task_contract.v1",
                "task_id": "TASK-1",
                "target_files": ["src/main.py"],
            },
            "ce_blueprint": {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": "ce_TASK-1",
                "target_files": ["src/main.py"],
            },
            "chat_messages": [
                {
                    "role": "system",
                    "content": (
                        "You are Director. PM Task Contract / 任务合同: TASK-1 target_files src/main.py. "
                        "Chief Engineer Blueprint / CE 蓝图交接: blueprint_id ce_TASK-1."
                    ),
                },
                {"role": "user", "content": "Implement src/main.py."},
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "system",
                "content": (
                    "You are Director. PM Task Contract / 任务合同: TASK-1 target_files src/main.py. "
                    "Chief Engineer Blueprint / CE 蓝图交接: blueprint_id ce_TASK-1."
                ),
            },
            {"role": "user", "content": "Implement src/main.py."},
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
    assert "final_provider_request" in evidence_coverage["included_refs"]
    assert evidence_coverage["missing_required_refs"] == []
    assert evidence_coverage["pass"] is True
    enforce_final_request_evidence_coverage(ai_request=ai_request, audit=audit)


def test_final_request_evidence_coverage_tracks_delivery_plan_and_depth_contract() -> None:
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
                    "required_evidence": ["delivery_plan_document", "delivery_depth_contract"],
                },
            },
            "delivery_plan_document": {
                "schema_version": "polaris.delivery_plan_document.v1",
                "product_summary": {
                    "intent": "Deliver a playable mood color wheel with visible doodle behavior.",
                    "core_terms": ["mood", "color", "wheel", "doodle"],
                },
                "user_journey": ["Open canvas", "Pick mood", "Inspect color wheel report"],
            },
            "delivery_depth_contract": {
                "schema_version": "polaris.delivery_depth_contract.v1",
                "product_intent": {
                    "subject": "mood doodle color wheel",
                    "primary_entities": ["mood", "color", "wheel", "doodle"],
                },
                "behavior_contract": {
                    "rule_matrix": [
                        "mood controls brush color",
                        "beat intensity changes stroke width",
                        "report summarizes dominant color wheel sector",
                    ],
                    "edge_cases": ["unknown mood uses explicit fallback"],
                },
            },
            "chat_messages": [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Implement the mood doodle color wheel."},
            ],
        },
    )
    prepared = PreparedLLMRequest(
        messages=[
            {"role": "system", "content": "You are Director."},
            {"role": "user", "content": "Implement the mood doodle color wheel."},
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
    assert metadata_summary["delivery_plan_document_hash"]
    assert metadata_summary["has_delivery_depth_contract"] is True
    assert metadata_summary["delivery_depth_contract_hash"]
    evidence_coverage = audit["final_request_evidence_coverage"]
    assert "delivery_plan_document" in evidence_coverage["required_refs"]
    assert "delivery_depth_contract" in evidence_coverage["required_refs"]
    assert "delivery_plan_document" in evidence_coverage["included_refs"]
    assert "delivery_depth_contract" in evidence_coverage["included_refs"]
    assert evidence_coverage["missing_required_refs"] == []
    assert evidence_coverage["pass"] is True
    enforce_final_request_evidence_coverage(ai_request=ai_request, audit=audit)


