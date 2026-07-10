"""Pure, stateless claim-readiness predicates for ``runtime.task_market``.

These functions encode the three-tier fission readiness gates used by the
claim-candidate selector. They are intentionally free of service state: each
takes the candidate record and the full in-memory item map and returns a
boolean. ``LifecycleMixin`` binds them as ``staticmethod`` attributes so the
existing ``self._exec_claim_ready`` / ``self._design_claim_ready`` call sites
keep working unchanged.
"""

from __future__ import annotations

from typing import Final

from polaris.cells.runtime.task_market.public.contracts import (
    OWNER_REWORK_HANDOFFS_METADATA_KEY,
    OwnerReworkHandoffV1,
)

from .models import TaskWorkItemRecord, now_epoch

_INVALID_RESOLVED_ONLY_HANDOFFS: Final[None] = None


def _resolved_only_dependency_ids(item: TaskWorkItemRecord) -> frozenset[str] | None:
    """Return typed owner dependencies or ``None`` for corrupt handoff state.

    Owner-rework handoffs are authoritative readiness facts.  A malformed
    dedicated handoff field or a handoff that names an unrelated task is an
    integrity failure, so callers block rather than quietly falling back to
    the ordinary QA fast-path.  Owner-side audit copies are valid but do not
    constrain the owner's own readiness.
    """

    raw_metadata = getattr(item, "metadata", {})
    if raw_metadata is None:
        raw_metadata = {}
    if not isinstance(raw_metadata, dict):
        return _INVALID_RESOLVED_ONLY_HANDOFFS
    raw_handoffs = raw_metadata.get(OWNER_REWORK_HANDOFFS_METADATA_KEY)
    if raw_handoffs is None:
        return frozenset()
    if not isinstance(raw_handoffs, dict):
        return _INVALID_RESOLVED_ONLY_HANDOFFS

    dependency_ids: set[str] = set()
    for raw_handoff_id, raw_record in raw_handoffs.items():
        try:
            handoff = OwnerReworkHandoffV1.from_record(raw_record)
        except ValueError:
            return _INVALID_RESOLVED_ONLY_HANDOFFS
        if str(raw_handoff_id or "").strip() != handoff.handoff_id:
            return _INVALID_RESOLVED_ONLY_HANDOFFS
        if handoff.requester_task_id == item.task_id:
            dependency_ids.add(handoff.owner_task_id)
            continue
        if handoff.owner_task_id == item.task_id:
            continue
        return _INVALID_RESOLVED_ONLY_HANDOFFS
    return frozenset(dependency_ids)


def exec_claim_ready(item: TaskWorkItemRecord, items: dict[str, TaskWorkItemRecord]) -> bool:
    """Execution-stage readiness gate (three-tier fission).

    At ``pending_exec``: (a) non-leaf supervision rows (a CE-fissioned
    parent) are never handed to Director workers; (b) a step is claimable
    only when every ``depends_on`` step has left the exec queue — resolved
    or advanced to QA. A failed-and-requeued dependency therefore blocks
    its dependents (fail-closed); orphan references block too (the
    depends_on validator reports them). Terminally-failed dependencies
    are not merely blocked: ``_cascade_dead_letter_dependents`` sweeps
    their dependents into the DLQ at claim time.
    """
    if item.stage != "pending_exec":
        return True
    if not item.is_leaf:
        return False

    resolved_only_dependency_ids = _resolved_only_dependency_ids(item)
    if resolved_only_dependency_ids is _INVALID_RESOLVED_ONLY_HANDOFFS:
        return False
    declared_dependency_ids = {str(dep_id).strip() for dep_id in item.depends_on if str(dep_id).strip()}
    if not resolved_only_dependency_ids.issubset(declared_dependency_ids):
        return False

    def _target(record: TaskWorkItemRecord) -> str:
        payload = getattr(record, "payload", None)
        if not isinstance(payload, dict):
            return ""
        step = payload.get("construction_step")
        if isinstance(step, dict):
            value = str(step.get("target_file") or "").strip()
            if value:
                return value.replace("\\", "/").lstrip("./")
        targets = payload.get("target_files")
        if isinstance(targets, list) and targets:
            return str(targets[0] or "").strip().replace("\\", "/").lstrip("./")
        return ""

    item_target = _target(item)
    if item_target:
        current_epoch = now_epoch()
        for other in items.values():
            if getattr(other, "task_id", "") == item.task_id:
                continue
            if _target(other) != item_target:
                continue
            if (
                str(getattr(other, "stage", "") or "") == "pending_exec"
                and str(getattr(other, "status", "") or "") == "in_execution"
                and str(getattr(other, "lease_token", "") or "").strip()
                and float(getattr(other, "lease_expires_at", 0.0) or 0.0) > current_epoch
            ):
                return False
    for dep_id in item.depends_on or []:
        dep = items.get(str(dep_id))
        if dep is None:
            return False
        if dep.status == "resolved":
            continue
        if str(dep_id) in resolved_only_dependency_ids:
            return False
        # Same-file predecessor must be fully RESOLVED, not merely at QA: the
        # pending_qa fast-path would otherwise let a QA bounce of the predecessor
        # put two writers on the same file concurrently (I3-r29 fill chains /
        # cross-parent edit_on_prior). Independent-file deps keep the fast-path.
        if item_target and _target(dep) == item_target:
            return False
        if dep.stage in ("pending_qa", "in_qa"):
            continue
        return False
    return True


def design_claim_ready(item: TaskWorkItemRecord, items: dict[str, TaskWorkItemRecord]) -> bool:
    """Design-stage ordering gate (组合律 / cross-parent interface coherence).

    A ``pending_design`` parent that ``depends_on`` another parent must not
    fission until that producer parent has left design (advanced to
    ``pending_exec`` or terminal). Otherwise an enhancement parent can
    fission *before* the base parent and invent colliding interface
    identifiers for a shared file — live I3-r14 shipped a non-running
    product because ``index.html`` was named ``id="game"`` by one parent and
    ``id="gameCanvas"`` by a sibling. Ordering producers first lets the
    interface ledger be populated before the consumer reads it.

    Orphan deps (not in the market) and terminal deps do not block — only a
    producer still actively in design holds the consumer back (no hang).
    """
    if item.stage != "pending_design":
        return True
    for dep_id in item.depends_on or []:
        dep = items.get(str(dep_id))
        if dep is None:
            continue
        if dep.stage in ("pending_design", "in_design"):
            return False
    return True
