"""Tests for final provider request sampling audit metadata."""

from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
    build_final_provider_request_snapshot,
    build_final_request_context_audit_for_request,
)
from polaris.cells.roles.kernel.internal.llm_caller.response_types import PreparedLLMRequest
from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType


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
        options={"temperature": 0.15, "max_tokens": 5000, "response_format": {"type": "json_object"}},
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
    assert evidence_coverage["pass"] is False
    assert "missing_required_final_request_tools" in finding_codes


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
