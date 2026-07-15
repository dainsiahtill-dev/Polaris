"""Typed, read-only Factory settlement barrier projection.

The projector in this module is intentionally free of I/O.  The public
service supplies canonical Run Ledger and TaskRuntime FactStream projections;
this module classifies settlement without waiting, mutating state, or inferring
facts from prose.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from polaris.cells.control_plane.run_ledger.public.ledger import stable_hash

FACTORY_SETTLEMENT_BARRIER_SCHEMA_V1 = "run_ledger.factory_settlement_barrier.v1"

_TERMINAL_TASK_STATES = frozenset(
    {
        "cancelled",
        "completed",
        "failed",
        "success",
        "succeeded",
        "timed_out",
        "timeout",
    }
)
_FAILED_TASK_STATES = frozenset({"cancelled", "failed", "timed_out", "timeout"})
_ACTIVE_LIFECYCLE_STATES = frozenset(
    {
        "active",
        "claimed",
        "executing",
        "in_progress",
        "running",
        "started",
        "starting",
    }
)


class FactorySettlementBarrierQueryError(ValueError):
    """Raised when a Factory settlement query has an invalid scope."""


@dataclass(frozen=True, slots=True)
class FactorySettlementBarrierQueryV1:
    """Exact workspace and Factory-run scope for a settlement query."""

    workspace: str
    factory_run_id: str

    def __post_init__(self) -> None:
        workspace = str(self.workspace or "").strip()
        factory_run_id = str(self.factory_run_id or "").strip()
        if not workspace:
            raise FactorySettlementBarrierQueryError("workspace must be a non-empty string")
        if not factory_run_id:
            raise FactorySettlementBarrierQueryError("factory_run_id must be a non-empty string")
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "factory_run_id", factory_run_id)


@dataclass(frozen=True, slots=True)
class FactorySettlementBarrierResultV1:
    """Immutable settlement state derived from canonical execution facts.

    ``closed`` means all declared settlement obligations reached a terminal
    state.  It deliberately does not mean success: failed evidence and failed
    task/tool lifecycles are closed failures.  ``release_allowed`` follows
    closure so Factory can release execution authority while QA preserves the
    independent failed outcome in ``passed``.
    """

    schema_version: str
    workspace: str
    factory_run_id: str
    closed: bool
    passed: bool
    release_allowed: bool
    barrier_hash: str
    missing_required_modalities: tuple[str, ...]
    failed_required_modalities: tuple[str, ...]
    task_lifecycle_count: int
    tool_lifecycle_count: int
    active_lifecycle_count: int
    open_lifecycle_count: int
    failed_lifecycle_count: int
    expected_effect_count: int
    effect_receipt_count: int
    open_effect_count: int
    evidence_refs: tuple[str, ...]
    blocking_reasons: tuple[str, ...]
    consumed_run_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _LifecycleCounts:
    task_count: int
    tool_count: int
    active_count: int
    open_count: int
    failed_count: int
    expected_effect_count: int
    effect_receipt_count: int
    open_effect_count: int
    missing_tool_lifecycle_count: int


def _clean_string(value: Any) -> str:
    return str(value or "").strip()


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(sorted({_clean_string(item) for item in value if _clean_string(item)}))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _task_identity(fact: Mapping[str, Any]) -> str:
    snapshot = _mapping(fact.get("task_row_snapshot"))
    metadata = _mapping(snapshot.get("metadata"))
    return _clean_string(
        fact.get("task_id")
        or snapshot.get("id")
        or metadata.get("external_task_id")
        or metadata.get("pm_task_id")
        or fact.get("fact_event_id")
    )


def _task_state(fact: Mapping[str, Any]) -> str:
    snapshot = _mapping(fact.get("task_row_snapshot"))
    return _clean_string(
        fact.get("execution_state")
        or fact.get("status")
        or snapshot.get("status")
        or fact.get("event_type")
    ).lower()


def _latest_task_facts(
    task_runtime_facts: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    latest: dict[str, tuple[int, int, Mapping[str, Any]]] = {}
    for index, fact in enumerate(task_runtime_facts):
        task_id = _task_identity(fact)
        if not task_id:
            continue
        sequence = _non_negative_int(fact.get("fact_event_seq"))
        previous = latest.get(task_id)
        if previous is None or (sequence, index) >= (previous[0], previous[1]):
            latest[task_id] = (sequence, index, fact)
    return {task_id: selected[2] for task_id, selected in latest.items()}


def _lifecycle_counts(
    run_projection: Mapping[str, Any],
    task_runtime_facts: Sequence[Mapping[str, Any]],
) -> _LifecycleCounts:
    latest_task_facts = _latest_task_facts(task_runtime_facts)
    task_states = tuple(_task_state(fact) for fact in latest_task_facts.values())
    open_task_count = sum(state not in _TERMINAL_TASK_STATES for state in task_states)
    active_task_count = sum(state in _ACTIVE_LIFECYCLE_STATES for state in task_states)
    failed_task_count = sum(state in _FAILED_TASK_STATES for state in task_states)

    lifecycle = _mapping(run_projection.get("tool_lifecycle"))
    latest_tool_lifecycles = _mapping(lifecycle.get("latest_by_task"))
    missing_tool_keys = set(_string_tuple(lifecycle.get("missing_required_task_keys")))
    active_tool_keys = {
        _clean_string(task_key)
        for task_key, raw_event in latest_tool_lifecycles.items()
        if _clean_string(task_key)
        and _clean_string(_mapping(raw_event).get("status")).lower() in _ACTIVE_LIFECYCLE_STATES
    }
    open_tool_keys = missing_tool_keys | active_tool_keys
    expected_effect_count = _non_negative_int(lifecycle.get("dispatched_tool_calls_count"))
    effect_receipt_count = _non_negative_int(lifecycle.get("effect_receipt_count"))
    return _LifecycleCounts(
        task_count=len(latest_task_facts),
        tool_count=_non_negative_int(lifecycle.get("event_count")),
        active_count=active_task_count + len(active_tool_keys),
        open_count=open_task_count + len(open_tool_keys),
        failed_count=failed_task_count + _non_negative_int(lifecycle.get("failed_count")),
        expected_effect_count=expected_effect_count,
        effect_receipt_count=effect_receipt_count,
        open_effect_count=max(0, expected_effect_count - effect_receipt_count),
        missing_tool_lifecycle_count=len(missing_tool_keys),
    )


def _fact_reference(fact: Mapping[str, Any]) -> str:
    stream = _clean_string(fact.get("fact_stream"))
    event_id = _clean_string(fact.get("fact_event_id"))
    sequence = _non_negative_int(fact.get("fact_event_seq"))
    if not event_id:
        return ""
    return ":".join(part for part in (stream, event_id, str(sequence) if sequence else "") if part)


def _evidence_references(
    run_projection: Mapping[str, Any],
    task_runtime_facts: Sequence[Mapping[str, Any]],
    ledger_facts: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    refs = {
        reference
        for fact in (*task_runtime_facts, *ledger_facts)
        if (reference := _fact_reference(fact))
    }
    gates = run_projection.get("gates")
    if isinstance(gates, Sequence) and not isinstance(gates, (str, bytes, bytearray)):
        for raw_gate in gates:
            gate = _mapping(raw_gate)
            refs.update(
                value
                for key in ("append_id", "content_id", "job_token_id")
                if (value := _clean_string(gate.get(key)))
            )
    lifecycle = _mapping(run_projection.get("tool_lifecycle"))
    events = lifecycle.get("events")
    if isinstance(events, Sequence) and not isinstance(events, (str, bytes, bytearray)):
        for raw_event in events:
            event = _mapping(raw_event)
            refs.update(
                value
                for key in ("append_id", "content_id", "batch_receipt_hash", "provider_response_hash")
                if (value := _clean_string(event.get(key)))
            )
            for field_name in ("batch_receipt_refs", "effect_receipt_refs"):
                raw_refs = event.get(field_name)
                if not isinstance(raw_refs, Sequence) or isinstance(raw_refs, (str, bytes, bytearray)):
                    continue
                for raw_ref in raw_refs:
                    if isinstance(raw_ref, Mapping):
                        refs.add(f"{field_name}:{stable_hash(dict(raw_ref))}")
                    elif (reference := _clean_string(raw_ref)):
                        refs.add(reference)
    return tuple(sorted(refs))


def _blocking_reasons(
    *,
    scope_found: bool,
    gate_count: int,
    failed_gate_count: int,
    missing_required_modalities: tuple[str, ...],
    failed_required_modalities: tuple[str, ...],
    counts: _LifecycleCounts,
    run_projection: Mapping[str, Any],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not scope_found:
        reasons.append("factory_run_not_found")
    if counts.open_count:
        reasons.append("lifecycle_open")
    if counts.missing_tool_lifecycle_count:
        reasons.append("tool_lifecycle_evidence_missing")
    if counts.expected_effect_count > 0 and counts.effect_receipt_count == 0:
        reasons.append("effect_receipt_missing")
    if counts.open_effect_count:
        reasons.append("effect_receipts_open")
    if missing_required_modalities:
        reasons.append("required_evidence_missing")
    if failed_required_modalities:
        reasons.append("required_evidence_failed")
    if failed_gate_count:
        reasons.append("run_ledger_gate_failed")
    if counts.failed_count:
        reasons.append("lifecycle_failed")
    capability = _mapping(run_projection.get("capability"))
    if gate_count and not bool(capability.get("ok")):
        reasons.append("capability_invalid")
    task_boundary = _mapping(run_projection.get("task_boundary"))
    failed_boundaries = task_boundary.get("failed")
    if isinstance(failed_boundaries, Sequence) and failed_boundaries:
        reasons.append("task_boundary_failed")
    return tuple(dict.fromkeys(reasons))


def project_factory_settlement_barrier(
    *,
    workspace: str,
    factory_run_id: str,
    run_projection: Mapping[str, Any],
    task_runtime_facts: Sequence[Mapping[str, Any]],
    ledger_facts: Sequence[Mapping[str, Any]],
    consumed_run_ids: Sequence[str],
) -> FactorySettlementBarrierResultV1:
    """Project one deterministic settlement result from canonical facts.

    Complexity:
        O(T + L + E) time and O(T + E) memory for TaskRuntime facts, ledger
        facts, and evidence references.  No polling, sleep, or state mutation is
        performed.
    """

    query = FactorySettlementBarrierQueryV1(workspace=workspace, factory_run_id=factory_run_id)
    policy = _mapping(run_projection.get("evidence_policy"))
    failed_required_modalities = _string_tuple(policy.get("failed_required_modalities"))
    failed_set = set(failed_required_modalities)
    missing_required_modalities = tuple(
        modality
        for modality in _string_tuple(policy.get("missing_required_modalities"))
        if modality not in failed_set
    )
    counts = _lifecycle_counts(run_projection, task_runtime_facts)
    gate_count = _non_negative_int(run_projection.get("gate_count"))
    failed_gates = run_projection.get("failed_gates")
    failed_gate_count = (
        len(failed_gates)
        if isinstance(failed_gates, Sequence) and not isinstance(failed_gates, (str, bytes, bytearray))
        else 0
    )
    normalized_run_ids = _string_tuple(consumed_run_ids)
    evidence_refs = _evidence_references(run_projection, task_runtime_facts, ledger_facts)
    scope_found = bool(task_runtime_facts and normalized_run_ids)
    blocking_reasons = _blocking_reasons(
        scope_found=scope_found,
        gate_count=gate_count,
        failed_gate_count=failed_gate_count,
        missing_required_modalities=missing_required_modalities,
        failed_required_modalities=failed_required_modalities,
        counts=counts,
        run_projection=run_projection,
    )
    closed = bool(
        scope_found
        and counts.open_count == 0
        and counts.open_effect_count == 0
        and not missing_required_modalities
    )
    passed = bool(closed and bool(run_projection.get("ok")) and counts.failed_count == 0)
    hash_payload = {
        "schema_version": FACTORY_SETTLEMENT_BARRIER_SCHEMA_V1,
        "workspace": query.workspace,
        "factory_run_id": query.factory_run_id,
        "closed": closed,
        "passed": passed,
        "missing_required_modalities": missing_required_modalities,
        "failed_required_modalities": failed_required_modalities,
        "task_lifecycle_count": counts.task_count,
        "tool_lifecycle_count": counts.tool_count,
        "active_lifecycle_count": counts.active_count,
        "open_lifecycle_count": counts.open_count,
        "failed_lifecycle_count": counts.failed_count,
        "expected_effect_count": counts.expected_effect_count,
        "effect_receipt_count": counts.effect_receipt_count,
        "open_effect_count": counts.open_effect_count,
        "evidence_refs": evidence_refs,
        "blocking_reasons": blocking_reasons,
        "consumed_run_ids": normalized_run_ids,
    }
    return FactorySettlementBarrierResultV1(
        schema_version=FACTORY_SETTLEMENT_BARRIER_SCHEMA_V1,
        workspace=query.workspace,
        factory_run_id=query.factory_run_id,
        closed=closed,
        passed=passed,
        release_allowed=closed,
        barrier_hash=stable_hash(hash_payload),
        missing_required_modalities=missing_required_modalities,
        failed_required_modalities=failed_required_modalities,
        task_lifecycle_count=counts.task_count,
        tool_lifecycle_count=counts.tool_count,
        active_lifecycle_count=counts.active_count,
        open_lifecycle_count=counts.open_count,
        failed_lifecycle_count=counts.failed_count,
        expected_effect_count=counts.expected_effect_count,
        effect_receipt_count=counts.effect_receipt_count,
        open_effect_count=counts.open_effect_count,
        evidence_refs=evidence_refs,
        blocking_reasons=blocking_reasons,
        consumed_run_ids=normalized_run_ids,
    )


__all__ = [
    "FactorySettlementBarrierQueryError",
    "FactorySettlementBarrierQueryV1",
    "FactorySettlementBarrierResultV1",
    "project_factory_settlement_barrier",
]
