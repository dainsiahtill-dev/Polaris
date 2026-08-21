"""Atomic mutation guarantees for Director repair execution."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.director.runtime.internal.repair_kernel.composer import PatchComposer
from polaris.cells.director.runtime.internal.repair_kernel.contracts import RepairOperation, RepairPlan, sha256_text
from polaris.cells.director.runtime.internal.repair_kernel.executor import TransactionalRepairExecutor


def _replace(target: Path, operation: RepairOperation) -> None:
    assert operation.span_start is not None
    assert operation.span_end is not None
    current = target.read_text(encoding="utf-8")
    target.write_text(
        current[: operation.span_start] + str(operation.replacement or "") + current[operation.span_end :],
        encoding="utf-8",
    )


def test_multi_edit_same_file_rebinds_each_editor_precondition(tmp_path: Path) -> None:
    path = "src/app.ts"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    before = "export const a = false; export const b = false;\n"
    target.write_text(before, encoding="utf-8")
    first = before.index("false")
    second = before.rindex("false")
    operations = (
        RepairOperation(
            kind="text_replace",
            path=path,
            span_start=first,
            span_end=first + 5,
            expected="false",
            replacement="true",
            before_hash=sha256_text(before),
            metadata={"expected_context_before": "a = "},
        ),
        RepairOperation(
            kind="text_replace",
            path=path,
            span_start=second,
            span_end=second + 5,
            expected="false",
            replacement="true",
            before_hash=sha256_text(before),
            metadata={"expected_context_before": "b = "},
        ),
    )
    plan = RepairPlan(rule_id="typescript.atomic_multi_edit", source_tool="test", operations=operations)
    composition = PatchComposer().compose({path: before}, operations)
    editor_hashes: list[str | None] = []
    writer_calls: list[str] = []

    def editor(operation: RepairOperation) -> dict[str, object]:
        editor_hashes.append(operation.before_hash)
        assert operation.before_hash == sha256_text(target.read_text(encoding="utf-8"))
        _replace(target, operation)
        return {"ok": True}

    def writer(relative_path: str, content: str) -> dict[str, object]:
        writer_calls.append(relative_path)
        (tmp_path / relative_path).write_text(content, encoding="utf-8")
        return {"ok": True}

    result = TransactionalRepairExecutor().execute(
        workspace=tmp_path,
        plan=plan,
        composition=composition,
        writer=writer,
        editor=editor,
    )

    assert result.ok
    assert len(editor_hashes) == 2
    assert editor_hashes[0] == sha256_text(before)
    assert editor_hashes[1] != editor_hashes[0]
    assert writer_calls == []
    assert target.read_text(encoding="utf-8") == "export const a = true; export const b = true;\n"
    assert result.receipt.metadata["execution_records"][0]["operation"] == "edit_file"


def test_editor_rejection_after_mutation_rolls_back(tmp_path: Path) -> None:
    path = "src/app.ts"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    before = "export const a = false; export const b = false;\n"
    target.write_text(before, encoding="utf-8")
    first = before.index("false")
    second = before.rindex("false")
    operations = (
        RepairOperation(
            kind="text_replace",
            path=path,
            span_start=first,
            span_end=first + 5,
            expected="false",
            replacement="true",
            before_hash=sha256_text(before),
            metadata={"expected_context_before": "a = "},
        ),
        RepairOperation(
            kind="text_replace",
            path=path,
            span_start=second,
            span_end=second + 5,
            expected="false",
            replacement="true",
            before_hash=sha256_text(before),
            metadata={"expected_context_before": "b = "},
        ),
    )
    plan = RepairPlan(rule_id="typescript.atomic_editor", source_tool="test", operations=operations)
    composition = PatchComposer().compose({path: before}, plan.operations)
    writer_calls: list[str] = []
    editor_calls = 0

    def editor(edit: RepairOperation) -> dict[str, object]:
        nonlocal editor_calls
        editor_calls += 1
        if editor_calls == 2:
            return {"ok": False}
        _replace(target, edit)
        return {"ok": True}

    def writer(relative_path: str, content: str) -> dict[str, object]:
        writer_calls.append(relative_path)
        (tmp_path / relative_path).write_text(content, encoding="utf-8")
        return {"ok": True}

    result = TransactionalRepairExecutor().execute(
        workspace=tmp_path,
        plan=plan,
        composition=composition,
        writer=writer,
        editor=editor,
    )

    assert not result.ok
    assert result.rolled_back
    assert result.receipt.status == "rolled_back"
    assert editor_calls == 2
    assert writer_calls == [path]
    assert target.read_text(encoding="utf-8") == before


def test_writer_rejection_after_mutation_rolls_back(tmp_path: Path) -> None:
    path = "src/app.ts"
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    before = "export const value = false;\n"
    after = "export const value = true;\n"
    target.write_text(before, encoding="utf-8")
    operation = RepairOperation(kind="write_file", path=path, content=after, before_hash=sha256_text(before))
    plan = RepairPlan(rule_id="typescript.atomic_writer", source_tool="test", operations=(operation,))
    composition = PatchComposer().compose({path: before}, plan.operations)
    calls = 0

    def writer(relative_path: str, content: str) -> dict[str, object]:
        nonlocal calls
        calls += 1
        (tmp_path / relative_path).write_text(content, encoding="utf-8")
        return {"ok": calls > 1}

    result = TransactionalRepairExecutor().execute(
        workspace=tmp_path,
        plan=plan,
        composition=composition,
        writer=writer,
    )

    assert not result.ok
    assert result.rolled_back
    assert result.receipt.status == "rolled_back"
    assert calls == 2
    assert target.read_text(encoding="utf-8") == before
