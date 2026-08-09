from __future__ import annotations

import pytest
from polaris.kernelone.audit.context_os_prompt import (
    audit_context_os_prompt_messages,
    summarize_context_os_audit_from_ledger,
)


def test_context_os_prompt_audit_accepts_state_first_projection_with_user_literal_key() -> None:
    audit = audit_context_os_prompt_messages(
        messages=[
            {"role": "system", "content": "Projected context summary."},
            {"role": "user", "content": "Explain why context_os_snapshot: is only a literal here."},
        ],
        context_sources=("state_first_context_os.project",),
        metadata={"state_first_mode_active": True},
        current_user_instruction="Explain why context_os_snapshot: is only a literal here.",
        expected=True,
    )

    assert audit["ok"] is True
    assert audit["state_first_context_os"]["projected"] is True
    assert audit["control_plane"]["content_hits"] == []
    assert audit["current_user_instruction"]["preserved"] is True


def test_context_os_prompt_audit_rejects_control_plane_content_leak() -> None:
    audit = audit_context_os_prompt_messages(
        messages=[
            {"role": "system", "content": "context_os_snapshot: {'raw': 'control'}"},
            {"role": "user", "content": "Summarize the context."},
        ],
        context_sources=("state_first_context_os.project",),
        metadata={"state_first_mode_active": True},
        current_user_instruction="Summarize the context.",
        expected=True,
    )

    assert audit["ok"] is False
    assert "context_os_snapshot:" in audit["control_plane"]["content_hits"]


def test_context_os_prompt_audit_allows_director_quality_repair_protocol_text() -> None:
    """Quality-repair directives intentionally name delivery_mode / markers.

    L1-01 r124 residual: quality-repair qualification failed with
    final_request_context_quality_failed because content hits matched
    delivery_mode / director_quality_repair / blueprint_id in intentional
    SESSION_PATCH and repair headers — not real control-plane dumps.
    """
    repair_user = (
        "[mode:materialize]\n"
        '<SESSION_PATCH>{"delivery_mode":"materialize_changes","task_progress":"implementing"}'
        "</SESSION_PATCH>\n"
        "Chief Engineer Blueprint evidence:\n"
        "- blueprint_id: ce_TASK-1_demo\n"
        "[director_quality_repair:write_only_single_target]\n"
        "- Target path: tests/verify.test.ts\n"
        "Return tool calls only."
    )
    audit = audit_context_os_prompt_messages(
        messages=[
            {"role": "system", "content": "You are Director. Execute materialization repairs."},
            {"role": "user", "content": repair_user},
        ],
        context_sources=("state_first_context_os.project",),
        metadata={"state_first_mode_active": True},
        current_user_instruction=repair_user,
        expected=True,
    )

    assert audit["ok"] is True
    assert audit["control_plane"]["content_hits"] == []
    assert audit["control_plane"]["isolated"] is True


def test_context_os_prompt_audit_still_rejects_capability_token_in_system_with_repair() -> None:
    """Real control-plane dumps still fail even when repair protocol text is present."""
    repair_user = (
        "[director_quality_repair:edit_preferred_single_target]\n"
        "- Target path: src/main.ts\n"
        "Fix the file."
    )
    audit = audit_context_os_prompt_messages(
        messages=[
            {
                "role": "system",
                "content": (
                    "You are Director.\n"
                    "capability_token: {'token_id': 'job-leaked'}\n"
                    '<SESSION_PATCH>{"delivery_mode":"materialize_changes"}</SESSION_PATCH>'
                ),
            },
            {"role": "user", "content": repair_user},
        ],
        context_sources=("state_first_context_os.project",),
        metadata={"state_first_mode_active": True},
        current_user_instruction=repair_user,
        expected=True,
    )

    assert audit["ok"] is False
    assert "capability_token:" in audit["control_plane"]["content_hits"]
    # Operational protocol text alone is not enough to fail isolation.
    assert "delivery_mode" not in audit["control_plane"]["content_hits"]
    assert "director_quality_repair" not in audit["control_plane"]["content_hits"]


def test_context_os_prompt_audit_rejects_capability_and_execution_attempt_authority() -> None:
    audit = audit_context_os_prompt_messages(
        messages=[
            {
                "role": "system",
                "content": "capability_token: {'token_id': 'job-1'}",
                "metadata": {
                    "task_runtime_execution_attempt": {
                        "run_id": "director-1",
                    }
                },
            },
            {"role": "user", "content": "Materialize the declared Rust targets."},
        ],
        context_sources=("state_first_context_os.project",),
        metadata={"state_first_mode_active": True},
        current_user_instruction="Materialize the declared Rust targets.",
        expected=True,
    )

    assert audit["ok"] is False
    assert "capability_token:" in audit["control_plane"]["content_hits"]
    assert audit["control_plane"]["metadata_key_hits"] == ["task_runtime_execution_attempt"]


def test_context_os_prompt_audit_rejects_opaque_capability_object_signature() -> None:
    audit = audit_context_os_prompt_messages(
        messages=[
            {
                "role": "system",
                "content": "payload: CapabilityToken(token_id='job-1', allowed_scope=['src/main.rs'])",
            },
            {"role": "user", "content": "Materialize the declared Rust targets."},
        ],
        context_sources=("state_first_context_os.project",),
        current_user_instruction="Materialize the declared Rust targets.",
        expected=True,
    )

    assert audit["ok"] is False
    assert "capabilitytoken(" in audit["control_plane"]["content_hits"]


def test_context_os_prompt_audit_rejects_camel_case_serialized_capability_key() -> None:
    audit = audit_context_os_prompt_messages(
        messages=[
            {
                "role": "system",
                "content": ("payload: capabilityToken: {'tokenId': 'job-2', 'allowedScope': ['src/camel.rs']}"),
            },
            {"role": "user", "content": "Materialize the declared Rust targets."},
        ],
        context_sources=("state_first_context_os.project",),
        current_user_instruction="Materialize the declared Rust targets.",
        expected=True,
    )

    assert audit["ok"] is False
    assert "capability_token" in audit["control_plane"]["content_hits"]


def test_context_os_prompt_audit_rejects_spaced_serialized_capability_key() -> None:
    audit = audit_context_os_prompt_messages(
        messages=[
            {
                "role": "system",
                "content": "payload: 'capability token': {'token_id': 'job-3'}",
            },
            {"role": "user", "content": "Materialize the declared Rust targets."},
        ],
        context_sources=("state_first_context_os.project",),
        current_user_instruction="Materialize the declared Rust targets.",
        expected=True,
    )

    assert audit["ok"] is False
    assert "capability_token" in audit["control_plane"]["content_hits"]


@pytest.mark.parametrize(
    ("serialized_key", "token_key", "scope_key"),
    (
        ("capability.token", "token.id", "allowed.scope"),
        ("capability/token", "token/id", "allowed/scope"),
        ("capability\ttoken", "token\tid", "allowed\tscope"),
    ),
)
def test_context_os_prompt_audit_normalizes_quoted_key_separator_variants(
    serialized_key: str,
    token_key: str,
    scope_key: str,
) -> None:
    audit = audit_context_os_prompt_messages(
        messages=[
            {
                "role": "system",
                "content": (
                    f"payload: '{serialized_key}': {{'{token_key}': 'job-variant', '{scope_key}': ['src/private.rs']}}"
                ),
            },
            {"role": "user", "content": "Materialize the declared Rust targets."},
        ],
        context_sources=("state_first_context_os.project",),
        current_user_instruction="Materialize the declared Rust targets.",
        expected=True,
    )

    assert audit["ok"] is False
    assert "capability_token" in audit["control_plane"]["content_hits"]


def test_context_os_prompt_audit_accepts_task_ids_inside_pm_contract() -> None:
    audit = audit_context_os_prompt_messages(
        messages=[
            {
                "role": "system",
                "content": "PM contract: {'tasks': [{'task_id': 'TASK-1', 'goal': 'build models'}]}",
            },
            {"role": "user", "content": "Produce the Chief Engineer portfolio."},
        ],
        context_sources=("state_first_context_os.project",),
        metadata={"state_first_mode_active": True},
        current_user_instruction="Produce the Chief Engineer portfolio.",
        expected=True,
    )

    assert audit["ok"] is True
    assert audit["control_plane"]["content_hits"] == []


def test_context_os_prompt_audit_accepts_metadata_as_ordinary_prompt_prose() -> None:
    """A generic structural noun is not proof of serialized runtime authority."""
    audit = audit_context_os_prompt_messages(
        messages=[
            {
                "role": "system",
                "content": "Document the 'metadata' field exposed by the public data model.",
            },
            {"role": "user", "content": "Implement the declared Rust target files."},
        ],
        context_sources=("state_first_context_os.project",),
        metadata={"state_first_mode_active": True},
        current_user_instruction="Implement the declared Rust target files.",
        expected=True,
    )

    assert audit["ok"] is True
    assert audit["control_plane"]["content_hits"] == []


def test_context_os_prompt_audit_still_rejects_metadata_as_message_metadata_key() -> None:
    audit = audit_context_os_prompt_messages(
        messages=[
            {
                "role": "system",
                "content": "Projected context summary.",
                "metadata": {"metadata": {"capability_token": "job-1"}},
            },
            {"role": "user", "content": "Implement the declared Rust target files."},
        ],
        context_sources=("state_first_context_os.project",),
        metadata={"state_first_mode_active": True},
        current_user_instruction="Implement the declared Rust target files.",
        expected=True,
    )

    assert audit["ok"] is False
    assert audit["control_plane"]["metadata_key_hits"] == ["metadata"]


def test_context_os_prompt_audit_accepts_qa_workspace_quality_data_plane() -> None:
    """QA receipts may name workspace paths and implementation metrics."""
    current_user_instruction = "Verify the workspace against the PM contract and CE blueprint."
    audit = audit_context_os_prompt_messages(
        messages=[
            {"role": "system", "content": "You are Polaris QA."},
            {
                "role": "user",
                "content": (
                    "[UNTRUSTED_USER_MESSAGE]\n"
                    '{"workspace":"/home/user/project","command":"npm test"}\n'
                    "implementation depth metrics: files=12 assertions=14"
                ),
            },
            {"role": "user", "content": current_user_instruction},
        ],
        context_sources=("state_first_context_os.project",),
        metadata={"state_first_mode_active": True},
        current_user_instruction=current_user_instruction,
        expected=True,
    )

    assert audit["ok"] is True
    assert audit["control_plane"]["content_hits"] == []
    assert audit["control_plane"]["isolated"] is True


@pytest.mark.parametrize("authority_key", ("workspace_root", "factory_run_id", "job_token"))
def test_context_os_prompt_audit_still_rejects_strong_authority_content_key(authority_key: str) -> None:
    current_user_instruction = "Verify the workspace."
    audit = audit_context_os_prompt_messages(
        messages=[
            {"role": "system", "content": f'authority: {{"{authority_key}":"secret"}}'},
            {"role": "user", "content": current_user_instruction},
        ],
        context_sources=("state_first_context_os.project",),
        metadata={"state_first_mode_active": True},
        current_user_instruction=current_user_instruction,
        expected=True,
    )

    assert audit["ok"] is False
    assert authority_key in audit["control_plane"]["content_hits"]


@pytest.mark.parametrize("metadata_key", ("workspace", "metrics"))
def test_context_os_prompt_audit_still_rejects_generic_terms_as_message_metadata(metadata_key: str) -> None:
    current_user_instruction = "Verify the workspace."
    audit = audit_context_os_prompt_messages(
        messages=[
            {
                "role": "system",
                "content": "Projected QA evidence.",
                "metadata": {metadata_key: "not-prompt-data"},
            },
            {"role": "user", "content": current_user_instruction},
        ],
        context_sources=("state_first_context_os.project",),
        metadata={"state_first_mode_active": True},
        current_user_instruction=current_user_instruction,
        expected=True,
    )

    assert audit["ok"] is False
    assert audit["control_plane"]["metadata_key_hits"] == [metadata_key]


def test_context_os_prompt_audit_still_rejects_task_id_metadata() -> None:
    audit = audit_context_os_prompt_messages(
        messages=[
            {
                "role": "system",
                "content": "Projected context summary.",
                "metadata": {"task_id": "TASK-1"},
            },
            {"role": "user", "content": "Produce the Chief Engineer portfolio."},
        ],
        context_sources=("state_first_context_os.project",),
        metadata={"state_first_mode_active": True},
        current_user_instruction="Produce the Chief Engineer portfolio.",
        expected=True,
    )

    assert audit["ok"] is False
    assert audit["control_plane"]["metadata_key_hits"] == ["task_id"]


def test_context_os_prompt_audit_rejects_non_final_current_user_instruction() -> None:
    audit = audit_context_os_prompt_messages(
        messages=[
            {"role": "system", "content": "Projected context summary."},
            {"role": "user", "content": "Summarize the context."},
            {"role": "system", "content": "Late system projection."},
        ],
        context_sources=("state_first_context_os.project",),
        metadata={"state_first_mode_active": True},
        current_user_instruction="Summarize the context.",
        expected=True,
    )

    assert audit["ok"] is False
    assert audit["requirements"]["current_user_final"] is False
    assert audit["current_user_instruction"]["preserved"] is True


def test_context_os_prompt_audit_rejects_raw_tool_failure_receipt() -> None:
    audit = audit_context_os_prompt_messages(
        messages=[
            {
                "role": "assistant",
                "content": (
                    "**write_file**: Error - {'ok': False, 'error': 'Director write policy denied', "
                    "'tool': 'write_file', 'error_type': 'director_write_policy_denied', "
                    "'director_policy': {'package_diff': {}}, "
                    "'handler_error_type': 'director_write_policy_denied'}"
                ),
            },
            {"role": "user", "content": "Fix the package manifest."},
        ],
        context_sources=("state_first_context_os.project",),
        metadata={"state_first_mode_active": True},
        current_user_instruction="Fix the package manifest.",
        expected=True,
    )

    assert audit["ok"] is False
    assert audit["data_plane"]["raw_tool_failure_receipt_absent"] is False
    assert "director_policy" in audit["data_plane"]["raw_tool_failure_receipt_hits"]


def test_context_os_prompt_audit_accepts_tool_failure_summary() -> None:
    audit = audit_context_os_prompt_messages(
        messages=[
            {
                "role": "assistant",
                "content": (
                    "[tool_failure_summary]\n"
                    '{"tool": "write_file", "error_type": "director_write_policy_denied", '
                    '"reason": "package.json content was invalid JSON", "prompt_safe": true}'
                ),
            },
            {"role": "user", "content": "Fix the package manifest."},
        ],
        context_sources=("state_first_context_os.project",),
        metadata={"state_first_mode_active": True},
        current_user_instruction="Fix the package manifest.",
        expected=True,
    )

    assert audit["ok"] is True
    assert audit["data_plane"]["raw_tool_failure_receipt_absent"] is True


def test_context_os_audit_summary_from_ledger_is_prompt_text_free() -> None:
    audit = audit_context_os_prompt_messages(
        messages=[
            {"role": "system", "content": "Projected context summary."},
            {"role": "user", "content": "Summarize the context."},
        ],
        context_sources=("state_first_context_os.project",),
        metadata={"state_first_mode_active": True},
        current_user_instruction="Summarize the context.",
        expected=True,
    )

    class _Ledger:
        llm_calls = [{"metadata": {"context_os_audit": audit}}]

    summary = summarize_context_os_audit_from_ledger(_Ledger())

    assert summary["ok"] is True
    assert summary["llm_call_count"] == 1
    assert summary["latest"]["prompt_digest"] == audit["prompt_digest"]
    assert "Summarize the context." not in str(summary)
