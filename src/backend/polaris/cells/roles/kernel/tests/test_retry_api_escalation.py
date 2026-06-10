"""ADR-0090: API-level escalation ladder for mutation-contract retries.

Observed live (qwen3.6, django-15213): the model emitted ``repo_rg`` through
FOUR "you MUST write" retries — prompt-level hints are exactly what weak models
ignore, and the write-INCLUSIVE tool set still offered read tools. Guided
decoding cannot be ignored: late attempts must narrow the offered tools to
write-only, and the final attempt must force the selected write tool by name.
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
    resolve_retry_escalation,
)

_STRICT_DEFS = [
    {"type": "function", "function": {"name": "edit_blocks"}},
    {"type": "function", "function": {"name": "write_file"}},
]


class TestResolveRetryEscalation:
    def test_early_attempts_keep_defaults(self) -> None:
        for attempt_index in (0, 1):
            definitions, tool_choice = resolve_retry_escalation(
                attempt_index=attempt_index,
                max_retry_attempts=4,
                strict_tool_definitions=_STRICT_DEFS,
                forced_write_tool_name="edit_blocks",
            )
            assert definitions is None
            assert tool_choice is None

    def test_third_attempt_narrows_to_write_only_without_forcing(self) -> None:
        definitions, tool_choice = resolve_retry_escalation(
            attempt_index=2,
            max_retry_attempts=4,
            strict_tool_definitions=_STRICT_DEFS,
            forced_write_tool_name="edit_blocks",
        )

        assert definitions == _STRICT_DEFS
        assert tool_choice is None

    def test_final_attempt_forces_named_write_tool(self) -> None:
        definitions, tool_choice = resolve_retry_escalation(
            attempt_index=3,
            max_retry_attempts=4,
            strict_tool_definitions=_STRICT_DEFS,
            forced_write_tool_name="edit_blocks",
        )

        assert definitions == _STRICT_DEFS
        assert tool_choice == {"type": "function", "function": {"name": "edit_blocks"}}

    def test_no_strict_definitions_disables_escalation(self) -> None:
        definitions, tool_choice = resolve_retry_escalation(
            attempt_index=3,
            max_retry_attempts=4,
            strict_tool_definitions=None,
            forced_write_tool_name="edit_blocks",
        )

        assert definitions is None
        assert tool_choice is None

    def test_final_attempt_without_forced_name_still_narrows(self) -> None:
        definitions, tool_choice = resolve_retry_escalation(
            attempt_index=3,
            max_retry_attempts=4,
            strict_tool_definitions=_STRICT_DEFS,
            forced_write_tool_name=None,
        )

        assert definitions == _STRICT_DEFS
        assert tool_choice is None
