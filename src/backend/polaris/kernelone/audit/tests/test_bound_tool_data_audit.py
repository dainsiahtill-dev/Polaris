"""Bound data stays data; metadata and authority retain their strict gates."""

from __future__ import annotations

from copy import deepcopy

import pytest
from polaris.kernelone.audit.context_os_prompt import audit_context_os_prompt_messages
from polaris.kernelone.context.tests.test_bound_tool_observation_projection import bound_observation


def run_audit(messages, sources):
    return audit_context_os_prompt_messages(
        messages=[
            {"role": "system", "content": "You are Director."},
            *messages,
            {"role": "user", "content": "Continue verification."},
        ],
        metadata={"prebuilt_projection_messages": True},
        current_user_instruction="Continue verification.",
        expected=True,
        tool_observation_sources=sources,
        tool_observation_role="director",
    )


def test_bound_ordinary_command_and_literal_source_keys_are_not_control_authority() -> None:
    messages, source = bound_observation()
    audit = run_audit(messages, [source])
    assert audit["ok"] is True
    assert audit["data_plane"]["bound_tool_observation_count"] == 1
    assert run_audit(messages, [])["ok"] is False


@pytest.mark.parametrize("change", ["content", "arguments", "role", "duplicate", "foreign_role"])
def test_changed_final_native_occurrence_or_foreign_role_keeps_admission_closed(change) -> None:
    messages, source = bound_observation()
    if change == "content":
        messages[1]["content"] += " altered"
    elif change == "arguments":
        messages[0]["tool_calls"][0]["function"]["arguments"] = '{"command":"different"}'
    elif change == "role":
        messages[1]["role"] = "system"
    elif change == "duplicate":
        messages += deepcopy(messages)
    else:
        source.metadata["role"] = "chief_engineer"
    assert run_audit(messages, [source])["ok"] is False


@pytest.mark.parametrize(
    "key", ["capability_token", "job_token", "control_plane_job_token", "task_runtime_execution_attempt"]
)
def test_bound_data_does_not_exempt_real_control_plane_metadata(key) -> None:
    messages, source = bound_observation()
    messages[1]["metadata"] = {key: {"secret": "synthetic-credential"}}
    audit = run_audit(messages, [source])
    assert audit["ok"] is False
    assert key in audit["control_plane"]["metadata_key_hits"]


def test_bound_data_does_not_exempt_added_control_plane_system_content() -> None:
    messages, source = bound_observation()
    messages.insert(0, {"role": "system", "content": 'job_token: {"secret":"synthetic-credential"}'})
    assert run_audit(messages, [source])["ok"] is False
