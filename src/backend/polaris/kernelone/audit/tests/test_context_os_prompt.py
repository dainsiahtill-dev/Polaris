from __future__ import annotations

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
