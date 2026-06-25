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
    }


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
