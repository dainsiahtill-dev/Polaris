"""ADR-0090: API-level escalation ladder for mutation-contract retries.

Observed live (qwen3.6, django-15213): the model emitted ``repo_rg`` through
FOUR "you MUST write" retries — prompt-level hints are exactly what weak models
ignore, and the write-INCLUSIVE tool set still offered read tools. Guided
decoding cannot be ignored: late attempts must narrow the offered tools to
write-only, and the final attempt must force the selected write tool by name.
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
    detect_creation_mode,
    narrow_edit_blocks_schema_to_line_range,
    resolve_escalation_temperature,
    resolve_retry_create_output_floor,
    resolve_retry_escalation,
    resolve_retry_output_floor,
    resolve_retry_temperature_override,
)


class TestRetryCreateOutputFloor:
    """F16 follow-up (Wall 2, 2026-06-15): a pure-create forced write needs a
    larger reserved output floor so a full file body is not truncated
    (finish_reason=length) into an empty/partial write."""

    def test_default_create_floor_exceeds_standard_floor(self) -> None:
        # The create floor must be larger so max() picks it at the pure-create site.
        assert resolve_retry_create_output_floor() == 7000
        assert resolve_retry_create_output_floor() > (resolve_retry_output_floor() or 0)

    def test_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("KERNELONE_RETRY_CREATE_OUTPUT_FLOOR_TOKENS", "9000")
        assert resolve_retry_create_output_floor() == 9000

    def test_env_disable(self, monkeypatch) -> None:
        monkeypatch.setenv("KERNELONE_RETRY_CREATE_OUTPUT_FLOOR_TOKENS", "off")
        assert resolve_retry_create_output_floor() is None

    def test_non_positive_disables(self, monkeypatch) -> None:
        monkeypatch.setenv("KERNELONE_RETRY_CREATE_OUTPUT_FLOOR_TOKENS", "0")
        assert resolve_retry_create_output_floor() is None


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
_STRICT_DEFS_WITH_VERIFY = [
    *_STRICT_DEFS,
    {"type": "function", "function": {"name": "execute_command"}},
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

    def test_final_attempt_removes_verification_tools_when_write_tool_is_forced(self) -> None:
        definitions, tool_choice = resolve_retry_escalation(
            attempt_index=3,
            max_retry_attempts=4,
            strict_tool_definitions=_STRICT_DEFS_WITH_VERIFY,
            forced_write_tool_name="write_file",
        )

        assert tool_choice == {"type": "function", "function": {"name": "write_file"}}
        assert definitions is not None
        assert {d["function"]["name"] for d in definitions} == {"edit_blocks", "write_file"}

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


class TestF16CreationModeEscalation:
    """F16 (2026-06-15): from-scratch creates force the write tool by name from
    the first retry attempt (index 0).

    Live (L2-12 brick-breaker, b2 solo): qwen emitted ``execute_command``×3
    through the graduated ladder before the last-attempt forced rung, tripping
    the circuit breaker into a dead-letter (0 runnable products). Live L1-01 Q6
    later showed the remaining free retry still burned minutes with no code
    landed. Pulling the force to index 0 keeps create-file retries write-bound.
    """

    def test_creation_forces_named_write_from_first_retry(self) -> None:
        definitions, tool_choice = resolve_retry_escalation(
            attempt_index=0,
            max_retry_attempts=4,
            strict_tool_definitions=_STRICT_DEFS_WITH_VERIFY,
            forced_write_tool_name="write_file",
            force_write_immediately=True,
        )
        assert definitions == _STRICT_DEFS
        assert tool_choice == {"type": "function", "function": {"name": "write_file"}}

    def test_creation_keeps_forcing_named_write_from_second_attempt(self) -> None:
        definitions, tool_choice = resolve_retry_escalation(
            attempt_index=1,
            max_retry_attempts=4,
            strict_tool_definitions=_STRICT_DEFS,
            forced_write_tool_name="write_file",
            force_write_immediately=True,
        )
        # The graduated (non-creation) ladder would still be in the free phase at
        # index 1 — creation forces the write by name three attempts earlier.
        assert definitions == _STRICT_DEFS
        assert tool_choice == {"type": "function", "function": {"name": "write_file"}}

    def test_creation_forces_edit_blocks_schema_narrowing_early(self) -> None:
        definitions, tool_choice = resolve_retry_escalation(
            attempt_index=0,
            max_retry_attempts=4,
            strict_tool_definitions=_STRICT_DEFS,
            forced_write_tool_name="edit_blocks",
            force_write_immediately=True,
        )
        assert tool_choice == {"type": "function", "function": {"name": "edit_blocks"}}
        edit_def = next(d for d in definitions if d["function"]["name"] == "edit_blocks")
        assert set(edit_def["function"]["parameters"]["required"]) == {"file", "start", "end", "replace"}

    def test_non_creation_schedule_is_unchanged(self) -> None:
        # Identical inputs without the creation flag stay in the free phase at
        # index 1 and only force on the final attempt — behaviour preserved.
        free_defs, free_choice = resolve_retry_escalation(
            attempt_index=1,
            max_retry_attempts=4,
            strict_tool_definitions=_STRICT_DEFS,
            forced_write_tool_name="write_file",
        )
        assert free_defs is None
        assert free_choice is None

    def test_creation_low_temperature_phase_shifts_with_force(self) -> None:
        # Forced create starts at index 0, so temperature drops immediately to
        # the deterministic transcription temperature.
        assert (
            resolve_retry_temperature_override(attempt_index=0, force_write_immediately=True)
            == resolve_escalation_temperature()
        )
        # Non-creation index 1 stays at profile temperature (unchanged).
        assert resolve_retry_temperature_override(attempt_index=1) is None


class TestDetectCreationMode:
    def test_missing_target_is_creation(self, tmp_path) -> None:
        assert detect_creation_mode(str(tmp_path), ("index.html",)) is True

    def test_any_missing_target_is_creation(self, tmp_path) -> None:
        (tmp_path / "a.js").write_text("x", encoding="utf-8")
        assert detect_creation_mode(str(tmp_path), ("a.js", "b.js")) is True

    def test_all_existing_targets_is_not_creation(self, tmp_path) -> None:
        (tmp_path / "a.js").write_text("x", encoding="utf-8")
        assert detect_creation_mode(str(tmp_path), ("a.js",)) is False

    def test_no_targets_is_not_creation(self, tmp_path) -> None:
        assert detect_creation_mode(str(tmp_path), ()) is False

    def test_no_workspace_is_not_creation(self) -> None:
        assert detect_creation_mode("", ("index.html",)) is False

    def test_extensionless_stem_phantom_does_not_flag_existing_file(self, tmp_path) -> None:
        # The message extractor yields BOTH "README.md" and the bare stem
        # "README" for "修改 README.md"; the stem never exists on disk and must
        # not mis-classify an edit-to-existing task as a from-scratch create.
        (tmp_path / "README.md").write_text("# r\n", encoding="utf-8")
        assert detect_creation_mode(str(tmp_path), ("README.md", "README")) is False

    def test_stem_filter_preserves_distinct_missing_targets(self, tmp_path) -> None:
        # Round-7 semantics: a genuinely distinct missing target still flags a
        # create even when another listed target already exists.
        (tmp_path / "quotes.json").write_text("[]", encoding="utf-8")
        assert detect_creation_mode(str(tmp_path), ("quotes.json", "index.html", "script.js")) is True


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
        # write_file rides along since the new-file deadlock fix (2026-06-12).
        assert names == {"edit_blocks", "write_file", "execute_command"}

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
        assert names == {"edit_blocks", "write_file"}

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


def test_forced_edit_blocks_set_includes_write_file_companion() -> None:
    """factory-bench live deadlock: teaching error says 'use write_file' for
    new files, so the narrowed escalation set must actually offer it."""
    from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
        build_forced_write_only_retry_tool_definitions,
    )

    defs = [*_STRICT_DEFS, {"type": "function", "function": {"name": "execute_command"}}]
    narrowed = build_forced_write_only_retry_tool_definitions(defs, "edit_blocks", include_verification_tools=True)
    names = {d["function"]["name"] for d in narrowed}
    assert names == {"edit_blocks", "write_file", "execute_command"}


class TestExistenceAwareForcedTool:
    """L1-05 round-6 regression: creation tasks must force write_file, not
    lock guided decoding onto edit_blocks (which cannot create files)."""

    _DEFS = [
        {"type": "function", "function": {"name": "edit_blocks"}},
        {"type": "function", "function": {"name": "write_file"}},
    ]

    def test_missing_targets_force_write_file(self, tmp_path) -> None:
        from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
            select_retry_forced_write_tool_name,
        )

        selected = select_retry_forced_write_tool_name(
            self._DEFS, workspace=str(tmp_path), target_files=("index.html", "script.js")
        )
        assert selected == "write_file"

    def test_existing_target_keeps_edit_blocks(self, tmp_path) -> None:
        from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
            select_retry_forced_write_tool_name,
        )

        (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
        selected = select_retry_forced_write_tool_name(self._DEFS, workspace=str(tmp_path), target_files=("main.py",))
        assert selected == "edit_blocks"

    def test_no_target_info_keeps_legacy_order(self) -> None:
        from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
            select_retry_forced_write_tool_name,
        )

        assert select_retry_forced_write_tool_name(self._DEFS) == "edit_blocks"


def test_partially_missing_targets_force_write_file(tmp_path) -> None:
    """Round-7 regression: one created file must not lock the remaining
    missing targets back onto edit_blocks (any-missing => creation mode)."""
    from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
        select_retry_forced_write_tool_name,
    )

    defs = [
        {"type": "function", "function": {"name": "edit_blocks"}},
        {"type": "function", "function": {"name": "write_file"}},
    ]
    (tmp_path / "quotes.json").write_text("[]", encoding="utf-8")
    selected = select_retry_forced_write_tool_name(
        defs,
        workspace=str(tmp_path),
        target_files=("quotes.json", "index.html", "script.js"),
    )
    assert selected == "write_file"
