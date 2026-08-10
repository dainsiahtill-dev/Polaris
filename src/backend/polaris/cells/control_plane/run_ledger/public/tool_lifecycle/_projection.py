"""Tool-lifecycle requirement contracts and event/summary projections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from polaris.cells.control_plane.run_ledger.public.failure_evidence import (
    FailureClassV1,
    normalize_failure_class,
)
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle._helpers import (
    _TOOL_LIFECYCLE_OUTCOME_SCHEMA_VERSION,
    _TOOL_LIFECYCLE_REQUIREMENT_MISSING,
    _TOOL_LIFECYCLE_REQUIREMENT_NOT_REQUIRED,
    _TOOL_LIFECYCLE_REQUIREMENT_SCHEMA_VERSION,
    _canonical_lifecycle_outcome_projection,
    _clean_string,
    _int_value,
    _is_terminal_incomplete_materialization_seal,
    _legacy_lifecycle_outcome_projection,
    _lifecycle_outcome_projection_from_events,
    _lifecycle_task_identity,
    _mapping,
    _mapping_refs,
    _string_list,
    _tool_lifecycle_is_required,
    _tool_lifecycle_requirement_status,
    _tool_result_failed_is_recoverable_admission,
)
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle._receipts import (
    failure_evidence_from_lifecycle_receipt,
    native_tool_call_facts_from_lifecycle_receipt,
    normalize_tool_call_lifecycle_receipt,
)


@dataclass(frozen=True)
class ToolLifecycleRequirementV1:
    """One task-scoped requirement for authoritative tool lifecycle evidence.

    The requirement is an execution-control-plane fact. Capability grants such
    as a JobToken do not instantiate it: a Director materialization task must
    first be claimed, or another authoritative producer must emit this contract.
    """

    task_id: str
    run_id: str = ""
    turn_id: str = ""
    source: str = "task_runtime.execution"
    reason: str = "director_materialization_claimed"
    evidence_refs: tuple[str, ...] = ()
    schema_version: str = _TOOL_LIFECYCLE_REQUIREMENT_SCHEMA_VERSION
    required: bool = True

    def __post_init__(self) -> None:
        task_id = _clean_string(self.task_id)
        run_id = _clean_string(self.run_id)
        if not task_id and not run_id:
            raise ValueError("tool lifecycle requirement needs task_id or run_id")
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "turn_id", _clean_string(self.turn_id))
        object.__setattr__(self, "source", _clean_string(self.source) or "execution_control_plane")
        object.__setattr__(self, "reason", _clean_string(self.reason) or "tool_lifecycle_required")
        object.__setattr__(self, "evidence_refs", tuple(_string_list(self.evidence_refs)))

    def to_dict(self) -> dict[str, Any]:
        """Return the stable JSON projection used by Run Ledger events."""

        task_key, _, _, _, identity_source = _lifecycle_task_identity(
            {
                "task_id": self.task_id,
                "run_id": self.run_id,
                "turn_id": self.turn_id,
            }
        )
        return {
            "schema_version": self.schema_version,
            "required": bool(self.required),
            "task_key": task_key,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "task_identity_source": identity_source,
            "source": self.source,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
        }


def build_tool_lifecycle_requirement_run_ledger_event(
    requirement: ToolLifecycleRequirementV1,
    *,
    project_id: str = "",
) -> dict[str, Any]:
    """Build a canonical projection event from one structured requirement."""

    payload = requirement.to_dict()
    return {
        "event_type": "tool_lifecycle_requirement",
        "tool_lifecycle_requirement": payload,
        "task_id": payload["task_id"],
        "run_id": payload["run_id"],
        "turn_id": payload["turn_id"],
        "project_id": _clean_string(project_id),
    }


def project_tool_lifecycle_requirement(
    requirement_events: Sequence[Mapping[str, Any]],
    lifecycle_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project task-scoped lifecycle obligations from structured facts only.

    Lifecycle receipts imply their own satisfied obligation. Requirement events
    carry the execution fact that activates fail-closed behavior before a
    receipt exists. No prompt text, stage label, or diagnostic string is read.

    Complexity:
        O(r + e) time and O(t) memory for ``r`` requirement facts, ``e``
        lifecycle rows, and ``t`` unique task identities.
    """

    obligations: dict[str, dict[str, Any]] = {}
    for raw_event in requirement_events:
        if not isinstance(raw_event, Mapping):
            continue
        requirement_raw = raw_event.get("tool_lifecycle_requirement")
        requirement = _mapping(requirement_raw) if requirement_raw is not None else _mapping(raw_event)
        if requirement.get("required") is False:
            continue
        task_key, task_id, run_id, turn_id, identity_source = _lifecycle_task_identity(requirement)
        obligations[task_key] = {
            "task_key": task_key,
            "task_id": task_id,
            "run_id": run_id,
            "turn_id": turn_id,
            "task_identity_source": identity_source,
            "source": _clean_string(requirement.get("source")) or "execution_control_plane",
            "reason": _clean_string(requirement.get("reason")) or "tool_lifecycle_required",
            "evidence_refs": _string_list(requirement.get("evidence_refs")),
        }
    for raw_event in lifecycle_events:
        if not isinstance(raw_event, Mapping):
            continue
        task_key, task_id, run_id, turn_id, identity_source = _lifecycle_task_identity(raw_event)
        obligations.setdefault(
            task_key,
            {
                "task_key": task_key,
                "task_id": task_id,
                "run_id": run_id,
                "turn_id": turn_id,
                "task_identity_source": identity_source,
                "source": "tool_call_lifecycle_receipt",
                "reason": "lifecycle_evidence_present",
                "evidence_refs": [],
            },
        )
    required_task_keys = list(obligations)
    return {
        "schema_version": _TOOL_LIFECYCLE_REQUIREMENT_SCHEMA_VERSION,
        "required": bool(required_task_keys),
        "state": "required" if required_task_keys else _TOOL_LIFECYCLE_REQUIREMENT_NOT_REQUIRED,
        "required_task_keys": required_task_keys,
        "obligations": list(obligations.values()),
    }


def project_tool_lifecycle_event(
    value: Any,
    *,
    append_id: Any = "",
    content_id: Any = "",
) -> dict[str, Any]:
    """Project one lifecycle receipt into the canonical Run Ledger read-model row.

    Boundary:
        The projection is derived only from ``tool_call_lifecycle_receipt.v1``.
        It centralizes lifecycle counters, dropped/failed flags, receipt refs and
        lifecycle-derived failure evidence for Run Ledger projections.

    Complexity:
        O(e + d) time and memory through lifecycle normalization and native-tool
        fact projection, where ``e`` is envelope refs and ``d`` is dropped-call
        refs.
    """

    lifecycle = normalize_tool_call_lifecycle_receipt(value)
    native_facts = native_tool_call_facts_from_lifecycle_receipt(lifecycle)
    native_count = _int_value(native_facts.get("native_tool_calls_count"))
    native_names = list(native_facts.get("native_tool_call_names") or [])
    decoded_count = _int_value(lifecycle.get("decoded_tool_calls_count"))
    dispatched_count = _int_value(lifecycle.get("dispatched_tool_calls_count"))
    result_count = _int_value(lifecycle.get("tool_result_count"))
    effect_count = _int_value(lifecycle.get("effect_receipt_count"))
    dispatch_status = _clean_string(lifecycle.get("dispatch_status"))
    dropped = bool(lifecycle.get("dropped")) or dispatch_status == "dropped"
    if native_count > 0 and dispatched_count <= 0:
        dropped = True
    failed = not bool(lifecycle.get("ok", False))
    failure_evidence = failure_evidence_from_lifecycle_receipt(lifecycle)
    task_key, task_id, run_id, turn_id, identity_source = _lifecycle_task_identity(lifecycle)
    row = {
        "status": dispatch_status or ("dropped" if dropped else "ok"),
        "failure_class": _clean_string(lifecycle.get("failure_class")),
        "reason": _clean_string(lifecycle.get("reason")),
        "ok": not failed,
        "failed": failed,
        "native_tool_calls_count": native_count,
        "native_tool_call_names": native_names,
        "decoded_tool_calls_count": decoded_count,
        "dispatched_tool_calls_count": dispatched_count,
        "tool_result_count": result_count,
        "effect_receipt_count": effect_count,
        "dropped": dropped,
        "provider_response_hash": _clean_string(lifecycle.get("provider_response_hash")),
        "batch_receipt_hash": _clean_string(lifecycle.get("batch_receipt_hash")),
        "batch_receipt_refs": _mapping_refs(lifecycle.get("batch_receipt_refs")),
        "effect_receipt_refs": _mapping_refs(lifecycle.get("effect_receipt_refs")),
        "receipt": lifecycle,
        "task_key": task_key,
        "task_id": task_id,
        "run_id": run_id,
        "turn_id": turn_id,
        "task_identity_source": identity_source,
        "append_id": _clean_string(append_id),
        "content_id": _clean_string(content_id),
    }
    if failure_evidence:
        row["failure_evidence"] = failure_evidence
    return row


def summarize_tool_lifecycle_events(
    events: Sequence[Mapping[str, Any]],
    *,
    requirement: bool = False,
    requirement_projection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize canonical lifecycle event rows for Run Ledger projections.

    Boundary:
        Input rows must already come from :func:`project_tool_lifecycle_event`.
        This helper owns aggregate lifecycle counters so Run Ledger projections
        do not maintain a second hand-written interpretation of event fields.

    Complexity:
        O(n + m) time and memory where ``n`` is lifecycle event count and ``m``
        is the number of native tool names / failure evidence rows.
    """

    native_count = 0
    decoded_count = 0
    dispatched_count = 0
    result_count = 0
    effect_count = 0
    dropped_count = 0
    failed_count = 0
    native_names: list[str] = []
    failure_evidence: list[dict[str, Any]] = []
    projected_events: list[dict[str, Any]] = []
    for raw_event in events:
        if not isinstance(raw_event, Mapping):
            continue
        event = dict(raw_event)
        projected_events.append(event)
        native_count += _int_value(event.get("native_tool_calls_count"))
        decoded_count += _int_value(event.get("decoded_tool_calls_count"))
        dispatched_count += _int_value(event.get("dispatched_tool_calls_count"))
        result_count += _int_value(event.get("tool_result_count"))
        effect_count += _int_value(event.get("effect_receipt_count"))
        native_names.extend(name for item in event.get("native_tool_call_names") or [] if (name := _clean_string(item)))
        if bool(event.get("dropped")):
            dropped_count += 1
        if bool(event.get("failed")):
            failed_count += 1
        evidence = event.get("failure_evidence")
        if isinstance(evidence, Mapping):
            failure_evidence.append(dict(evidence))
    structured_requirement = _mapping(requirement_projection)
    required_task_keys = _string_list(structured_requirement.get("required_task_keys"))
    declared_requirement = bool(requirement or structured_requirement.get("required"))
    outcome_projection = _lifecycle_outcome_projection_from_events(
        projected_events,
        source="event_rows",
        degraded=False,
        requirement=declared_requirement,
        required_task_keys=required_task_keys,
    )
    normalized_requirement_projection = {
        "schema_version": _TOOL_LIFECYCLE_REQUIREMENT_SCHEMA_VERSION,
        "required": bool(outcome_projection["requirement"]),
        "state": ("required" if bool(outcome_projection["requirement"]) else _TOOL_LIFECYCLE_REQUIREMENT_NOT_REQUIRED),
        "required_task_keys": list(outcome_projection["required_task_keys"]),
        "missing_required_task_keys": list(outcome_projection["missing_required_task_keys"]),
        "obligations": [
            dict(item) for item in structured_requirement.get("obligations") or [] if isinstance(item, Mapping)
        ],
    }
    return {
        "ok": bool(outcome_projection["ok"]),
        "event_count": len(projected_events),
        "native_tool_calls_count": native_count,
        "decoded_tool_calls_count": decoded_count,
        "dispatched_tool_calls_count": dispatched_count,
        "tool_result_count": result_count,
        "effect_receipt_count": effect_count,
        "native_tool_call_names": list(dict.fromkeys(native_names)),
        "dropped_count": dropped_count,
        "failed_count": failed_count,
        "failure_evidence": failure_evidence,
        "events": projected_events,
        "requirement_projection": normalized_requirement_projection,
        **outcome_projection,
    }


def project_tool_lifecycle_summary(summary: Mapping[str, Any] | None) -> dict[str, Any]:
    """Project the canonical lifecycle summary into Run Ledger read-model shape.

    Boundary:
        ``summarize_tool_lifecycle_events`` owns lifecycle aggregation and this
        helper owns the read-model field projection for that aggregate. Generic
        Run Ledger projections should not hand-copy lifecycle count/name/event
        fields because that recreates a second summary contract.

    Complexity:
        O(n + m) over native tool names, failure evidence rows, and events; O(n)
        memory for the copied projection lists.
    """

    lifecycle = summary if isinstance(summary, Mapping) else {}
    if not lifecycle:
        lifecycle = empty_tool_lifecycle_summary()
    failure_evidence_raw = lifecycle.get("failure_evidence")
    events_raw = lifecycle.get("events")
    failure_evidence = (
        [dict(item) for item in failure_evidence_raw if isinstance(item, Mapping)]
        if isinstance(failure_evidence_raw, list)
        else []
    )
    events = [dict(item) for item in events_raw if isinstance(item, Mapping)] if isinstance(events_raw, list) else []
    requirement_projection_raw = lifecycle.get("requirement_projection")
    requirement_projection = dict(requirement_projection_raw) if isinstance(requirement_projection_raw, Mapping) else {}
    outcome_projection = _canonical_lifecycle_outcome_projection(lifecycle)
    if outcome_projection is None:
        outcome_projection = _legacy_lifecycle_outcome_projection(lifecycle, events)
    return {
        "ok": bool(outcome_projection["ok"]),
        "event_count": _int_value(lifecycle.get("event_count")),
        "native_tool_calls_count": _int_value(lifecycle.get("native_tool_calls_count")),
        "decoded_tool_calls_count": _int_value(lifecycle.get("decoded_tool_calls_count")),
        "dispatched_tool_calls_count": _int_value(lifecycle.get("dispatched_tool_calls_count")),
        "tool_result_count": _int_value(lifecycle.get("tool_result_count")),
        "effect_receipt_count": _int_value(lifecycle.get("effect_receipt_count")),
        "native_tool_call_names": _string_list(lifecycle.get("native_tool_call_names")),
        "dropped_count": _int_value(lifecycle.get("dropped_count")),
        "failed_count": _int_value(lifecycle.get("failed_count")),
        "failure_evidence": failure_evidence,
        "events": events,
        "requirement_projection": requirement_projection,
        **outcome_projection,
    }


def project_tool_lifecycle_failure_status(summary: Mapping[str, Any] | None) -> dict[str, Any]:
    """Project aggregate lifecycle failure status from a canonical summary.

    Boundary:
        Run Ledger owns the precedence between dropped dispatch and other
        lifecycle failures. Runtime/UI projections should consume this helper
        instead of reinterpreting ``dropped_count`` / ``failed_count`` locally.

    Complexity:
        O(n) time and O(1) additional memory over projected lifecycle events.
    """

    lifecycle = summary if isinstance(summary, Mapping) else {}
    projected = project_tool_lifecycle_summary(lifecycle)
    unresolved_raw = projected.get("unresolved_by_task")
    unresolved_by_task = unresolved_raw if isinstance(unresolved_raw, Mapping) else {}
    metadata = _mapping(projected.get("outcome_projection"))
    degraded = bool(metadata.get("degraded"))
    fallback = _clean_string(metadata.get("fallback"))
    if projected.get("requirement_status") == _TOOL_LIFECYCLE_REQUIREMENT_MISSING:
        return {
            "failed": True,
            "status": _TOOL_LIFECYCLE_REQUIREMENT_MISSING,
            "failure_class": FailureClassV1.TOOL_LIFECYCLE_MISSING.value,
            "reason": "required tool lifecycle evidence is missing",
            "degraded": degraded,
            "fallback": fallback,
        }
    if unresolved_by_task:
        latest = next(reversed(tuple(unresolved_by_task.values())))
        latest_event = dict(latest) if isinstance(latest, Mapping) else {}
        dropped = bool(latest_event.get("dropped"))
        failure_class = normalize_failure_class(
            _clean_string(latest_event.get("failure_class"))
            or (FailureClassV1.TOOL_DISPATCH_DROPPED.value if dropped else FailureClassV1.TOOL_LIFECYCLE_FAILED.value)
        )
        # M08 fix: a tool that RAN and returned ok=False (TOOL_RESULT_FAILED) is a
        # recoverable per-tool execution failure / product-quality defect, NOT a
        # control-plane integrity break. canonical_execution integrity is about
        # MISSING/DROPPED/missing-effect LIFECYCLE EVIDENCE, not a single tool's
        # recoverable denial; real_run_gate / delivery_depth catch the product
        # defect on a separate plane. (L1-01 m03-r24 DELIVERY_VERIFIED_CHAIN_
        # CONTROL_PLANE_FAIL: one CAS-race TOOL_RESULT_FAILED broke canonical
        # despite the product chain verifying. R195+Layer 2 made specific denial
        # modes non-fatal at the tool surface; this extends the separation to ALL
        # per-tool execution failures.)
        if failure_class == FailureClassV1.TOOL_RESULT_FAILED.value and (
            not dropped or _tool_result_failed_is_recoverable_admission(latest_event)
        ):
            return {
                "failed": False,
                "status": "recoverable_tool_failure",
                "failure_class": failure_class,
                "reason": _clean_string(latest_event.get("reason")) or failure_class,
                "degraded": degraded,
                "fallback": fallback,
            }
        return {
            "failed": True,
            "status": _clean_string(latest_event.get("status")) or ("dropped" if dropped else "failed"),
            "failure_class": failure_class,
            "reason": _clean_string(latest_event.get("reason")) or failure_class,
            "degraded": degraded,
            "fallback": fallback,
        }
    # Terminal incomplete seals satisfy integrity (not missing) but remain
    # attributable as outcome failures for multi-task incomplete materialization.
    latest_by_task_raw = projected.get("latest_by_task")
    latest_by_task = latest_by_task_raw if isinstance(latest_by_task_raw, Mapping) else {}
    for raw_event in reversed(tuple(latest_by_task.values())):
        if not isinstance(raw_event, Mapping):
            continue
        event = dict(raw_event)
        if not _is_terminal_incomplete_materialization_seal(event):
            continue
        return {
            "failed": True,
            "status": _clean_string(event.get("status")) or "blocked",
            "failure_class": normalize_failure_class(
                event.get("failure_class") or FailureClassV1.INCOMPLETE_MATERIALIZATION.value
            ),
            "reason": _clean_string(event.get("reason")) or "incomplete_materialization",
            "degraded": degraded,
            "fallback": fallback,
        }
    return {
        "failed": False,
        "status": "",
        "failure_class": "",
        "reason": "",
        "degraded": degraded,
        "fallback": fallback,
    }


def empty_tool_lifecycle_summary(*, requirement: bool = False) -> dict[str, Any]:
    """Return the canonical empty tool-lifecycle summary shape.

    Absence is neutral until an execution fact activates a requirement. Callers
    holding such a fact must pass ``requirement=True`` or use the structured
    requirement projection path in :func:`summarize_tool_lifecycle_events`.
    """

    requirement_status = _tool_lifecycle_requirement_status(
        requirement=requirement,
        evidence_present=False,
    )

    return {
        "ok": not requirement,
        "requirement": requirement,
        "requirement_status": requirement_status,
        "event_count": 0,
        "native_tool_calls_count": 0,
        "decoded_tool_calls_count": 0,
        "dispatched_tool_calls_count": 0,
        "tool_result_count": 0,
        "effect_receipt_count": 0,
        "native_tool_call_names": [],
        "dropped_count": 0,
        "failed_count": 0,
        "failure_evidence": [],
        "events": [],
        "requirement_projection": {
            "schema_version": _TOOL_LIFECYCLE_REQUIREMENT_SCHEMA_VERSION,
            "required": requirement,
            "state": "required" if requirement else _TOOL_LIFECYCLE_REQUIREMENT_NOT_REQUIRED,
            "required_task_keys": [],
            "missing_required_task_keys": [],
            "obligations": [],
        },
        "required_task_keys": [],
        "missing_required_task_keys": [],
        "latest_by_task": {},
        "unresolved_by_task": {},
        "unresolved_count": 0,
        "unresolved_dropped_count": 0,
        "unresolved_failed_count": 0,
        "outcome_projection": {
            "schema_version": _TOOL_LIFECYCLE_OUTCOME_SCHEMA_VERSION,
            "source": "event_rows",
            "degraded": False,
            "fallback": "",
            "requirement": requirement,
            "requirement_status": requirement_status,
        },
    }


def merge_tool_lifecycle_summaries(projects: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Merge project-level lifecycle summaries into a single read model.

    Boundary:
        This is a pure projection helper. It does not inspect ledger event rows
        and does not create lifecycle receipts.

    Complexity:
        O(n + m) time and memory, where ``n`` is project count and ``m`` is the
        total number of projected lifecycle events and native tool names.
    """

    totals = empty_tool_lifecycle_summary()
    requirement_flags: list[bool] = []
    missing_required_project_count = 0
    native_names: list[str] = []
    failure_evidence: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    required_task_keys: list[str] = []
    requirement_obligations: list[dict[str, Any]] = []
    for project in projects:
        lifecycle_raw = project.get("tool_lifecycle")
        lifecycle: Mapping[str, Any] = lifecycle_raw if isinstance(lifecycle_raw, Mapping) else {}
        lifecycle_required = _tool_lifecycle_is_required(lifecycle)
        requirement_flags.append(lifecycle_required)
        raw_project_events = lifecycle.get("events")
        project_has_lifecycle_evidence = isinstance(raw_project_events, list) and any(
            isinstance(item, Mapping) for item in raw_project_events
        )
        if lifecycle_required and not project_has_lifecycle_evidence:
            missing_required_project_count += 1
        if not lifecycle:
            continue
        totals["ok"] = bool(totals["ok"]) and bool(lifecycle.get("ok", True))
        for key in (
            "event_count",
            "native_tool_calls_count",
            "decoded_tool_calls_count",
            "dispatched_tool_calls_count",
            "tool_result_count",
            "effect_receipt_count",
            "dropped_count",
            "failed_count",
        ):
            totals[key] = _int_value(totals.get(key)) + _int_value(lifecycle.get(key))
        native_names.extend(
            name for item in lifecycle.get("native_tool_call_names") or [] if (name := _clean_string(item))
        )
        raw_failure_evidence = lifecycle.get("failure_evidence")
        if isinstance(raw_failure_evidence, list):
            failure_evidence.extend(dict(item) for item in raw_failure_evidence if isinstance(item, Mapping))
        requirement_projection = _mapping(lifecycle.get("requirement_projection"))
        required_task_keys.extend(_string_list(requirement_projection.get("required_task_keys")))
        raw_obligations = requirement_projection.get("obligations")
        if isinstance(raw_obligations, list):
            requirement_obligations.extend(dict(item) for item in raw_obligations if isinstance(item, Mapping))
        raw_events = lifecycle.get("events")
        if isinstance(raw_events, list):
            events.extend(dict(item) for item in raw_events if isinstance(item, Mapping))
    totals["native_tool_call_names"] = list(dict.fromkeys(native_names))
    totals["failure_evidence"] = failure_evidence
    totals["events"] = events
    requirement = any(requirement_flags) if requirement_flags else False
    required_task_keys = list(dict.fromkeys(required_task_keys))
    totals["requirement"] = requirement
    canonical_event_rows = bool(events) and all(
        isinstance(event, Mapping) and bool(_clean_string(event.get("task_key"))) for event in events
    )
    outcome_projection = (
        _lifecycle_outcome_projection_from_events(
            events,
            source="merged_event_rows",
            degraded=False,
            requirement=requirement,
            required_task_keys=required_task_keys,
        )
        if canonical_event_rows or required_task_keys
        else _legacy_lifecycle_outcome_projection(totals, events)
    )
    if missing_required_project_count:
        outcome_projection["ok"] = False
        outcome_projection["requirement_status"] = _TOOL_LIFECYCLE_REQUIREMENT_MISSING
        outcome_metadata = _mapping(outcome_projection.get("outcome_projection"))
        outcome_metadata["requirement_status"] = _TOOL_LIFECYCLE_REQUIREMENT_MISSING
        outcome_projection["outcome_projection"] = outcome_metadata
    totals["requirement_projection"] = {
        "schema_version": _TOOL_LIFECYCLE_REQUIREMENT_SCHEMA_VERSION,
        "required": requirement,
        "state": "required" if requirement else _TOOL_LIFECYCLE_REQUIREMENT_NOT_REQUIRED,
        "required_task_keys": required_task_keys,
        "missing_required_task_keys": list(outcome_projection.get("missing_required_task_keys") or []),
        "obligations": requirement_obligations,
    }
    totals.update(outcome_projection)
    return totals
