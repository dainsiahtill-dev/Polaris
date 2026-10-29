"""Typed, read-only Factory settlement barrier projection.

The projector in this module is intentionally free of I/O.  The public
service supplies canonical Run Ledger and TaskRuntime FactStream projections;
this module classifies settlement without waiting, mutating state, or inferring
facts from prose.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from polaris.cells.control_plane.run_ledger.public.committed_turn_receipts import CommittedTurnReceiptObservationV1
from polaris.cells.control_plane.run_ledger.public.directed_effect_receipt_validation import (
    directed_effect_receipt_v2_errors,
)
from polaris.cells.control_plane.run_ledger.public.ledger import stable_hash
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle import project_tool_lifecycle_event
from polaris.cells.runtime.task_runtime.public.contracts import TaskRuntimeExecutionAttemptIdentityV1
from polaris.cells.runtime.task_runtime.public.execution_effect_closure import ExecutionEffectClosureObservationV1
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

FACTORY_SETTLEMENT_BARRIER_SCHEMA_V1 = "run_ledger.factory_settlement_barrier.v1"

_TERMINAL_TASK_STATES = frozenset(
    {
        "cancelled",
        "completed",
        "failed",
        "removed",
        "success",
        "succeeded",
        "timed_out",
        "timeout",
    }
)
# "removed" is TaskRuntime reset tombstone (factory terminal drain cleanup).
# It is terminal for barrier closure but not a failed lifecycle verdict.
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
        fact.get("execution_state") or fact.get("status") or snapshot.get("status") or fact.get("event_type")
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


def _task_key_matches_terminal_ids(task_key: str, terminal_task_ids: set[str]) -> bool:
    """Return True when a tool-lifecycle task key maps to a terminal TaskRuntime id.

    Tool lifecycle keys may be numeric board ids, external/pm task tokens, or
    prefixed forms. Once the task itself is terminal (failed/cancelled/completed),
    a missing tool-lifecycle receipt must not keep the Factory settlement barrier
    open forever — that pin stuck workspace leases after Director stage failure
    (R55: tool_lifecycle_evidence_missing + lifecycle_open).

    Matching is exact after normalization (including TASK- prefix strip) or an
    explicit composite suffix (``...:id`` / ``.../id``). Bare ``endswith`` on
    digit keys is forbidden so key ``1`` cannot match terminal id ``11``/``21``.
    """

    key = _clean_string(task_key)
    if not key:
        return False
    if key in terminal_task_ids:
        return True
    key_norm = key.upper()
    key_suffix = key_norm[5:] if key_norm.startswith("TASK-") else key_norm
    for task_id in terminal_task_ids:
        if not task_id:
            continue
        tid = _clean_string(task_id)
        tid_norm = tid.upper()
        tid_suffix = tid_norm[5:] if tid_norm.startswith("TASK-") else tid_norm
        if key == tid or key_norm == tid_norm:
            return True
        if key_suffix and key_suffix == tid_suffix:
            return True
        # Composite tool-lifecycle keys only: require delimiter before the id token.
        if key.endswith(f":{tid}") or key.endswith(f"/{tid}"):
            return True
        if key_norm.endswith(f":{tid_norm}") or key_norm.endswith(f"/{tid_norm}"):
            return True
        if tid_suffix and (key_norm.endswith(f":{tid_suffix}") or key_norm.endswith(f"/{tid_suffix}")):
            return True
    return False


def _lifecycle_counts(
    run_projection: Mapping[str, Any],
    task_runtime_facts: Sequence[Mapping[str, Any]],
    *,
    proven_closed_readonly_dispatch_count: int = 0,
    proven_closed_terminal_negative_dispatch_count: int = 0,
) -> _LifecycleCounts:
    latest_task_facts = _latest_task_facts(task_runtime_facts)
    task_states = {task_id: _task_state(fact) for task_id, fact in latest_task_facts.items()}
    open_task_count = sum(state not in _TERMINAL_TASK_STATES for state in task_states.values())
    active_task_count = sum(state in _ACTIVE_LIFECYCLE_STATES for state in task_states.values())
    failed_task_count = sum(state in _FAILED_TASK_STATES for state in task_states.values())
    terminal_task_ids = {task_id for task_id, state in task_states.items() if state in _TERMINAL_TASK_STATES}

    lifecycle = _mapping(run_projection.get("tool_lifecycle"))
    latest_tool_lifecycles = _mapping(lifecycle.get("latest_by_task"))
    missing_tool_keys = {
        key
        for key in _string_tuple(lifecycle.get("missing_required_task_keys"))
        if not _task_key_matches_terminal_ids(key, terminal_task_ids)
    }
    active_tool_keys = {
        _clean_string(task_key)
        for task_key, raw_event in latest_tool_lifecycles.items()
        if _clean_string(task_key)
        and _clean_string(_mapping(raw_event).get("status")).lower() in _ACTIVE_LIFECYCLE_STATES
        and not _task_key_matches_terminal_ids(str(task_key), terminal_task_ids)
    }
    open_tool_keys = missing_tool_keys | active_tool_keys
    dispatched_count = _non_negative_int(lifecycle.get("dispatched_tool_calls_count"))
    readonly_count = (
        proven_closed_readonly_dispatch_count
        if type(proven_closed_readonly_dispatch_count) is int
        and 0 <= proven_closed_readonly_dispatch_count <= dispatched_count
        else 0
    )
    terminal_negative_count = (
        proven_closed_terminal_negative_dispatch_count
        if type(proven_closed_terminal_negative_dispatch_count) is int
        and 0 <= proven_closed_terminal_negative_dispatch_count <= dispatched_count - readonly_count
        else 0
    )
    # Verified terminal negatives neither need mutation receipts nor count as
    # reads. Only the complete owner analysis may remove this obligation.
    expected_effect_count = dispatched_count - readonly_count - terminal_negative_count
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
    refs = {reference for fact in (*task_runtime_facts, *ledger_facts) if (reference := _fact_reference(fact))}
    gates = run_projection.get("gates")
    if isinstance(gates, Sequence) and not isinstance(gates, (str, bytes, bytearray)):
        for raw_gate in gates:
            gate = _mapping(raw_gate)
            refs.update(
                value for key in ("append_id", "content_id", "job_token_id") if (value := _clean_string(gate.get(key)))
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
                    elif reference := _clean_string(raw_ref):
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


def _closed_tool_result_identity(row: dict[str, Any], *, committed_readonly: bool = False) -> dict[str, Any]:
    """Compare authority content across raw and typed result projections."""
    optional_empty_fields = {"error", "effect_receipt"}
    if committed_readonly:
        optional_empty_fields.update(
            {"effect_receipt_commit", "directed_effect_claim_status", "directed_effect_mutation_status"}
        )
    return {
        key: value
        for key, value in row.items()
        if key != "execution_time_ms" and not (key in optional_empty_fields and value is None)
    }


def _observed_readonly_batch(
    receipt: Mapping[str, Any],
    observations: Sequence[CommittedTurnReceiptObservationV1],
    *,
    workspace: str,
) -> Mapping[str, Any] | None:
    """Bind a missing batch to its immutable owner's exact read-only witness."""
    matching = [
        observed
        for observed in observations
        if observed.status == "observed"
        and observed.workspace == workspace
        and (observed.run_id, observed.task_id, observed.turn_id)
        == (receipt.get("run_id"), receipt.get("task_id"), receipt.get("turn_id"))
    ]
    if len(matching) != 1:
        return None
    observed = matching[0]
    refs = receipt.get("batch_receipt_refs")
    if not isinstance(refs, list | tuple) or not refs:
        return None
    identities = tuple((_mapping(ref).get("batch_id"), _mapping(ref).get("receipt_hash")) for ref in refs)
    if identities != observed.batch_receipt_refs:
        return None
    try:
        batch = observed.batch_receipt
        batch_hash = stable_hash(batch)
    except (TypeError, ValueError):
        return None
    if (
        observed.effect_class != "readonly"
        or not observed.fact_event_id
        or observed.fact_event_seq <= 0
        or len(observed.outcome_hash) != 64
        or batch.get("batch_id") != observed.batch_id
        or batch.get("turn_id") != observed.turn_id
        or batch_hash != observed.batch_receipt_hash
        or batch.get("pending_async_count") != 0
        or batch.get("has_pending_async") is not False
        or batch.get("effect_receipts") != []
    ):
        return None
    rows = batch.get("results")
    if not isinstance(rows, list) or not rows:
        return None
    if tuple(_mapping(row).get("call_id") for row in rows) != observed.native_call_ids:
        return None
    for raw_row in rows:
        row = _mapping(raw_row)
        spec = ToolSpecRegistry.get(ToolSpecRegistry.get_canonical(_clean_string(row.get("tool_name"))))
        if spec is None or not spec.is_read_tool() or spec.is_write_tool() or spec.is_exec_tool():
            return None
        if any(
            row.get(key) is not None
            for key in (
                "effect_receipt",
                "effect_receipt_commit",
                "directed_effect_claim_status",
                "directed_effect_mutation_status",
            )
        ):
            return None
    return cast("Mapping[str, Any] | None", (batch))


def _closed_artifact_receipt_reuse(
    receipt: Mapping[str, Any], effect_closures: Sequence[ExecutionEffectClosureObservationV1], *, workspace: str
) -> bool:
    """Recognize a terminal read-only snapshot observation, never a new effect."""
    if (
        receipt.get("schema_version") != "tool_call_lifecycle_receipt.v1"
        or receipt.get("compatibility_mode") != "receipt_reuse"
        or receipt.get("dispatch_status") != "verified_existing_artifacts"
        or receipt.get("ok") is not True
        or any(
            receipt.get(key) != 0
            for key in (
                "native_tool_calls_count",
                "decoded_tool_calls_count",
                "dispatched_tool_calls_count",
                "tool_result_count",
            )
        )
        or receipt.get("batch_receipt_refs") != []
        or receipt.get("native_tool_call_envelope_refs") != []
        or receipt.get("dropped_tool_calls") != []
        or receipt.get("batch_receipt_hash") != ""
        or receipt.get("provider_response_hash") != ""
        or receipt.get("pending_async_count") not in (None, 0)
        or receipt.get("has_pending_async") not in (None, False)
    ):
        return False
    refs = receipt.get("effect_receipt_refs")
    if not isinstance(refs, list) or not refs or receipt.get("effect_receipt_count") != len(refs):
        return False
    prefix = "execution-broker://project-verification/artifact/"
    seen: set[str] = set()
    for raw_ref in refs:
        ref = _mapping(raw_ref)
        value = _clean_string(ref.get("receipt_ref"))
        suffix = value.removeprefix(prefix)
        if (
            ref.get("source") != "runtime.execution_broker.project_artifact_receipt.v1"
            or ref.get("receipt_outcome") != "success"
            or not value.startswith(prefix)
            or len(suffix) != 64
            or any(character not in "0123456789abcdef" for character in suffix)
            or value in seen
        ):
            return False
        seen.add(value)
    matching = [
        closure
        for closure in effect_closures
        if closure.status == "closed"
        and closure.execution_attempt.workspace == workspace
        and closure.execution_attempt.run_id == receipt.get("run_id")
        and closure.execution_attempt.external_task_id == receipt.get("task_id")
    ]
    return len(matching) == 1 and not matching[0].receipts


def _negative_terminal_result_matches(
    result: Mapping[str, Any],
    receipt: Mapping[str, Any],
    effect_closures: Sequence[ExecutionEffectClosureObservationV1],
    *,
    workspace: str,
    raw_result: Mapping[str, Any] | None = None,
) -> bool:
    """Match a failure only to the owner's independent, terminal negative proof."""
    physical = _mapping(result.get("result"))
    recovery = _mapping(physical.get("effect_recovery"))
    code = physical.get("exit_code")
    failure_kind = physical.get("failure_kind")
    valid_failure = (
        failure_kind == "file_prewrite_rejected" and code is None and result.get("tool_name") == "edit_blocks"
    ) or (
        failure_kind == "physical_result_failed"
        and result.get("tool_name") == "execute_command"
        and type(code) is int
        and code > 0
    )
    if (
        result.get("status") not in {"error", "failed", "cancelled"}
        or result.get("effect_receipt") is not None
        or result.get("effect_receipt_commit") is not None
        or result.get("directed_effect_claim_status") != "claimed"
        or result.get("directed_effect_mutation_status") != "failed"
        or physical.get("schema_version") != "roles.adapters.directed_effect_physical_failure.v1"
        or not valid_failure
    ):
        return False
    matching = [
        failure
        for closure in effect_closures
        if closure.status == "closed"
        and closure.execution_attempt.workspace == workspace
        and closure.execution_attempt.run_id == receipt.get("run_id")
        and closure.execution_attempt.external_task_id == receipt.get("task_id")
        for failure in closure.terminal_failures
        if failure.operation_id == recovery.get("operation_id")
        and failure.turn_id == receipt.get("turn_id")
        and failure.tool_call_id == result.get("call_id")
        and failure.normalized_tool_name == result.get("tool_name")
        and failure.dead_letter_event_id == recovery.get("event_id")
        and failure.dead_letter_version == recovery.get("version")
        and failure.resolution_evidence_ref == recovery.get("resolution_evidence_ref")
        and failure.resolution_evidence_hash == recovery.get("resolution_evidence_hash")
        and failure.exit_code == code
        and failure.failure_kind == failure_kind
        and failure.physical_failure_hash == stable_hash(dict(physical))
        and failure.owner_result_hash == stable_hash({k: v for k, v in result.items() if k != "execution_time_ms"})
        and (
            raw_result is None
            or failure.owner_raw_result_hash
            == stable_hash({k: v for k, v in raw_result.items() if k != "execution_time_ms"})
        )
    ]
    return len(matching) == 1


def _terminal_execution_owners_are_closed(
    task_runtime_facts: Sequence[Mapping[str, Any]],
    effect_closures: Sequence[ExecutionEffectClosureObservationV1],
    *,
    workspace: str,
) -> bool:
    """Account for each canonical execution identity, including old attempts."""
    fields = ("task_id", "external_task_id", "session_id", "attempt", "role_id", "worker_id", "run_id")
    expected: set[tuple[Any, ...]] = set()
    for fact in task_runtime_facts:
        row = _mapping(fact.get("task_row_snapshot"))
        session = _mapping(_mapping(row.get("metadata")).get("runtime_execution"))
        # A role-only hint predates an execution claim; it is not an owner.
        if not any(key in session for key in (*fields[:4], *fields[5:], "lease_expires_at")):
            continue
        try:
            attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(
                {
                    "schema_version": "task-runtime.execution-attempt-identity/1",
                    "workspace": workspace,
                    **{key: session[key] for key in (*fields, "lease_expires_at")},
                }
            )
        except (KeyError, TypeError, ValueError):
            return False
        if attempt.run_id != fact.get("run_id") or str(attempt.task_id) != str(row.get("id")):
            return False
        expected.add(tuple(getattr(attempt, key) for key in fields))
    observed: set[tuple[Any, ...]] = set()
    for closure in effect_closures:
        attempt = closure.execution_attempt
        key = tuple(getattr(attempt, field) for field in fields)
        if closure.status != "closed" or attempt.workspace != workspace or key not in expected or key in observed:
            return False
        observed.add(key)
    return observed == expected


def _lifecycle_batch_witness_key(receipt: Mapping[str, Any], *, workspace: str) -> tuple[Any, ...] | None:
    """Identify a bound batch proof independently from projection annotations."""
    refs = receipt.get("batch_receipt_refs")
    if not refs:
        return None
    if not isinstance(refs, list | tuple):
        raise ValueError("batch witness references must be a sequence")
    scope = tuple(receipt.get(field) for field in ("run_id", "task_id", "turn_id"))
    identities = tuple((_mapping(ref).get("batch_id"), _mapping(ref).get("receipt_hash")) for ref in refs)
    if any(
        type(value) is not str or not value or value.strip() != value
        for value in (*scope, *(v for pair in identities for v in pair))
    ):
        raise ValueError("batch witness identity missing")
    validated_refs = cast(tuple[tuple[str, str], ...], identities)
    if any(len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest) for _, digest in validated_refs):
        raise ValueError("batch witness digest malformed")
    if len(set(validated_refs)) != len(validated_refs):
        raise ValueError("batch witness reference collision")
    return (workspace, *scope, validated_refs)


def _canonical_cleanup_lifecycle_events(
    events: Sequence[Any],
    ledger_facts: Sequence[Mapping[str, Any]],
    *,
    committed_turn_receipts: Sequence[CommittedTurnReceiptObservationV1],
    workspace: str,
    effect_closures: Sequence[ExecutionEffectClosureObservationV1] = (),
) -> list[Mapping[str, Any]] | None:
    """Bind the complete dispatch inventory before consuming derived fields."""
    identity_fields = ("append_id", "content_id", "run_id", "task_id")
    canonical: dict[tuple[str, ...], dict[str, Any]] = {}
    try:
        for fact in ledger_facts:
            if fact.get("event_type") != "tool_call_lifecycle":
                continue
            identity = tuple(fact.get(field) for field in identity_fields)
            if any(type(value) is not str or not value or value.strip() != value for value in identity):
                return None
            receipt = _mapping(fact.get("tool_call_lifecycle_receipt"))
            if (
                receipt.get("schema_version") != "tool_call_lifecycle_receipt.v1"
                or type(receipt.get("ok")) is not bool
                or any(
                    type(receipt.get(field)) is not int or receipt[field] < 0
                    for field in (
                        "native_tool_calls_count",
                        "decoded_tool_calls_count",
                        "dispatched_tool_calls_count",
                        "tool_result_count",
                        "effect_receipt_count",
                    )
                )
                or any(
                    type(receipt.get(field)) is not str
                    or not receipt[field]
                    or receipt[field].strip() != receipt[field]
                    for field in ("run_id", "task_id")
                )
                or type(receipt.get("turn_id")) is not str
                or receipt["turn_id"].strip() != receipt["turn_id"]
                or (
                    not receipt["turn_id"]
                    and not _closed_artifact_receipt_reuse(receipt, effect_closures, workspace=workspace)
                )
                or any(
                    field in receipt and type(receipt[field]) is not bool
                    for field in (
                        "dropped",
                        "text_fallback_requested",
                        "parser_attempted",
                        "native_tool_surface_absent_because_text_fallback",
                    )
                )
            ):
                return None
            expected = project_tool_lifecycle_event(receipt, append_id=fact["append_id"], content_id=fact["content_id"])
            key = tuple(expected[field] for field in identity_fields)
            # The compatibility normalizer must not repair a malformed current
            # witness or change its raw identity into a different ledger scope.
            if key != identity or stable_hash(receipt) != stable_hash(expected["receipt"]) or key in canonical:
                return None
            canonical[key] = expected
        observed: set[tuple[str, ...]] = set()
        consumed_witnesses: set[tuple[Any, ...]] = set()
        resolved: list[Mapping[str, Any]] = []
        for raw_event in events:
            event = _mapping(raw_event)
            receipt = _mapping(event.get("receipt"))
            if "append_id" in event or "content_id" in event:
                key = tuple(event.get(field) for field in identity_fields)
                bound = canonical.get(key)
                if bound is None or key in observed or "receipt" not in event or not set(event) <= set(bound):
                    return None
                # Hash comparison preserves JSON types: False is not integer0.
                # Missing row fields are rederived; present contradictions reject.
                if stable_hash(event) != stable_hash({field: bound[field] for field in event}):
                    return None
                witness = _lifecycle_batch_witness_key(bound["receipt"], workspace=workspace)
                if witness is not None:
                    if witness in consumed_witnesses:
                        return None
                    consumed_witnesses.add(witness)
                observed.add(key)
                resolved.append(bound)
                continue
            # Historical compact rows have no lifecycle schema/count inputs.
            # Only their already-existing strict immutable readonly owner proof
            # supplies the missing dispatch obligation; never default it to zero.
            if set(event) != {"status", "dispatched_tool_calls_count", "receipt"} or set(receipt) != {
                "run_id",
                "task_id",
                "turn_id",
                "batch_receipt_refs",
            }:
                return None
            batch = _observed_readonly_batch(receipt, committed_turn_receipts, workspace=workspace)
            if (
                batch is None
                or type(event["dispatched_tool_calls_count"]) is not int
                or event["dispatched_tool_calls_count"] != len(batch["results"])
                or event["status"] not in {"failed", "dropped", "dispatched", "ok"}
            ):
                return None
            witness = _lifecycle_batch_witness_key(receipt, workspace=workspace)
            if witness is None or witness in consumed_witnesses:
                return None
            consumed_witnesses.add(witness)
            resolved.append(event)
        return resolved if observed == set(canonical) else None
    except (TypeError, ValueError):
        return None


def _terminal_failure_has_closed_dispatches(
    task_runtime_facts: Sequence[Mapping[str, Any]],
    run_projection: Mapping[str, Any],
    ledger_facts: Sequence[Mapping[str, Any]],
    effect_closures: Sequence[ExecutionEffectClosureObservationV1] = (),
    *,
    committed_turn_receipts: Sequence[CommittedTurnReceiptObservationV1] = (),
    workspace: str = "",
) -> bool:
    """Keep the existing bool closeout contract over the shared strict analysis."""
    closed, _ = _terminal_dispatch_closure_analysis(
        task_runtime_facts,
        run_projection,
        ledger_facts,
        effect_closures,
        committed_turn_receipts=committed_turn_receipts,
        workspace=workspace,
    )
    return closed


def _terminal_dispatch_closure_analysis(
    task_runtime_facts: Sequence[Mapping[str, Any]],
    run_projection: Mapping[str, Any],
    ledger_facts: Sequence[Mapping[str, Any]],
    effect_closures: Sequence[ExecutionEffectClosureObservationV1] = (),
    *,
    committed_turn_receipts: Sequence[CommittedTurnReceiptObservationV1] = (),
    workspace: str = "",
) -> tuple[bool, int]:
    """Prove terminal resource closeout independently of product acceptance.

    Read denials have no effect receipt because policy prevented execution.
    Mutations require an exact persisted terminal TaskRuntime commit observation.
    Match every declared dispatch to its canonical result; classify tools through
    KernelOne's registry. Missing or ambiguous effect facts remain open;
    acceptance evidence is neither created nor waived.
    """

    states = [_task_state(fact) for fact in _latest_task_facts(task_runtime_facts).values()]
    if not states or any(state not in _TERMINAL_TASK_STATES for state in states):
        return False, 0
    lifecycle = _mapping(run_projection.get("tool_lifecycle"))
    events = lifecycle.get("events", ())
    if not _terminal_execution_owners_are_closed(task_runtime_facts, effect_closures, workspace=workspace):
        return False, 0
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes, bytearray)):
        return False, 0
    canonical_events = _canonical_cleanup_lifecycle_events(
        events,
        ledger_facts,
        committed_turn_receipts=committed_turn_receipts,
        workspace=workspace,
        effect_closures=effect_closures,
    )
    if canonical_events is None:
        return False, 0
    events = canonical_events
    proven_readonly_dispatch_count = 0
    batches: dict[str, list[Mapping[str, Any]]] = {}
    for fact in ledger_facts:
        batch = _mapping(_mapping(fact.get("physical_evidence")).get("batch_receipt"))
        turn_id = _clean_string(batch.get("turn_id"))
        if turn_id:
            batches.setdefault(turn_id, []).append(batch)
    # Unknown execution owners remain resources, even when a transport batch
    # happens to contain only read results.
    if any(closure.status != "closed" for closure in effect_closures):
        return False, 0
    for raw_event in events:
        event = _mapping(raw_event)
        receipt = _mapping(event.get("receipt"))
        dispatched = _non_negative_int(event.get("dispatched_tool_calls_count"))
        if not receipt or _clean_string(event.get("status")).lower() in _ACTIVE_LIFECYCLE_STATES:
            return False, 0
        if dispatched == 0:
            if workspace and _closed_artifact_receipt_reuse(receipt, effect_closures, workspace=workspace):
                continue
            if receipt.get("dispatch_status") not in {"failed", "dropped"}:
                return False, 0
            # A pre-dispatch failure may have an otherwise valid empty decode.
            # Its ok flag is not an effect: require zero physical obligations.
            if receipt.get("ok") is not False and (
                not effect_closures
                or receipt.get("schema_version") != "tool_call_lifecycle_receipt.v1"
                or any(
                    receipt.get(key) != 0
                    for key in (
                        "native_tool_calls_count",
                        "decoded_tool_calls_count",
                        "dispatched_tool_calls_count",
                        "tool_result_count",
                        "effect_receipt_count",
                    )
                )
                or receipt.get("batch_receipt_refs")
                or receipt.get("native_tool_call_envelope_refs")
            ):
                return False, 0
            continue
        candidates = batches.get(_clean_string(receipt.get("turn_id")), [])
        committed_readonly = False
        if not candidates and workspace:
            observed_batch = _observed_readonly_batch(receipt, committed_turn_receipts, workspace=workspace)
            if observed_batch is not None:
                candidates = [observed_batch]
                committed_readonly = True
        if len(candidates) != 1:
            return False, 0
        batch = candidates[0]
        refs = receipt.get("batch_receipt_refs")
        if not isinstance(refs, Sequence) or not any(
            _clean_string(_mapping(ref).get("batch_id")) == _clean_string(batch.get("batch_id")) for ref in refs
        ):
            return False, 0
        results = batch.get("results")
        raw_results = batch.get("raw_results")
        if (
            not isinstance(results, list)
            or len(results) != dispatched
            or not isinstance(raw_results, list)
            or len(raw_results) != len(results)
            or any(type(row) is not dict for row in (*results, *raw_results))
        ):
            return False, 0
        # Typed read results add optional error/effect_receipt=None where raw
        # results omit them. Only those empty top-level fields and duration are
        # projection details. Non-null errors/effects, unknown fields and every
        # nested value remain exact; mutations still require authentic commits.
        try:
            for typed_row, raw_row in zip(results, raw_results, strict=True):
                if stable_hash(
                    _closed_tool_result_identity(typed_row, committed_readonly=committed_readonly)
                ) != stable_hash(
                    _closed_tool_result_identity(raw_row, committed_readonly=committed_readonly)
                ) and not _negative_terminal_result_matches(
                    typed_row, receipt, effect_closures, workspace=workspace, raw_result=raw_row
                ):
                    return False, 0
        except (TypeError, ValueError):
            return False, 0
        if batch.get("pending_async_count") != 0 or batch.get("has_pending_async") is not False:
            return False, 0
        successes = sum(_mapping(item).get("status") == "success" for item in results)
        if batch.get("success_count") != successes or batch.get("failure_count") != dispatched - successes:
            return False, 0
        call_ids: set[str] = set()
        for raw_result in results:
            result = _mapping(raw_result)
            call_id = _clean_string(result.get("call_id"))
            spec = ToolSpecRegistry.get(ToolSpecRegistry.get_canonical(_clean_string(result.get("tool_name"))))
            if (
                not call_id
                or call_id in call_ids
                or result.get("status") not in {"success", "error", "failed", "cancelled"}
            ):
                return False, 0
            call_ids.add(call_id)
            if spec is None:
                return False, 0
            if spec.is_write_tool() or spec.is_exec_tool():
                if _negative_terminal_result_matches(result, receipt, effect_closures, workspace=workspace):
                    continue
                effect = _mapping(result.get("effect_receipt"))
                commit = _mapping(result.get("effect_receipt_commit"))
                if directed_effect_receipt_v2_errors(effect, commit, prefix="closure") != ():
                    return False, 0
                # Legacy transport batches can wrap several DEO parent batches.
                # Operational closure binds the immutable owner receipt below;
                # transport/invocation mismatches remain delivery failures.
                if (effect.get("tool_call_id"), effect.get("normalized_tool_name")) != (
                    call_id,
                    spec.canonical_name,
                ):
                    return False, 0
                matching = [
                    observed
                    for closure in effect_closures
                    if closure.status == "closed"
                    and closure.execution_attempt.run_id == receipt.get("run_id")
                    and closure.execution_attempt.external_task_id == receipt.get("task_id")
                    for observed in closure.receipts
                    if observed.operation_id == effect.get("operation_id")
                    and (observed.batch_id, observed.tool_call_id, observed.normalized_tool_name)
                    == (effect.get("batch_id"), call_id, effect.get("normalized_tool_name"))
                    and all(
                        getattr(observed, field) == commit.get(key)
                        for field, key in (
                            ("receipt_ref", "receipt_ref"),
                            ("receipt_hash", "receipt_hash"),
                            ("receipt_binding_hash", "receipt_binding_hash"),
                            ("receipt_outcome", "receipt_outcome"),
                            ("commit_event_id", "event_id"),
                            ("commit_version", "version"),
                        )
                    )
                ]
                if len(matching) != 1:
                    return False, 0
                continue
            if not spec.is_read_tool():
                return False, 0
            if result.get("status") == "success" and not any(
                closure.status == "closed"
                and closure.execution_attempt.workspace == workspace
                and closure.execution_attempt.run_id == receipt.get("run_id")
                and closure.execution_attempt.external_task_id == receipt.get("task_id")
                for closure in effect_closures
            ):
                return False, 0
            if any(
                result.get(key) is not None
                for key in (
                    "effect_receipt",
                    "effect_receipt_commit",
                    "directed_effect_claim_status",
                    "directed_effect_mutation_status",
                )
            ):
                return False, 0
            proven_readonly_dispatch_count += 1
        if batch.get("effect_receipts") != [item["effect_receipt"] for item in results if item.get("effect_receipt")]:
            return False, 0
        envelopes = receipt.get("native_tool_call_envelope_refs")
        if (
            isinstance(envelopes, Sequence)
            and envelopes
            and {_clean_string(_mapping(item).get("call_id")) for item in envelopes} != call_ids
        ):
            return False, 0
    return True, proven_readonly_dispatch_count


def project_factory_settlement_barrier(
    *,
    workspace: str,
    factory_run_id: str,
    run_projection: Mapping[str, Any],
    task_runtime_facts: Sequence[Mapping[str, Any]],
    ledger_facts: Sequence[Mapping[str, Any]],
    consumed_run_ids: Sequence[str],
    effect_closures: Sequence[ExecutionEffectClosureObservationV1] = (),
    committed_turn_receipts: Sequence[CommittedTurnReceiptObservationV1] = (),
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
        modality for modality in _string_tuple(policy.get("missing_required_modalities")) if modality not in failed_set
    )
    # The same complete owner proof supplies both closure and the count of
    # reads that require no mutation receipt. Counts never grant release.
    terminal_resources_closed, proven_readonly_dispatch_count = _terminal_dispatch_closure_analysis(
        task_runtime_facts,
        run_projection,
        ledger_facts,
        effect_closures,
        committed_turn_receipts=committed_turn_receipts,
        workspace=query.workspace,
    )
    counts = _lifecycle_counts(
        run_projection,
        task_runtime_facts,
        proven_closed_readonly_dispatch_count=proven_readonly_dispatch_count,
        proven_closed_terminal_negative_dispatch_count=(
            sum(len(closure.terminal_failures) for closure in effect_closures) if terminal_resources_closed else 0
        ),
    )
    gate_count = _non_negative_int(run_projection.get("gate_count"))
    failed_gates = run_projection.get("failed_gates")
    failed_gate_count = (
        len(failed_gates)
        if isinstance(failed_gates, Sequence) and not isinstance(failed_gates, (str, bytes, bytearray))
        else 0
    )
    normalized_run_ids = _string_tuple(consumed_run_ids)
    evidence_refs = _evidence_references(run_projection, task_runtime_facts, ledger_facts)
    evidence_refs = tuple(
        sorted(
            {
                *evidence_refs,
                *(
                    ref
                    for observed in committed_turn_receipts
                    if observed.workspace == query.workspace
                    for ref in observed.evidence_refs
                ),
            }
        )
    )
    # Factory-scoped settlement: matching task_runtime.execution facts under
    # factory_run_id prove the run was observed. Director/workflow run_ids are
    # optional until dispatch — early CE/provider abort after PM creates rows
    # often has factory_run_id on facts but empty run_id (R51 lifecycle_open +
    # factory_run_not_found false positive that pinned workspace leases).
    scope_found = bool(task_runtime_facts)
    blocking_reasons = _blocking_reasons(
        scope_found=scope_found,
        gate_count=gate_count,
        failed_gate_count=failed_gate_count,
        missing_required_modalities=missing_required_modalities,
        failed_required_modalities=failed_required_modalities,
        counts=counts,
        run_projection=run_projection,
    )
    closed = bool(scope_found and counts.open_count == 0 and terminal_resources_closed)
    if scope_found and counts.open_count == 0 and not terminal_resources_closed:
        blocking_reasons = (*blocking_reasons, "resource_closure_unproven")
    passed = bool(
        closed
        and bool(run_projection.get("ok"))
        and counts.failed_count == 0
        and not missing_required_modalities
        and not failed_required_modalities
    )
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
