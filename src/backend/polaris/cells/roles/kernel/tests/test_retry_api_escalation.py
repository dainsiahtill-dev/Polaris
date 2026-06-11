"""ADR-0090: API-level escalation ladder for mutation-contract retries.

Observed live (qwen3.6, django-15213): the model emitted ``repo_rg`` through
FOUR "you MUST write" retries — prompt-level hints are exactly what weak models
ignore, and the write-INCLUSIVE tool set still offered read tools. Guided
decoding cannot be ignored: late attempts must narrow the offered tools to
write-only, and the final attempt must force the selected write tool by name.
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
    narrow_edit_blocks_schema_to_line_range,
    resolve_retry_escalation,
)

_STRICT_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "edit_blocks",
            "description": "old",
            "parameters": {"type": "object", "properties": {"blocks": {"type": "string"}}},
        },
    },
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

    def test_final_attempt_forces_named_write_tool_and_narrows_schema(self) -> None:
        definitions, tool_choice = resolve_retry_escalation(
            attempt_index=3,
            max_retry_attempts=4,
            strict_tool_definitions=_STRICT_DEFS,
            forced_write_tool_name="edit_blocks",
        )

        assert tool_choice == {"type": "function", "function": {"name": "edit_blocks"}}
        assert definitions is not None
        edit_def = next(d for d in definitions if d["function"]["name"] == "edit_blocks")
        parameters = edit_def["function"]["parameters"]
        # Guided decoding can ONLY produce the line-range form: prose-in-blocks
        # ("No valid edit blocks found", observed live) becomes ungenerable.
        assert set(parameters["required"]) == {"file", "start", "end", "replace"}
        assert "blocks" not in parameters["properties"]

    def test_final_attempt_with_non_edit_blocks_tool_keeps_schema(self) -> None:
        definitions, tool_choice = resolve_retry_escalation(
            attempt_index=3,
            max_retry_attempts=4,
            strict_tool_definitions=_STRICT_DEFS,
            forced_write_tool_name="write_file",
        )

        assert definitions == _STRICT_DEFS
        assert tool_choice == {"type": "function", "function": {"name": "write_file"}}

    def test_narrow_transform_preserves_other_tools(self) -> None:
        narrowed = narrow_edit_blocks_schema_to_line_range(_STRICT_DEFS)

        assert narrowed[1] == _STRICT_DEFS[1]
        assert narrowed[0]["function"]["parameters"]["required"] == ["file", "start", "end", "replace"]
        # Source definitions must not be mutated.
        assert "blocks" in _STRICT_DEFS[0]["function"]["parameters"]["properties"]

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


class TestMutationImpliesVerification:
    """Phase-1 A2 (run20 audit): a mutation contract implies verification access.

    run20: 18/18 instances executed ZERO verification commands because
    ``requires_verification`` keyed off message keywords ("test", "verify")
    that a plain "fix the bug" task never contains, so execute_command was
    excluded from the narrowed retry tool set and model-initiated test runs
    were rejected as contract violations.
    """

    def test_write_only_set_includes_execute_command_when_verification_enabled(self) -> None:
        from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
            build_forced_write_only_retry_tool_definitions,
        )

        defs = [*_STRICT_DEFS, {"type": "function", "function": {"name": "execute_command"}}]
        narrowed = build_forced_write_only_retry_tool_definitions(
            defs,
            "edit_blocks",
            include_verification_tools=True,
        )
        names = {d["function"]["name"] for d in narrowed}
        assert names == {"edit_blocks", "execute_command"}

    def test_benchmark_forbidden_execute_command_stays_excluded(self) -> None:
        from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
            build_forced_write_only_retry_tool_definitions,
        )

        defs = [*_STRICT_DEFS, {"type": "function", "function": {"name": "execute_command"}}]
        narrowed = build_forced_write_only_retry_tool_definitions(
            defs,
            "edit_blocks",
            include_verification_tools=True,
            forbidden_tool_names={"execute_command"},
        )
        names = {d["function"]["name"] for d in narrowed}
        assert names == {"edit_blocks"}

    def test_plain_fix_request_implies_verification_in_retry_path(self) -> None:
        """The composed predicate used by retry_tool_batch_after_contract_violation:
        a mutation-intent message without any verification keyword must still
        grant verification access."""
        from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
            requires_mutation_intent,
            requires_verification_intent,
        )

        message = "[mode:materialize]\nYou are fixing a real bug in this repository. Apply the fix."
        assert requires_verification_intent(message) is False, "fixture must stay keyword-free"
        assert requires_mutation_intent(message) is True
        assert (requires_verification_intent(message) or requires_mutation_intent(message)) is True
