"""Transactional execution shell for composed repair patches."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .contracts import CompositionResult, RepairExecutionResult, RepairPlan
from .receipts import build_receipt

WriteFileFn = Callable[[str, str], Mapping[str, Any]]


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

        if writer is None:
            receipt = build_receipt(
                plan=plan,
                status="blocked",
                mode=plan.mode,
                patches=composition.patches,
                metadata={"reason": "commit_requires_policy_gated_writer"},
            )
            return RepairExecutionResult(
                ok=False,
                receipt=receipt,
                error="commit_requires_policy_gated_writer",
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
                result = dict(writer(patch.path, patch.content_after))
                if not bool(result.get("ok")):
                    raise RuntimeError(f"repair writer rejected {patch.path}")
                written.append((patch.path, patch.content_before))
        except (OSError, RuntimeError, ValueError) as exc:
            rollback_failures: list[str] = []
            rollback_success_count = 0
            for path, content_before in reversed(written):
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
