"""Repair receipt builders."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Mapping

from .contracts import (
    ComposedPatch,
    RepairAdvisorNote,
    RepairDiagnostic,
    RepairPlan,
    RepairReceipt,
    stable_id,
)


def build_receipt(
    *,
    plan: RepairPlan,
    status: str,
    mode: str,
    patches: Sequence[ComposedPatch] = (),
    diagnostics: Sequence[RepairDiagnostic] = (),
    metadata: Mapping[str, Any] | None = None,
) -> RepairReceipt:
    """Build a repair receipt from plan and composed patches."""

    files_changed = tuple(patch.path for patch in patches if patch.before_hash != patch.after_hash)
    operation_ids = tuple(operation_id for patch in patches for operation_id in patch.operation_ids)
    before_hashes = {patch.path: patch.before_hash for patch in patches}
    after_hashes = {patch.path: patch.after_hash for patch in patches}
    return RepairReceipt(
        receipt_id=stable_id("repair_receipt", plan.plan_id, status, mode, files_changed, operation_ids),
        plan_id=plan.plan_id,
        rule_id=plan.rule_id,
        source_tool=plan.source_tool,
        status=status,
        mode=mode,
        authoritative=mode == "commit" and status == "applied",
        files_changed=files_changed,
        operation_ids=operation_ids,
        diagnostics=tuple(diagnostics or plan.diagnostics),
        before_hashes=before_hashes,
        after_hashes=after_hashes,
        advisor_notes=tuple(note for note in plan.advisor_notes if isinstance(note, RepairAdvisorNote)),
        metadata=dict(metadata or {}),
    )
