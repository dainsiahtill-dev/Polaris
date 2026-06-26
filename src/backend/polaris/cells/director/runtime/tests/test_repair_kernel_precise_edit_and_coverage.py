"""Precise edit and coverage-gap hardening tests for the repair kernel."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.director.runtime.internal.repair_kernel import (
    PatchComposer,
    RepairDiagnostic,
    RepairOperation,
    RepairPlan,
    TransactionalRepairExecutor,
    default_repair_rule_registry,
    repair_language_slots,
    runtime_repair_bindings,
)
from polaris.cells.director.runtime.internal.repair_kernel.composer import _unique_occurrence_count_limited
from polaris.cells.director.runtime.internal.repair_kernel.contracts import FILE_ABSENT_HASH, sha256_text


def test_patch_composer_applies_multispan_text_replacements_and_detects_conflicts() -> None:
    base = {"src/app.ts": "alpha beta gamma delta"}
    operations = (
        RepairOperation(
            kind="text_replace",
            path="src/app.ts",
            span_start=0,
            span_end=5,
            expected="alpha",
            replacement="one",
        ),
        RepairOperation(
            kind="text_replace",
            path="src/app.ts",
            span_start=11,
            span_end=16,
            expected="gamma",
            replacement="three",
        ),
    )

    result = PatchComposer().compose(base, operations)

    assert result.ok
    assert result.patches[0].content_after == "one beta three delta"
    assert result.patches[0].metadata["large_file_safe"] is True
    assert result.patches[0].metadata["span_based"] is True

    conflict = PatchComposer().compose(
        {"src/app.ts": "abcdef"},
        (
            RepairOperation(kind="text_replace", path="src/app.ts", span_start=1, span_end=4, replacement="X"),
            RepairOperation(kind="text_replace", path="src/app.ts", span_start=3, span_end=5, replacement="Y"),
        ),
    )

    assert not conflict.ok
    assert conflict.issues[0].code == "overlapping_text_spans"


def test_patch_composer_requires_unique_context_when_expected_is_missing() -> None:
    content = "call target();\ncall target();\n"
    start = content.index("target")
    ambiguous = RepairOperation(
        kind="text_replace",
        path="src/app.ts",
        span_start=start,
        span_end=start + len("target"),
        replacement="fixed",
        metadata={
            "expected_context_before": "call ",
            "expected_context_after": "();",
        },
    )

    result = PatchComposer().compose({"src/app.ts": content}, (ambiguous,))

    assert not result.ok
    assert result.patches == ()
    assert result.issues[0].code == "text_context_not_unique"
    assert result.issues[0].metadata["unique_context_checked"] is True

    unique_content = "only call target();\nother call target();\n"
    unique_start = unique_content.index("target")
    unique = RepairOperation(
        kind="text_replace",
        path="src/app.ts",
        span_start=unique_start,
        span_end=unique_start + len("target"),
        replacement="fixed",
        metadata={"unique_context": "only call target();"},
    )

    unique_result = PatchComposer().compose({"src/app.ts": unique_content}, (unique,))

    assert unique_result.ok
    assert unique_result.patches[0].content_after == "only call fixed();\nother call target();\n"
    assert unique_result.patches[0].metadata["unique_context_checked"] is True


def test_unique_context_duplicate_probe_stops_after_second_occurrence() -> None:
    class FindCountingContent(str):
        find_calls: int

        def __new__(cls, value: str) -> FindCountingContent:
            instance = str.__new__(cls, value)
            instance.find_calls = 0
            return instance

        def find(self, sub: str, start: int = 0, end: int = -1) -> int:
            self.find_calls += 1
            if end == -1:
                return super().find(sub, start)
            return super().find(sub, start, end)

    content = FindCountingContent("target" + ("x" * 4096) + "target" + (" target" * 32))

    match_count, first_match = _unique_occurrence_count_limited(content, "target")

    assert (match_count, first_match) == (2, 0)
    assert content.find_calls == 2

    operation = RepairOperation(
        kind="text_replace",
        path="src/big.ts",
        span_start=0,
        span_end=len("target"),
        replacement="fixed",
        metadata={"unique_context": "target"},
    )

    result = PatchComposer().compose({"src/big.ts": str(content)}, (operation,))

    assert not result.ok
    assert result.issues[0].code == "text_context_not_unique"
    assert result.issues[0].metadata["match_count"] == 2
    assert result.issues[0].metadata["match_count_limited"] is True


def test_executor_prefers_policy_gated_editor_and_records_write_file_reason(tmp_path: Path) -> None:
    relative_path = "src/app.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    original = "export const pending = true;\n"
    target.write_text(original, encoding="utf-8")
    start = original.index("pending")
    operation = RepairOperation(
        kind="text_replace",
        path=relative_path,
        span_start=start,
        span_end=start + len("pending"),
        expected="pending",
        replacement="done",
    )
    plan = RepairPlan(
        rule_id="typescript.precise_pending_export",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(operation,),
    )
    composition = PatchComposer().compose({relative_path: original}, plan.operations)
    edit_calls: list[str] = []
    write_calls: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        write_calls.append(path)
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    def editor(edit_operation: RepairOperation) -> dict[str, object]:
        current = target.read_text(encoding="utf-8")
        assert edit_operation.span_start is not None
        assert edit_operation.span_end is not None
        updated = (
            current[: edit_operation.span_start]
            + str(edit_operation.replacement or "")
            + current[edit_operation.span_end :]
        )
        target.write_text(updated, encoding="utf-8")
        edit_calls.append(edit_operation.operation_id)
        return {"ok": True}

    result = TransactionalRepairExecutor().execute(
        workspace=tmp_path,
        plan=plan,
        composition=composition,
        writer=writer,
        editor=editor,
    )

    assert result.ok
    assert target.read_text(encoding="utf-8") == "export const done = true;\n"
    assert edit_calls == [operation.operation_id]
    assert write_calls == []
    assert result.receipt.metadata["execution_records"][0]["operation"] == "edit_file"
    assert result.receipt.metadata["write_file_reasons_by_path"] == {}

    whole_file_plan = RepairPlan(
        rule_id="typescript.whole_file_fallback",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(
            RepairOperation(
                kind="write_file",
                path=relative_path,
                content="export const replacement = true;\n",
            ),
        ),
    )
    current = target.read_text(encoding="utf-8")
    whole_file_composition = PatchComposer().compose({relative_path: current}, whole_file_plan.operations)

    whole_file_result = TransactionalRepairExecutor().execute(
        workspace=tmp_path,
        plan=whole_file_plan,
        composition=whole_file_composition,
        writer=writer,
    )

    assert whole_file_result.ok
    assert whole_file_result.receipt.metadata["write_file_reasons_by_path"] == {
        relative_path: "fallback_whole_file_repair"
    }


def test_executor_records_full_file_rollback_strategy_after_editor_forward_failure(tmp_path: Path) -> None:
    first_path = "src/a.ts"
    second_path = "src/b.ts"
    first_target = tmp_path / first_path
    second_target = tmp_path / second_path
    first_target.parent.mkdir(parents=True)
    first_original = "export const first = false;\n"
    second_original = "export const second = false;\n"
    first_target.write_text(first_original, encoding="utf-8")
    second_target.write_text(second_original, encoding="utf-8")

    first_start = first_original.index("false")
    second_start = second_original.index("false")
    first_operation = RepairOperation(
        kind="text_replace",
        path=first_path,
        span_start=first_start,
        span_end=first_start + len("false"),
        expected="false",
        replacement="true",
    )
    second_operation = RepairOperation(
        kind="text_replace",
        path=second_path,
        span_start=second_start,
        span_end=second_start + len("false"),
        expected="false",
        replacement="true",
    )
    plan = RepairPlan(
        rule_id="typescript.rollback_strategy_evidence",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(first_operation, second_operation),
    )
    composition = PatchComposer().compose(
        {
            first_path: first_original,
            second_path: second_original,
        },
        plan.operations,
    )
    edit_calls: list[str] = []
    write_calls: list[tuple[str, str]] = []

    def writer(path: str, content: str) -> dict[str, object]:
        write_calls.append((path, content))
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    def editor(edit_operation: RepairOperation) -> dict[str, object]:
        edit_calls.append(edit_operation.path)
        if edit_operation.path == second_path:
            return {"ok": False}
        assert edit_operation.span_start is not None
        assert edit_operation.span_end is not None
        target = tmp_path / edit_operation.path
        current = target.read_text(encoding="utf-8")
        updated = (
            current[: edit_operation.span_start]
            + str(edit_operation.replacement or "")
            + current[edit_operation.span_end :]
        )
        target.write_text(updated, encoding="utf-8")
        return {"ok": True}

    result = TransactionalRepairExecutor().execute(
        workspace=tmp_path,
        plan=plan,
        composition=composition,
        writer=writer,
        editor=editor,
    )

    assert not result.ok
    assert result.rolled_back is True
    assert first_target.read_text(encoding="utf-8") == first_original
    assert second_target.read_text(encoding="utf-8") == second_original
    assert edit_calls == [first_path, second_path]
    assert write_calls == [(first_path, first_original)]
    assert result.receipt.metadata["rollback_attempted"] is True
    assert result.receipt.metadata["rollback_restore_strategy"] == "write_file_full_restore"
    assert result.receipt.metadata["rollback_success_count"] == 1
    execution_records = result.receipt.metadata["execution_records"]
    assert execution_records[0]["operation"] == "edit_file"
    assert execution_records[0]["rollback_restore_strategy"] == "write_file_full_restore"


def test_patch_composer_supports_delete_file_fail_closed() -> None:
    content = "export const stale = true;\n"
    operation = RepairOperation(
        kind="delete_file",
        path="src/stale.ts",
        before_hash=sha256_text(content),
    )

    result = PatchComposer().compose({"src/stale.ts": content}, (operation,))

    assert result.ok
    patch = result.patches[0]
    assert patch.content_before == content
    assert patch.content_after == ""
    assert patch.before_hash == sha256_text(content)
    assert patch.after_hash == FILE_ABSENT_HASH
    assert patch.exists_before is True
    assert patch.exists_after is False
    assert patch.metadata["deleted_file"] is True
    assert patch.metadata["created_or_deleted"] == "deleted"

    missing = PatchComposer().compose({}, (RepairOperation(kind="delete_file", path="src/stale.ts"),))

    assert not missing.ok
    assert missing.issues[0].code == "delete_file_missing_base_file"

    mismatch = PatchComposer().compose(
        {"src/stale.ts": content},
        (
            RepairOperation(
                kind="delete_file",
                path="src/stale.ts",
                before_hash=sha256_text("different\n"),
            ),
        ),
    )

    assert not mismatch.ok
    assert mismatch.issues[0].code == "before_hash_mismatch"

    unsafe = PatchComposer().compose(
        {"../stale.ts": content},
        (RepairOperation(kind="delete_file", path="../stale.ts"),),
    )

    assert not unsafe.ok
    assert unsafe.issues[0].code == "invalid_path"


def test_executor_records_delete_file_operation_and_receipt_evidence(tmp_path: Path) -> None:
    relative_path = "src/stale.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    original = "export const stale = true;\n"
    target.write_text(original, encoding="utf-8")
    operation = RepairOperation(
        kind="delete_file",
        path=relative_path,
        before_hash=sha256_text(original),
    )
    plan = RepairPlan(
        rule_id="generic.file_ops_transaction",
        source_tool="deterministic_patch_residue_cleanup",
        operations=(operation,),
    )
    composition = PatchComposer().compose({relative_path: original}, plan.operations)
    delete_calls: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    def deleter(path: str) -> dict[str, object]:
        delete_calls.append(path)
        (tmp_path / path).unlink()
        return {"ok": True}

    result = TransactionalRepairExecutor().execute(
        workspace=tmp_path,
        plan=plan,
        composition=composition,
        writer=writer,
        deleter=deleter,
    )

    assert result.ok
    assert not target.exists()
    assert delete_calls == [relative_path]
    assert result.receipt.before_hashes[relative_path] == sha256_text(original)
    assert result.receipt.after_hashes[relative_path] == FILE_ABSENT_HASH
    record = result.receipt.metadata["execution_records"][0]
    assert record["operation"] == "delete_file"
    assert record["before_hash"] == sha256_text(original)
    assert record["after_hash"] == FILE_ABSENT_HASH
    assert record["deleted_file"] is True
    assert record["created_or_deleted"] == "deleted"
    assert record["rollback_strategy"] == "write_file_full_restore"


def test_executor_restores_deleted_file_after_later_failure(tmp_path: Path) -> None:
    stale_path = "src/a-stale.ts"
    config_path = "src/b-config.ts"
    stale = tmp_path / stale_path
    config = tmp_path / config_path
    stale.parent.mkdir(parents=True)
    stale_original = "export const stale = true;\n"
    config_original = "export const config = false;\n"
    stale.write_text(stale_original, encoding="utf-8")
    config.write_text(config_original, encoding="utf-8")
    plan = RepairPlan(
        rule_id="generic.file_ops_transaction",
        source_tool="deterministic_patch_residue_cleanup",
        operations=(
            RepairOperation(
                kind="delete_file",
                path=stale_path,
                before_hash=sha256_text(stale_original),
            ),
            RepairOperation(
                kind="write_file",
                path=config_path,
                content="export const config = true;\n",
            ),
        ),
    )
    composition = PatchComposer().compose(
        {
            stale_path: stale_original,
            config_path: config_original,
        },
        plan.operations,
    )
    write_calls: list[tuple[str, str]] = []
    delete_calls: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        write_calls.append((path, content))
        if path == config_path:
            return {"ok": False}
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    def deleter(path: str) -> dict[str, object]:
        delete_calls.append(path)
        (tmp_path / path).unlink()
        return {"ok": True}

    result = TransactionalRepairExecutor().execute(
        workspace=tmp_path,
        plan=plan,
        composition=composition,
        writer=writer,
        deleter=deleter,
    )

    assert not result.ok
    assert result.rolled_back is True
    assert stale.read_text(encoding="utf-8") == stale_original
    assert config.read_text(encoding="utf-8") == config_original
    assert delete_calls == [stale_path]
    assert write_calls == [(config_path, "export const config = true;\n"), (stale_path, stale_original)]
    assert result.receipt.metadata["rollback_success_count"] == 1
    record = result.receipt.metadata["execution_records"][0]
    assert record["operation"] == "delete_file"
    assert record["deleted_file"] is True
    assert record["rollback_strategy"] == "write_file_full_restore"


def test_executor_create_file_rollback_requires_delete_tool_after_later_failure(tmp_path: Path) -> None:
    created_path = "src/new-file.ts"
    config_path = "src/z-config.ts"
    config = tmp_path / config_path
    config.parent.mkdir(parents=True)
    config_original = "export const config = false;\n"
    config.write_text(config_original, encoding="utf-8")
    plan = RepairPlan(
        rule_id="generic.file_ops_transaction",
        source_tool="deterministic_patch_residue_cleanup",
        operations=(
            RepairOperation(
                kind="write_file",
                path=created_path,
                content="export const created = true;\n",
            ),
            RepairOperation(
                kind="write_file",
                path=config_path,
                content="export const config = true;\n",
            ),
        ),
    )
    composition = PatchComposer().compose({config_path: config_original}, plan.operations)
    write_calls: list[tuple[str, str]] = []

    def writer(path: str, content: str) -> dict[str, object]:
        write_calls.append((path, content))
        if path == config_path:
            return {"ok": False}
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True}

    result = TransactionalRepairExecutor().execute(
        workspace=tmp_path,
        plan=plan,
        composition=composition,
        writer=writer,
    )

    assert not result.ok
    assert result.rolled_back is False
    assert (tmp_path / created_path).read_text(encoding="utf-8") == "export const created = true;\n"
    assert config.read_text(encoding="utf-8") == config_original
    assert write_calls == [
        (created_path, "export const created = true;\n"),
        (config_path, "export const config = true;\n"),
    ]
    assert result.receipt.status == "rollback_failed"
    assert result.receipt.metadata["rollback_failed_paths"] == [
        f"{created_path}:rollback_requires_delete_tool"
    ]
    record = result.receipt.metadata["execution_records"][0]
    assert record["operation"] == "write_file"
    assert record["created_file"] is True
    assert record["created_or_deleted"] == "created"
    assert record["rollback_strategy"] == "delete_created_file"
    assert record["rollback_requires_delete_tool"] is True


def test_composer_reserves_toml_and_yaml_structured_operations_fail_closed(tmp_path: Path) -> None:
    reserved_cases = (
        ("toml_set", "pyproject.toml", ("tool", "example", "enabled"), True, "[tool.example]\n", "toml"),
        ("toml_delete", "pyproject.toml", ("tool", "example", "enabled"), None, "[tool.example]\n", "toml"),
        ("yaml_set", "config.yaml", ("tool", "example", "enabled"), True, "tool:\n  example: {}\n", "yaml"),
        ("yaml_delete", "config.yaml", ("tool", "example", "enabled"), None, "tool:\n  example:\n    enabled: true\n", "yaml"),
    )
    write_calls: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        write_calls.append(path)
        return {"ok": True}

    for kind, path, json_path, value, content, structured_format in reserved_cases:
        operation = RepairOperation(
            kind=kind,
            path=path,
            json_path=json_path,
            value=value,
        )
        result = PatchComposer().compose(
            {path: content},
            (operation,),
        )

        assert not result.ok
        assert result.patches == ()
        issue = result.issues[0]
        assert issue.code == "reserved_structured_operation"
        assert issue.metadata["structured_operation_reserved"] is True
        assert issue.metadata["structured_format"] == structured_format
        assert issue.metadata["structured_formats"] == [structured_format]
        assert issue.metadata["languages"] == [structured_format]
        assert issue.metadata["requires_parser"] is True
        assert issue.metadata["parser_available"] is False
        assert issue.metadata["format_preservation_unproven"] is True
        assert issue.metadata["manual_runtime_rule_required"] is True
        assert issue.metadata["executable_structured_composer"] is False
        assert issue.metadata["write_file_fallback_allowed"] is False
        assert issue.metadata["write_file_reason"] == "reserved_structured_serialization_requires_parser"

        execution = TransactionalRepairExecutor().execute(
            workspace=tmp_path,
            plan=RepairPlan(
                rule_id=f"{structured_format}.structured_reserved",
                source_tool="deterministic_rust_dependency_repair",
                operations=(operation,),
            ),
            composition=result,
            writer=writer,
        )

        assert not execution.ok
        assert execution.error == "composition_failed"
        assert execution.receipt.status == "composition_failed"

    unsupported = PatchComposer().compose(
        {"config.yaml": "enabled: false\n"},
        (RepairOperation(kind="yaml_merge", path="config.yaml", value={"enabled": True}),),
    )

    assert not unsupported.ok
    assert unsupported.patches == ()
    unsupported_issue = unsupported.issues[0]
    assert unsupported_issue.code == "unsupported_operation"
    assert unsupported_issue.metadata["structured_operation_reserved"] is False
    assert unsupported_issue.metadata["structured_format"] == "yaml"
    assert unsupported_issue.metadata["requires_parser"] is True
    assert unsupported_issue.metadata["parser_available"] is False
    assert unsupported_issue.metadata["format_preservation_unproven"] is True
    assert unsupported_issue.metadata["manual_runtime_rule_required"] is True
    assert unsupported_issue.metadata["executable_structured_composer"] is False
    assert unsupported_issue.metadata["write_file_fallback_allowed"] is False

    mixed = PatchComposer().compose(
        {"pyproject.toml": "[tool.example]\n"},
        (
            RepairOperation(
                kind="toml_set",
                path="pyproject.toml",
                json_path=("tool", "example", "enabled"),
                value=True,
            ),
            RepairOperation(
                kind="write_file",
                path="pyproject.toml",
                content="[tool.example]\nenabled = true\n",
            ),
        ),
    )

    assert not mixed.ok
    assert mixed.patches == ()
    mixed_issue = mixed.issues[0]
    assert mixed_issue.code == "write_file_conflict"
    assert mixed_issue.metadata["structured_format"] == "toml"
    assert mixed_issue.metadata["write_file_fallback_allowed"] is False

    mixed_execution = TransactionalRepairExecutor().execute(
        workspace=tmp_path,
        plan=RepairPlan(
            rule_id="rust.toml_structured_reserved",
            source_tool="deterministic_rust_dependency_repair",
            operations=(
                RepairOperation(
                    kind="toml_set",
                    path="pyproject.toml",
                    json_path=("tool", "example", "enabled"),
                    value=True,
                ),
            ),
        ),
        composition=mixed,
        writer=writer,
    )

    assert not mixed_execution.ok
    assert mixed_execution.error == "composition_failed"
    assert mixed_execution.receipt.status == "composition_failed"
    assert write_calls == []


def test_coverage_gap_includes_reserved_slot_and_recommended_owner() -> None:
    diagnostic = RepairDiagnostic(
        source="compiler",
        code="ruby_future_error",
        message="uninitialized constant Widget",
        path="app/models/widget.rb",
        raw="app/models/widget.rb:3: uninitialized constant Widget",
    )

    report = default_repair_rule_registry().coverage((diagnostic,))
    payload = report.to_dict()
    gap = payload["coverage_gaps"][0]

    assert payload["coverage_gap_languages"] == ["ruby"]
    assert payload["coverage_gap_archetypes"] == ["unknown"]
    assert payload["coverage_gap_diagnostic_codes"] == ["ruby_future_error"]
    assert payload["coverage_gap_handoff_recommendations"] == ["llm_triage_then_runtime_rule"]
    assert gap["diagnostic_language"] == "ruby"
    assert gap["diagnostic_code"] == "ruby_future_error"
    assert gap["diagnostic_archetype"] == "unknown"
    assert gap["reserved_language_slot_matched"] is True
    assert gap["reserved_language_slot"]["language"] == "ruby"
    assert gap["reserved_repairer_module"].endswith(".ruby_runtime")
    assert gap["reserved_slot_registration_policy"] == "bench_verified_rule_required"
    assert gap["recommended_next_owner"] == "runtime_rule"
    assert gap["handoff_recommendation"] == "llm_triage_then_runtime_rule"
    assert gap["llm_advisory_recommended"] is True
    assert gap["agi_advisory_recommended"] is False
    assert gap["authoritative_rule_registration_allowed"] is False
    assert gap["recommended_registration_path"] == "bench_verified_rule_required"


def test_future_language_slots_are_reserved_only_without_runtime_false_positive() -> None:
    slots = {slot.language: slot for slot in repair_language_slots()}
    runtime_languages = {str(binding["language"]) for binding in runtime_repair_bindings()}

    for language in ("ruby", "php", "csharp", "kotlin", "swift", "lua", "shell", "sql"):
        assert language in slots
        assert slots[language].repairer_module.endswith(f".{language}_runtime")
        assert language not in runtime_languages
