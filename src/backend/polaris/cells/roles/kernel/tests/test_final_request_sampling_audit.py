"""Tests for final provider request sampling audit metadata."""

from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
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
        "temperature_source": "task.execution_profile.v1",
        "temperature_phase": "repair",
        "sampling_mode": "deterministic_precise",
        "task_type": "bugfix",
        "phase": "repair",
        "execution_profile_schema": "task.execution_profile.v1",
        "execution_profile_source": "director.tasking",
    }
