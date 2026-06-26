"""Transactional execution shell for composed repair patches."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .contracts import (
    FILE_ABSENT_HASH,
    CompositionResult,
    RepairExecutionResult,
    RepairOperation,
    RepairPlan,
    sha256_text,
)
from .receipts import build_receipt

WriteFileFn = Callable[[str, str], Mapping[str, Any]]
EditFileFn = Callable[[RepairOperation], Mapping[str, Any]]
DeleteFileFn = Callable[[str], Mapping[str, Any]]

_ROLLBACK_RESTORE_STRATEGY = "write_file_full_restore"
_ROLLBACK_DELETE_CREATED_STRATEGY = "delete_created_file"


class TransactionalRepairExecutor:
    """Execute composed patches.

    Shadow mode never writes. Commit mode requires an explicit writer callback
    so production integration can route writes through the existing Director
    policy-gated write tool instead of direct filesystem writes.
    """

    def execute(
        self,
        *,
        workspace: Path,
        plan: RepairPlan,
        composition: CompositionResult,
        writer: WriteFileFn | None = None,
        editor: EditFileFn | None = None,
        deleter: DeleteFileFn | None = None,
    ) -> RepairExecutionResult:
        if not composition.ok:
            receipt = build_receipt(
                plan=plan,
                status="composition_failed",
                mode=plan.mode,
                patches=(),
                metadata={"issues": [issue.to_dict() for issue in composition.issues]},
            )
            return RepairExecutionResult(ok=False, receipt=receipt, error="composition_failed")

        if plan.mode == "shadow":
            receipt = build_receipt(
                plan=plan,
                status="shadow_observed",
                mode="shadow",
                patches=composition.patches,
                metadata={
                    "writes_performed": False,
                    "patches": [patch.to_dict() for patch in composition.patches],
                },
            )
            return RepairExecutionResult(ok=True, receipt=receipt)

        if writer is None and editor is None and deleter is None:
            receipt = build_receipt(
                plan=plan,
                status="blocked",
                mode=plan.mode,
                patches=composition.patches,
                metadata={"reason": "commit_requires_policy_gated_writer_or_editor"},
            )
            return RepairExecutionResult(
                ok=False,
                receipt=receipt,
                error="commit_requires_policy_gated_writer_or_editor",
            )

        workspace_root = workspace.resolve()
        written: list[dict[str, Any]] = []
        execution_records: list[dict[str, Any]] = []
        try:
            for patch in composition.patches:
                target = (workspace_root / patch.path).resolve()
                target.relative_to(workspace_root)
                if target.exists() and not target.is_file():
                    raise RuntimeError(f"repair target is not a regular file for {patch.path}")
                current_exists = target.is_file()
                current = target.read_text(encoding="utf-8") if current_exists else ""
                current_hash = sha256_text(current) if current_exists else FILE_ABSENT_HASH
                if current_exists != patch.exists_before:
                    raise RuntimeError(f"repair file existence precondition failed for {patch.path}")
                if current != patch.content_before or (patch.exists_before and current_hash != patch.before_hash):
                    raise RuntimeError(f"repair precondition failed for {patch.path}")
                if not patch.exists_after:
                    if writer is None:
                        raise RuntimeError(f"repair delete_file requires policy-gated writer rollback for {patch.path}")
                    if deleter is None:
                        raise RuntimeError(f"repair delete_file requires policy-gated deleter for {patch.path}")
                    result = dict(deleter(patch.path))
                    if not bool(result.get("ok")):
                        raise RuntimeError(f"repair deleter rejected {patch.path}")
                    if target.exists():
                        raise RuntimeError(f"repair deleter did not remove {patch.path}")
                    written.append(_rollback_entry_for_patch(patch))
                    execution_records.append(
                        _execution_record(
                            patch=patch,
                            operation="delete_file",
                            operation_ids=list(patch.operation_ids),
                            rollback_requires_delete_tool=False,
                        )
                    )
                    continue
                text_operations = _text_replace_operations_for_patch(plan.operations, patch.path)
                if editor is not None and _can_apply_with_editor(current, text_operations):
                    for operation in text_operations:
                        result = dict(editor(operation))
                        if not bool(result.get("ok")):
                            raise RuntimeError(f"repair editor rejected {patch.path}")
                    written.append(_rollback_entry_for_patch(patch))
                    execution_records.append(
                        _execution_record(
                            patch=patch,
                            operation="edit_file",
                            operation_ids=[operation.operation_id for operation in text_operations],
                            large_file_safe=True,
                            span_based=True,
                            unique_context_checked=any(
                                _operation_has_context_metadata(operation) for operation in text_operations
                            ),
                        )
                    )
                    continue
                if writer is None:
                    raise RuntimeError(f"repair patch requires whole-file writer for {patch.path}")
                result = dict(writer(patch.path, patch.content_after))
                if not bool(result.get("ok")):
                    raise RuntimeError(f"repair writer rejected {patch.path}")
                written.append(_rollback_entry_for_patch(patch))
                execution_records.append(
                    _execution_record(
                        patch=patch,
                        operation="write_file",
                        operation_ids=list(patch.operation_ids),
                        rollback_requires_delete_tool=not patch.exists_before and deleter is None,
                    )
                )
        except (OSError, RuntimeError, ValueError) as exc:
            rollback_failures: list[str] = []
            rollback_success_count = 0
            for entry in reversed(written):
                path = str(entry["path"])
                rollback_strategy = str(entry["rollback_strategy"])
                if rollback_strategy == _ROLLBACK_DELETE_CREATED_STRATEGY:
                    if deleter is None:
                        rollback_failures.append(f"{path}:rollback_requires_delete_tool")
                        continue
                    try:
                        rollback_result = dict(deleter(path))
                    except (OSError, RuntimeError, ValueError) as rollback_exc:
                        rollback_failures.append(f"{path}:{rollback_exc}")
                        continue
                else:
                    if writer is None:
                        rollback_failures.append(f"{path}:rollback_requires_writer")
                        continue
                    try:
                        rollback_result = dict(writer(path, str(entry["content_before"])))
                    except (OSError, RuntimeError, ValueError) as rollback_exc:
                        rollback_failures.append(f"{path}:{rollback_exc}")
                        continue
                if bool(rollback_result.get("ok")):
                    rollback_success_count += 1
                else:
                    rollback_failures.append(path)
            rollback_attempted = bool(written)
            rolled_back = rollback_attempted and not rollback_failures
            status = "rolled_back" if rolled_back else "rollback_failed" if rollback_attempted else "failed"
            receipt = build_receipt(
                plan=plan,
                status=status,
                mode=plan.mode,
                patches=composition.patches,
                metadata={
                    "error": str(exc),
                    "rollback_attempted": rollback_attempted,
                    "rollback_restore_strategy": _ROLLBACK_RESTORE_STRATEGY if rollback_attempted else "",
                    "rollback_delete_created_strategy": _ROLLBACK_DELETE_CREATED_STRATEGY if rollback_attempted else "",
                    "rollback_failed_paths": rollback_failures,
                    "rollback_success_count": rollback_success_count,
                    "execution_records": execution_records,
                },
            )
            return RepairExecutionResult(ok=False, receipt=receipt, rolled_back=rolled_back, error=str(exc))

        receipt = build_receipt(
            plan=plan,
            status="applied",
            mode=plan.mode,
            patches=composition.patches,
            metadata={
                "writes_performed": True,
                "rollback_restore_strategy": _ROLLBACK_RESTORE_STRATEGY,
                "rollback_delete_created_strategy": _ROLLBACK_DELETE_CREATED_STRATEGY,
                "execution_records": execution_records,
                "write_file_reasons_by_path": {
                    record["path"]: record["write_file_reason"]
                    for record in execution_records
                    if record["operation"] == "write_file"
                },
            },
        )
        return RepairExecutionResult(ok=True, receipt=receipt)


def _text_replace_operations_for_patch(
    operations: Sequence[RepairOperation],
    path: str,
) -> tuple[RepairOperation, ...]:
    normalized_path = str(path or "").strip().replace("\\", "/")
    path_operations = tuple(
        operation
        for operation in operations
        if operation.kind == "text_replace" and str(operation.path or "").strip().replace("\\", "/") == normalized_path
    )
    all_path_operations = tuple(
        operation
        for operation in operations
        if str(operation.path or "").strip().replace("\\", "/") == normalized_path
    )
    if len(path_operations) != len(all_path_operations):
        return ()
    return tuple(sorted(path_operations, key=lambda operation: operation.span_start or 0, reverse=True))


def _can_apply_with_editor(content: str, operations: Sequence[RepairOperation]) -> bool:
    if not operations:
        return False
    current = str(content or "")
    for operation in operations:
        if operation.span_start is None or operation.span_end is None:
            return False
        start = int(operation.span_start)
        end = int(operation.span_end)
        expected = operation.expected
        context_checked = _operation_context_matches(current, operation, start, end)
        if expected is None:
            if not context_checked:
                return False
        elif current[start:end] != expected:
            return False
        if expected is not None and expected and current.count(expected) != 1 and not context_checked:
            return False
        if expected == "" and not context_checked:
            return False
        current = current[:start] + str(operation.replacement or "") + current[end:]
    return True


def _patch_write_file_reason(patch: Any) -> str:
    reason = str(patch.metadata.get("write_file_reason") or "").strip()
    if reason:
        return reason
    if patch.content_before == "":
        return "new_file_or_empty_file"
    return "fallback_whole_file_repair"


def _rollback_entry_for_patch(patch: Any) -> dict[str, Any]:
    return {
        "path": patch.path,
        "content_before": patch.content_before,
        "rollback_strategy": _rollback_strategy_for_patch(patch),
    }


def _rollback_strategy_for_patch(patch: Any) -> str:
    if not bool(patch.exists_before) and bool(patch.exists_after):
        return _ROLLBACK_DELETE_CREATED_STRATEGY
    return _ROLLBACK_RESTORE_STRATEGY


def _execution_record(
    *,
    patch: Any,
    operation: str,
    operation_ids: list[str],
    large_file_safe: bool | None = None,
    span_based: bool | None = None,
    unique_context_checked: bool | None = None,
    rollback_requires_delete_tool: bool = False,
) -> dict[str, Any]:
    created_file = not bool(patch.exists_before) and bool(patch.exists_after)
    deleted_file = bool(patch.exists_before) and not bool(patch.exists_after)
    if created_file:
        created_or_deleted = "created"
    elif deleted_file:
        created_or_deleted = "deleted"
    else:
        created_or_deleted = ""
    rollback_strategy = _rollback_strategy_for_patch(patch)
    return {
        "path": patch.path,
        "operation": operation,
        "operation_ids": operation_ids,
        "before_hash": patch.before_hash,
        "after_hash": patch.after_hash,
        "exists_before": bool(patch.exists_before),
        "exists_after": bool(patch.exists_after),
        "created_file": created_file,
        "deleted_file": deleted_file,
        "created_or_deleted": created_or_deleted,
        "large_file_safe": bool(patch.metadata.get("large_file_safe")) if large_file_safe is None else large_file_safe,
        "span_based": bool(patch.metadata.get("span_based")) if span_based is None else span_based,
        "unique_context_checked": bool(patch.metadata.get("unique_context_checked"))
        if unique_context_checked is None
        else unique_context_checked,
        "write_file_reason": _patch_write_file_reason(patch) if operation == "write_file" else "",
        "rollback_restore_strategy": rollback_strategy,
        "rollback_strategy": rollback_strategy,
        "rollback_requires_delete_tool": rollback_requires_delete_tool,
    }


def _operation_has_context_metadata(operation: RepairOperation) -> bool:
    return any(
        str(operation.metadata.get(key) or "")
        for key in (
            "expected_context_before",
            "context_before",
            "expected_context_after",
            "context_after",
            "unique_context",
        )
    )


def _operation_context_matches(content: str, operation: RepairOperation, start: int, end: int) -> bool:
    before = _metadata_text(operation.metadata, "expected_context_before", "context_before")
    after = _metadata_text(operation.metadata, "expected_context_after", "context_after")
    unique_context = _metadata_text(operation.metadata, "unique_context")
    if not before and not after and not unique_context:
        return False
    if before:
        before_start = start - len(before)
        if before_start < 0 or content[before_start:start] != before:
            return False
    if after and content[end : end + len(after)] != after:
        return False
    probe = unique_context or f"{before}{content[start:end]}{after}"
    if not probe or content.count(probe) != 1:
        return False
    probe_start = content.find(probe)
    probe_end = probe_start + len(probe)
    if unique_context:
        return probe_start <= start and end <= probe_end
    return probe_start == start - len(before) and probe_end == end + len(after)


def _metadata_text(metadata: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = metadata.get(key)
        if value is not None:
            return str(value)
    return ""
