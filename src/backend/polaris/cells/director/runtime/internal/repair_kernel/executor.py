"""Transactional execution shell for composed repair patches."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .contracts import CompositionResult, RepairExecutionResult, RepairOperation, RepairPlan
from .receipts import build_receipt

WriteFileFn = Callable[[str, str], Mapping[str, Any]]
EditFileFn = Callable[[RepairOperation], Mapping[str, Any]]


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
                metadata={"writes_performed": False},
            )
            return RepairExecutionResult(ok=True, receipt=receipt)

        if writer is None and editor is None:
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
        written: list[tuple[str, str]] = []
        try:
            for patch in composition.patches:
                target = (workspace_root / patch.path).resolve()
                target.relative_to(workspace_root)
                current = target.read_text(encoding="utf-8") if target.is_file() else ""
                if current != patch.content_before:
                    raise RuntimeError(f"repair precondition failed for {patch.path}")
                text_operations = _text_replace_operations_for_patch(plan.operations, patch.path)
                if editor is not None and _can_apply_with_editor(current, text_operations):
                    for operation in text_operations:
                        result = dict(editor(operation))
                        if not bool(result.get("ok")):
                            raise RuntimeError(f"repair editor rejected {patch.path}")
                    written.append((patch.path, patch.content_before))
                    continue
                if writer is None:
                    raise RuntimeError(f"repair patch requires whole-file writer for {patch.path}")
                result = dict(writer(patch.path, patch.content_after))
                if not bool(result.get("ok")):
                    raise RuntimeError(f"repair writer rejected {patch.path}")
                written.append((patch.path, patch.content_before))
        except (OSError, RuntimeError, ValueError) as exc:
            rollback_failures: list[str] = []
            rollback_success_count = 0
            for path, content_before in reversed(written):
                if writer is None:
                    rollback_failures.append(f"{path}:rollback_requires_writer")
                    continue
                try:
                    rollback_result = dict(writer(path, content_before))
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
                    "rollback_failed_paths": rollback_failures,
                    "rollback_success_count": rollback_success_count,
                },
            )
            return RepairExecutionResult(ok=False, receipt=receipt, rolled_back=rolled_back, error=str(exc))

        receipt = build_receipt(plan=plan, status="applied", mode=plan.mode, patches=composition.patches)
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
        if expected is None or current[start:end] != expected:
            return False
        if not expected or current.count(expected) != 1:
            return False
        current = current[:start] + str(operation.replacement or "") + current[end:]
    return True
