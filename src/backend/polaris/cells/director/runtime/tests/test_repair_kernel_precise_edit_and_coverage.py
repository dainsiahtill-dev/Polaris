"""Precise edit and coverage-gap hardening tests for the repair kernel."""

from __future__ import annotations

from pathlib import Path
from typing import SupportsIndex

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
    assert len(conflict.issues[0].operation_ids) == 2
    assert conflict.issues[0].metadata["same_file_multi_patch_conflict"] is True
    assert conflict.issues[0].metadata["current_span"] == {"start": 1, "end": 4}
    assert conflict.issues[0].metadata["previous_span"] == {"start": 3, "end": 5}


def test_patch_composer_requires_unique_context_when_expected_is_missing() -> None:
    content = "call target();\ncall target();\n"
    start = content.index("target")
    missing_precondition = RepairOperation(
        kind="text_replace",
        path="src/app.ts",
        span_start=start,
        span_end=start + len("target"),
        replacement="fixed",
    )

    missing_result = PatchComposer().compose({"src/app.ts": content}, (missing_precondition,))

    assert not missing_result.ok
    assert missing_result.patches == ()
    assert missing_result.issues[0].code == "missing_text_precondition"
    assert missing_result.issues[0].metadata["requires_expected_or_unique_context"] is True

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
    assert unique_result.patches[0].metadata["precision_strategy"] == "span_context_text_patch"
    assert unique_result.patches[0].metadata["write_file_fallback_allowed"] is True


def test_unique_context_duplicate_probe_stops_after_second_occurrence() -> None:
    class FindCountingContent(str):
        find_calls: int

        def __new__(cls, value: str) -> FindCountingContent:
            instance = str.__new__(cls, value)
            instance.find_calls = 0
            return instance

        def find(
            self,
            sub: str,
            start: SupportsIndex | None = 0,
            end: SupportsIndex | None = -1,
        ) -> int:
            self.find_calls += 1
            normalized_start: SupportsIndex = 0 if start is None else start
            if end is None or end.__index__() == -1:
                return super().find(sub, normalized_start)
            return super().find(sub, normalized_start, end)

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


def test_patch_composer_requires_unique_context_for_large_file_text_replace() -> None:
    lines = [f"export const value_{index} = {index};\n" for index in range(1200)]
    target_line = "export const targetFlag = false;\n"
    lines.insert(900, target_line)
    content = "".join(lines)
    start = content.index("false")
    bare_span = RepairOperation(
        kind="text_replace",
        path="src/large.ts",
        span_start=start,
        span_end=start + len("false"),
        expected="false",
        replacement="true",
    )

    missing_context = PatchComposer().compose({"src/large.ts": content}, (bare_span,))

    assert not missing_context.ok
    assert missing_context.issues[0].code == "missing_large_file_unique_context"
    assert missing_context.issues[0].metadata["requires_unique_context_for_large_file"] is True
    assert missing_context.issues[0].metadata["span_based"] is True
    assert missing_context.issues[0].metadata["unique_context_checked"] is False

    precise_span = RepairOperation(
        kind="text_replace",
        path="src/large.ts",
        span_start=start,
        span_end=start + len("false"),
        expected="false",
        replacement="true",
        metadata={"unique_context": target_line},
    )

    precise_result = PatchComposer().compose({"src/large.ts": content}, (precise_span,))

    assert precise_result.ok
    assert "export const targetFlag = true;\n" in precise_result.patches[0].content_after
    assert precise_result.patches[0].metadata["large_file_safe"] is True
    assert precise_result.patches[0].metadata["unique_context_checked"] is True


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
    whole_file_record = whole_file_result.receipt.metadata["execution_records"][0]
    assert whole_file_record["write_file_allowed_category"] == "fallback"
    assert whole_file_record["write_file_policy_decision"] == "allowed_fallback"
    assert whole_file_record["write_file_fallback_source"] == "explicit_write_file_operation"


def test_executor_records_write_file_fallback_when_precise_editor_is_unavailable(tmp_path: Path) -> None:
    relative_path = "src/app.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    original = "export const feature = false;\n"
    target.write_text(original, encoding="utf-8")
    start = original.index("false")
    operation = RepairOperation(
        kind="text_replace",
        path=relative_path,
        span_start=start,
        span_end=start + len("false"),
        expected="false",
        replacement="true",
        metadata={"unique_context": "export const feature = false;\n"},
    )
    plan = RepairPlan(
        rule_id="typescript.precise_writer_fallback",
        source_tool="deterministic_typescript_nullable_canvas_context_repair",
        operations=(operation,),
    )
    composition = PatchComposer().compose({relative_path: original}, plan.operations)
    write_calls: list[tuple[str, str]] = []

    def writer(path: str, content: str) -> dict[str, object]:
        write_calls.append((path, content))
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    result = TransactionalRepairExecutor().execute(
        workspace=tmp_path,
        plan=plan,
        composition=composition,
        writer=writer,
    )

    assert result.ok
    assert target.read_text(encoding="utf-8") == "export const feature = true;\n"
    assert write_calls == [(relative_path, "export const feature = true;\n")]
    record = result.receipt.metadata["execution_records"][0]
    assert record["operation"] == "write_file"
    assert record["span_based"] is True
    assert record["unique_context_checked"] is True
    assert record["write_file_reason"] == "fallback_whole_file_repair"
    assert record["write_file_allowed_category"] == "fallback"
    assert record["write_file_policy_decision"] == "allowed_fallback"
    assert record["write_file_fallback_source"] == "span_context_text_patch_editor_unavailable_or_rejected"
    assert record["precise_edit_strategy"] == {
        "strategy": "write_file_fallback",
        "span_based": True,
        "unique_context_checked": True,
        "editor_preferred": True,
        "editor_used": False,
        "write_file_used": True,
        "write_file_fallback_source": "span_context_text_patch_editor_unavailable_or_rejected",
        "large_file_safe": True,
        "operation_ids": [operation.operation_id],
    }
    assert result.receipt.metadata["precise_edit_strategy"] == {
        "strategy": "span_based",
        "span_based": True,
        "unique_context_checked": True,
        "editor_preferred": True,
        "editor_used": False,
        "write_file_used": True,
        "write_file_fallback_paths": [relative_path],
        "large_file_safe": True,
        "paths": [relative_path],
    }


def test_executor_large_file_span_edit_uses_editor_and_projects_rollback_evidence(tmp_path: Path) -> None:
    large_path = "src/a-large.ts"
    fail_path = "src/b-fail.ts"
    large_target = tmp_path / large_path
    fail_target = tmp_path / fail_path
    large_target.parent.mkdir(parents=True)
    large_lines = [f"export const value_{index} = {index};\n" for index in range(2200)]
    target_line = "export const target_1999 = false;\n"
    large_lines.insert(1999, target_line)
    large_original = "".join(large_lines)
    fail_original = "export const fail = false;\n"
    large_target.write_text(large_original, encoding="utf-8")
    fail_target.write_text(fail_original, encoding="utf-8")

    large_start = large_original.index("false")
    fail_start = fail_original.index("false")
    large_operation = RepairOperation(
        kind="text_replace",
        path=large_path,
        span_start=large_start,
        span_end=large_start + len("false"),
        expected="false",
        replacement="true",
        metadata={"unique_context": target_line},
    )
    fail_operation = RepairOperation(
        kind="text_replace",
        path=fail_path,
        span_start=fail_start,
        span_end=fail_start + len("false"),
        expected="false",
        replacement="true",
    )
    plan = RepairPlan(
        rule_id="typescript.large_file_precise_edit_rollback",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(large_operation, fail_operation),
    )
    composition = PatchComposer().compose(
        {
            large_path: large_original,
            fail_path: fail_original,
        },
        plan.operations,
    )
    write_calls: list[tuple[str, str]] = []
    edit_calls: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        write_calls.append((path, content))
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    def editor(edit_operation: RepairOperation) -> dict[str, object]:
        edit_calls.append(edit_operation.path)
        if edit_operation.path == fail_path:
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
    assert large_target.read_text(encoding="utf-8") == large_original
    assert fail_target.read_text(encoding="utf-8") == fail_original
    assert edit_calls == [large_path, fail_path]
    assert write_calls == [(large_path, large_original)]

    metadata = result.receipt.metadata
    assert metadata["rollback_strategy"] == "write_file_full_restore"
    assert metadata["rollback_success_count"] == 1
    assert metadata["rollback_failed_paths"] == []
    assert metadata["rollback_patch"] == [
        {
            "path": large_path,
            "rollback_patch_id": f"rollback:write_file_full_restore:{large_path}",
            "rollback_strategy": "write_file_full_restore",
            "rollback_operation": "write_file",
            "rollback_reason": "transaction_failure",
            "rollback_policy_decision": "allowed_rollback",
            "before_hash": sha256_text(large_original.replace("false", "true", 1)),
            "after_hash": sha256_text(large_original),
            "exists_before": True,
            "exists_after": True,
            "operation_ids": [large_operation.operation_id],
        }
    ]
    assert metadata["rollback_operations"] == [
        {
            "path": large_path,
            "rollback_patch_id": f"rollback:write_file_full_restore:{large_path}",
            "operation": "write_file",
            "rollback_strategy": "write_file_full_restore",
            "rollback_reason": "transaction_failure",
            "rollback_policy_decision": "allowed_rollback",
            "write_file_allowed_category": "rollback",
            "write_file_reason": "rollback_full_restore",
            "before_hash": sha256_text(large_original.replace("false", "true", 1)),
            "after_hash": sha256_text(large_original),
            "exists_before": True,
            "exists_after": True,
            "operation_ids": [large_operation.operation_id],
            "ok": True,
        }
    ]

    record = metadata["execution_records"][0]
    assert record["operation"] == "edit_file"
    assert record["span_based"] is True
    assert record["unique_context_checked"] is True
    assert record["write_file_reason"] == ""
    assert record["precise_edit_strategy"] == {
        "strategy": "span_based",
        "span_based": True,
        "unique_context_checked": True,
        "editor_preferred": True,
        "editor_used": True,
        "write_file_used": False,
        "write_file_fallback_source": "",
        "large_file_safe": True,
        "operation_ids": [large_operation.operation_id],
    }
    assert metadata["precise_edit_strategy"] == {
        "strategy": "span_based",
        "span_based": True,
        "unique_context_checked": True,
        "editor_preferred": True,
        "editor_used": True,
        "write_file_used": False,
        "write_file_fallback_paths": [],
        "large_file_safe": True,
        "paths": [large_path],
    }
    assert metadata["precise_edit_strategy_by_path"][large_path]["write_file_used"] is False


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
    assert result.receipt.metadata["rollback_failed_paths"] == [f"{created_path}:rollback_requires_delete_tool"]
    assert result.receipt.metadata["rollback_strategy"] == "delete_created_file"
    assert result.receipt.metadata["rollback_patch"] == [
        {
            "path": created_path,
            "rollback_patch_id": f"rollback:delete_created_file:{created_path}",
            "rollback_strategy": "delete_created_file",
            "rollback_operation": "delete_file",
            "rollback_reason": "transaction_failure",
            "rollback_policy_decision": "allowed_rollback",
            "before_hash": sha256_text("export const created = true;\n"),
            "after_hash": FILE_ABSENT_HASH,
            "exists_before": True,
            "exists_after": False,
            "operation_ids": [plan.operations[0].operation_id],
        }
    ]
    assert result.receipt.metadata["rollback_operations"] == [
        {
            "path": created_path,
            "rollback_patch_id": f"rollback:delete_created_file:{created_path}",
            "operation": "delete_file",
            "rollback_strategy": "delete_created_file",
            "rollback_reason": "transaction_failure",
            "rollback_policy_decision": "allowed_rollback",
            "write_file_allowed_category": "",
            "write_file_reason": "",
            "before_hash": sha256_text("export const created = true;\n"),
            "after_hash": FILE_ABSENT_HASH,
            "exists_before": True,
            "exists_after": False,
            "operation_ids": [plan.operations[0].operation_id],
            "ok": False,
            "error": "rollback_requires_delete_tool",
        }
    ]
    record = result.receipt.metadata["execution_records"][0]
    assert record["operation"] == "write_file"
    assert record["created_file"] is True
    assert record["created_or_deleted"] == "created"
    assert record["rollback_strategy"] == "delete_created_file"
    assert record["rollback_requires_delete_tool"] is True


def test_composer_executes_toml_and_yaml_structured_operations(tmp_path: Path) -> None:
    structured_cases = (
        (
            "toml_set",
            "pyproject.toml",
            ("tool", "example", "enabled"),
            True,
            "[tool.example]\n",
            "toml",
            "[tool]\n\n[tool.example]\nenabled = true\n",
        ),
        (
            "toml_delete",
            "pyproject.toml",
            ("tool", "example", "enabled"),
            None,
            "[tool.example]\nenabled = true\n",
            "toml",
            "[tool]\n\n[tool.example]\n",
        ),
        (
            "yaml_set",
            "config.yaml",
            ("tool", "example", "enabled"),
            True,
            "tool:\n  example: {}\n",
            "yaml",
            "tool:\n  example:\n    enabled: true\n",
        ),
        (
            "yaml_delete",
            "config.yaml",
            ("tool", "example", "enabled"),
            None,
            "tool:\n  example:\n    enabled: true\n",
            "yaml",
            "tool:\n  example: {}\n",
        ),
    )
    write_calls: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        write_calls.append(path)
        return {"ok": True}

    for kind, path, json_path, value, content, structured_format, expected_content in structured_cases:
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

        assert result.ok
        assert result.patches[0].content_after == expected_content
        assert result.patches[0].metadata["structured_operation"] == structured_format
        assert result.patches[0].metadata["executable_structured_composer"] is True
        assert result.patches[0].metadata["parser_available"] is True
        assert result.patches[0].metadata["write_file_allowed_category"] == "structured_serialization"
        assert result.patches[0].metadata["write_file_reason"] == f"structured_{structured_format}_serialization"

        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
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

        assert execution.ok
        assert execution.receipt.status == "applied"

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
    assert sorted(write_calls) == ["config.yaml", "config.yaml", "pyproject.toml", "pyproject.toml"]


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
    assert payload["coverage_gap_recommended_routes"] == ["llm_repair"]
    assert payload["coverage_gap_slot_statuses"] == ["reserved_slot_available"]
    assert gap["language"] == "ruby"
    assert gap["diagnostic_language"] == "ruby"
    assert gap["diagnostic_code"] == "ruby_future_error"
    assert gap["diagnostic_archetype"] == "unknown"
    assert gap["archetype_suggestion"] == "unknown"
    assert gap["reserved_slot_available"] is True
    assert gap["slot_status"] == "reserved_slot_available"
    assert gap["reserved_language_slot_matched"] is True
    assert gap["reserved_language_slot"]["language"] == "ruby"
    assert gap["reserved_repairer_module"].endswith(".ruby_runtime")
    assert gap["reserved_slot_registration_policy"] == "bench_verified_rule_required"
    assert gap["recommended_next_owner"] == "runtime_rule"
    assert gap["recommended_route"] == "llm_repair"
    assert gap["handoff_recommendation"] == "llm_triage_then_runtime_rule"
    assert gap["llm_advisory_recommended"] is True
    assert gap["agi_advisory_recommended"] is False
    assert gap["authoritative_rule_registration_allowed"] is False
    assert gap["recommended_registration_path"] == "bench_verified_rule_required"
    assert gap["coverage_status"] == "coverage_gap"


def test_coverage_gap_for_language_without_slot_recommends_reserved_slot_addition() -> None:
    diagnostic = RepairDiagnostic(
        source="compiler",
        code="ballerina_future_error",
        message="undefined symbol Widget",
        path="src/main.bal",
        raw="src/main.bal:3: undefined symbol Widget",
        metadata={"language": "ballerina"},
    )

    payload = default_repair_rule_registry().coverage((diagnostic,)).to_dict()
    gap = payload["coverage_gaps"][0]

    assert payload["covered_diagnostic_count"] == 0
    assert payload["coverage_gap_languages"] == ["ballerina"]
    assert payload["coverage_gap_recommended_routes"] == ["add_reserved_slot"]
    assert payload["coverage_gap_slot_statuses"] == ["reserved_slot_missing"]
    assert gap["known_rule_matched"] is False
    assert gap["metadata_only_match"] is False
    assert gap["executable_runtime_plan_matched"] is False
    assert gap["language"] == "ballerina"
    assert gap["diagnostic_language"] == "ballerina"
    assert gap["diagnostic_code"] == "ballerina_future_error"
    assert gap["reserved_slot_available"] is False
    assert gap["reserved_language_slot_matched"] is False
    assert gap["slot_status"] == "reserved_slot_missing"
    assert gap["recommended_route"] == "add_reserved_slot"
    assert gap["recommended_registration_path"] == "coverage_report_then_bench_verified_rule"
    assert gap["audit_reason"] == "known_rule_matched=false"


def test_future_language_slots_are_reserved_only_without_runtime_false_positive() -> None:
    slots = {slot.language: slot for slot in repair_language_slots()}
    runtime_languages = {str(binding["language"]) for binding in runtime_repair_bindings()}

    for language in ("ruby", "php", "csharp", "kotlin", "swift", "lua", "shell", "sql"):
        assert language in slots
        assert slots[language].repairer_module.endswith(f".{language}_runtime")
        assert language not in runtime_languages
