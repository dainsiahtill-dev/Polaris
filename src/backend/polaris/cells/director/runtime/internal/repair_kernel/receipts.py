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
    RepairRevalidationEvidence,
    stable_id,
)
from .environment import environment_refresh_metadata_for_files


def build_receipt(
    *,
    plan: RepairPlan,
    status: str,
    mode: str,
    patches: Sequence[ComposedPatch] = (),
    diagnostics: Sequence[RepairDiagnostic] = (),
    round_number: int | None = None,
    revalidation_evidence: RepairRevalidationEvidence | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> RepairReceipt:
    """Build a repair receipt from plan and composed patches."""

    files_changed = tuple(patch.path for patch in patches if patch.before_hash != patch.after_hash)
    operation_ids = tuple(operation_id for patch in patches for operation_id in patch.operation_ids)
    before_hashes = {patch.path: patch.before_hash for patch in patches}
    after_hashes = {patch.path: patch.after_hash for patch in patches}
    revalidation_failed = _revalidation_failed(revalidation_evidence)
    authoritative = (
        mode == "commit" and status == "applied" and revalidation_evidence is not None and not revalidation_failed
    )
    receipt_metadata = dict(plan.metadata)
    receipt_metadata.update(dict(metadata or {}))
    receipt_metadata.update(
        {
            key: value
            for key, value in environment_refresh_metadata_for_files(
                files_changed=files_changed,
                after_hashes=after_hashes,
            ).items()
            if key not in receipt_metadata
        }
    )
    receipt_metadata.setdefault(
        "requires_revalidation",
        mode == "commit" and status == "applied" and revalidation_evidence is None,
    )
    return RepairReceipt(
        receipt_id=stable_id("repair_receipt", plan.plan_id, status, mode, files_changed, operation_ids),
        plan_id=plan.plan_id,
        rule_id=plan.rule_id,
        source_tool=plan.source_tool,
        status=status,
        mode=mode,
        authoritative=authoritative,
        files_changed=files_changed,
        operation_ids=operation_ids,
        diagnostics=tuple(diagnostics or plan.diagnostics),
        before_hashes=before_hashes,
        after_hashes=after_hashes,
        round_number=round_number,
        revalidation_evidence=revalidation_evidence,
        advisor_notes=tuple(note for note in plan.advisor_notes if isinstance(note, RepairAdvisorNote)),
        metadata=receipt_metadata,
    )


def attach_revalidation_evidence(
    receipt: RepairReceipt,
    evidence: RepairRevalidationEvidence,
) -> RepairReceipt:
    """Return a receipt carrying post-check evidence without changing advisory data."""

    revalidation_failed = _revalidation_failed(evidence)
    status = receipt.status
    if status == "pending_revalidation":
        status = "failed_revalidation" if revalidation_failed else "applied"
    elif status == "applied" and revalidation_failed:
        status = "failed_revalidation"
    authoritative = receipt.mode == "commit" and status == "applied" and not revalidation_failed
    metadata = dict(receipt.metadata)
    metadata["requires_revalidation"] = False
    return RepairReceipt(
        receipt_id=receipt.receipt_id,
        plan_id=receipt.plan_id,
        rule_id=receipt.rule_id,
        source_tool=receipt.source_tool,
        status=status,
        mode=receipt.mode,
        authoritative=authoritative,
        files_changed=receipt.files_changed,
        operation_ids=receipt.operation_ids,
        diagnostics=receipt.diagnostics,
        before_hashes=receipt.before_hashes,
        after_hashes=receipt.after_hashes,
        round_number=evidence.round_number,
        revalidation_evidence=evidence,
        advisor_notes=receipt.advisor_notes,
        metadata=metadata,
    )


def _revalidation_failed(evidence: RepairRevalidationEvidence | None) -> bool:
    if evidence is None:
        return False
    if evidence.evidence_status != "resolved_evidence":
        return True
    if evidence.exit_code not in (None, 0):
        return True
    if evidence.errors_after > 0:
        return True
    return bool(evidence.residual_diagnostic_ids)
