"""Run-ledger failure evidence taxonomy.

This module is the platform-level failure-class vocabulary for execution
control-plane evidence. It intentionally lives in ``control_plane.run_ledger``
because Run Ledger projections, QA verdicts and runtime result mapping all need
the same terms without importing role-runtime internals.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from polaris.kernelone.events.final_request_evidence import looks_like_failed_gate_evidence_context_payload

_TASK_BOUNDARY_FAILURE_BOOL_KEYS: tuple[str, ...] = (
    "repairable_by_director",
    "requires_ce_replan",
    "requires_pm_revision",
)
_TASK_BOUNDARY_VERDICT_SOURCE = "polaris.task_boundary_verdict.v1"


class FailureClassV1(str, Enum):
    """Canonical failure classes carried through run-ledger evidence."""

    PATCH_FILE_PROTOCOL_DISABLED = "PATCH_FILE_PROTOCOL_DISABLED"
    TEXT_TOOL_PROTOCOL_DISABLED = "TEXT_TOOL_PROTOCOL_DISABLED"
    TOOL_DISPATCH_DROPPED = "TOOL_DISPATCH_DROPPED"
    MISSING_BATCH_RECEIPT = "MISSING_BATCH_RECEIPT"
    MISSING_EFFECT_RECEIPT = "MISSING_EFFECT_RECEIPT"
    MISSING_TOOL_RESULT = "MISSING_TOOL_RESULT"
    TOOL_RESULT_FAILED = "TOOL_RESULT_FAILED"
    TOOL_LIFECYCLE_FAILED = "TOOL_LIFECYCLE_FAILED"
    TOOL_LIFECYCLE_UNKNOWN = "TOOL_LIFECYCLE_UNKNOWN"
    TOOL_LIFECYCLE_MISSING = "TOOL_LIFECYCLE_MISSING"
    REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED = "REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED"
    NO_MATERIALIZED_EFFECT = "NO_MATERIALIZED_EFFECT"
    DEPENDENCY_NOT_UNLOCKED = "DEPENDENCY_NOT_UNLOCKED"
    INCOMPLETE_MATERIALIZATION = "INCOMPLETE_MATERIALIZATION"
    MISSING_ENTRYPOINT_TARGET = "MISSING_ENTRYPOINT_TARGET"
    EXECUTION_EVIDENCE_MISSING = "EXECUTION_EVIDENCE_MISSING"
    IMPLEMENTATION_DEFECT = "IMPLEMENTATION_DEFECT"
    COMPILER_OR_TEST_FAILURE = "COMPILER_OR_TEST_FAILURE"
    IMPLEMENTATION_DEFECT_BOUNCE_LIMIT = "IMPLEMENTATION_DEFECT_BOUNCE_LIMIT"
    DEFERRED_FOLLOWUP_REQUIRED = "DEFERRED_FOLLOWUP_REQUIRED"
    BLUEPRINT_SCOPE_MISMATCH = "BLUEPRINT_SCOPE_MISMATCH"
    BLUEPRINT_VERIFY_INVALID = "BLUEPRINT_VERIFY_INVALID"
    CONTRACT_AMBIGUOUS = "CONTRACT_AMBIGUOUS"
    TEST_ENVIRONMENT_FAILURE = "TEST_ENVIRONMENT_FAILURE"
    ACCEPTANCE_INVALID = "ACCEPTANCE_INVALID"
    SECURITY_POLICY_VIOLATION = "SECURITY_POLICY_VIOLATION"
    RESOURCE_BUDGET_EXHAUSTED = "RESOURCE_BUDGET_EXHAUSTED"
    PROGRESS_STALLED = "PROGRESS_STALLED"
    MODEL_PROVIDER_FAILURE = "MODEL_PROVIDER_FAILURE"
    MODEL_PROVIDER_TIMEOUT = "MODEL_PROVIDER_TIMEOUT"
    TASK_BOUNDARY_FAILED = "TASK_BOUNDARY_FAILED"
    TASKBOARD_DEADLOCK = "TASKBOARD_DEADLOCK"
    LEDGER_PROJECTION_INCOMPLETE = "LEDGER_PROJECTION_INCOMPLETE"
    QUALITY_GATE_BLOCKED = "QUALITY_GATE_BLOCKED"
    ROLE_ADAPTER_EXCEPTION = "ROLE_ADAPTER_EXCEPTION"


def _failure_class_key(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip()).strip("_").casefold()


_FAILURE_CLASS_BY_KEY: dict[str, FailureClassV1] = {_failure_class_key(item.value): item for item in FailureClassV1}
# Backward-compatible aliases for legacy hand-written values that predate the
# canonical taxonomy.  New code must use the canonical enum members directly.
_FAILURE_CLASS_BY_KEY[_failure_class_key("pm_quality_gate_blocked")] = FailureClassV1.QUALITY_GATE_BLOCKED
_FAILURE_CLASS_BY_KEY[_failure_class_key("pm_runtime_exception")] = FailureClassV1.ROLE_ADAPTER_EXCEPTION
_FAILURE_CLASS_TOKEN_SEPARATORS = (":", ";", "\n")


def normalize_failure_class(value: Any, *, default: str | FailureClassV1 = "") -> str:
    """Normalize *value* to a canonical failure-class string when known.

    Unknown non-empty classes are preserved so older or experiment-specific
    evidence remains visible instead of being collapsed into an opaque bucket.
    """
    if isinstance(value, FailureClassV1):
        return value.value
    raw = str(value or "").strip()
    if not raw:
        return str(default.value if isinstance(default, FailureClassV1) else default)
    known = _FAILURE_CLASS_BY_KEY.get(_failure_class_key(raw))
    return known.value if known is not None else raw


def _failure_class_evidence_value(value: Any) -> str:
    """Project a failure-evidence class from structured rows, not prose.

    Some upstream metadata fields are composed by humans or older projections as
    ``CLASS; extra context`` or ``failure_class: CLASS``. The Run Ledger row must
    carry one stable class, so prefer the first known class token and otherwise
    preserve the first non-empty token.
    """

    if isinstance(value, FailureClassV1):
        return value.value
    raw = str(value or "").strip()
    if not raw:
        return ""

    candidates = [raw]
    for separator in _FAILURE_CLASS_TOKEN_SEPARATORS:
        next_candidates: list[str] = []
        for candidate in candidates:
            next_candidates.extend(candidate.split(separator))
        candidates = next_candidates

    fallback = ""
    for candidate in candidates:
        token = candidate.strip()
        if not token:
            continue
        known = _FAILURE_CLASS_BY_KEY.get(_failure_class_key(token))
        if known is not None:
            return known.value
        if not fallback:
            fallback = token
    return normalize_failure_class(fallback or raw)


def is_failure_class(value: Any, expected: FailureClassV1) -> bool:
    """Return whether *value* denotes *expected*, case-insensitively."""
    return normalize_failure_class(value).casefold() == expected.value.casefold()


def merge_failure_evidence_rows(
    existing: Any,
    *new_rows: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return JSON-safe failure evidence rows with stable de-duplication.

    Existing metadata fields may carry a single mapping, a list/tuple of
    mappings, or malformed legacy values. Only mapping rows are authoritative
    evidence; malformed values are ignored instead of being string-parsed.
    Complexity is O(n*m) in row count and row width for equality checks; current
    evidence lists are tiny, and preserving exact row dictionaries avoids
    lossy hashes or ad-hoc identity keys.
    """

    rows: list[dict[str, Any]] = []
    if isinstance(existing, Mapping):
        rows.append(dict(existing))
    elif isinstance(existing, (list, tuple)):
        rows.extend(dict(item) for item in existing if isinstance(item, Mapping))
    for row in new_rows:
        candidate = dict(row)
        if candidate not in rows:
            rows.append(candidate)
    return rows


def _dedupe_text_tokens(values: Any) -> tuple[str, ...]:
    tokens: list[str] = []
    seen: set[str] = set()
    try:
        iterator = iter(values)
    except TypeError:
        return ()
    for item in iterator:
        token = str(item or "").strip()
        if token and token not in seen:
            tokens.append(token)
            seen.add(token)
    return tuple(tokens)


def _bool_value(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def _truthy_metadata_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "disabled"}:
            return False
    return bool(value)


def _int_value(value: Any, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _dedupe_text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items: tuple[Any, ...] = (value,)
    elif isinstance(value, (list, tuple)):
        raw_items = tuple(value)
    elif isinstance(value, set):
        raw_items = tuple(sorted(value, key=lambda item: str(item)))
    else:
        return []
    rows: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        token = _clean_text(item)
        if token and token not in seen:
            rows.append(token)
            seen.add(token)
    return rows


def _json_safe_failure_evidence_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_safe_failure_evidence_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_failure_evidence_value(item) for item in value]
    if isinstance(value, set):
        return [_json_safe_failure_evidence_value(item) for item in sorted(value, key=lambda item: str(item))]
    return str(value)


def _task_boundary_verdict_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    nested = value.get("task_boundary_verdict")
    if isinstance(nested, Mapping):
        return dict(nested)
    return dict(value)


def task_boundary_failure_evidence_from_verdict(
    verdict: Mapping[str, Any],
) -> dict[str, Any]:
    """Project a failed TaskBoundary verdict into one failure-evidence row.

    Boundary:
        The Run Ledger public layer owns the canonical row shape. Callers pass a
        TaskBoundary verdict mapping, or RoleTurnResult metadata containing a
        ``task_boundary_verdict`` mapping. Successful verdicts return ``{}`` so
        callers do not accidentally append pass-state as failure evidence.

    Complexity:
        O(k + r) time and memory, where ``k`` is the fixed verdict metadata key
        count and ``r`` is the evidence-ref count.
    """

    if not isinstance(verdict, Mapping):
        return {}
    payload = _task_boundary_verdict_mapping(verdict)
    if bool(payload.get("ok")):
        return {}

    status = _clean_text(payload.get("status")) or "failed"
    failure_class = _failure_class_evidence_value(payload.get("failure_class")) or "TASK_BOUNDARY_FAILED"
    reason = _clean_text(payload.get("reason")) or "Task boundary failed"
    failure_stage = _clean_text(payload.get("failure_stage")) or "task_boundary"
    metadata: dict[str, Any] = {str(key): _json_safe_failure_evidence_value(value) for key, value in payload.items()}
    metadata.update(
        {
            "source": _TASK_BOUNDARY_VERDICT_SOURCE,
            "task_boundary_status": status,
            "failure_stage": failure_stage,
        }
    )

    row = FailureEvidenceV1(
        failure_class=failure_class,
        responsible_layer=_clean_text(payload.get("responsible_layer")) or "task_boundary",
        reason=reason,
        evidence_refs=tuple(_dedupe_text_list(payload.get("evidence_refs") or payload.get("evidence_ref"))),
        metadata=metadata,
    ).to_dict()
    row["failure_stage"] = failure_stage
    row["failure_classes"] = [failure_class]
    row["root_cause_hint"] = _clean_text(payload.get("root_cause_hint")) or reason
    row["detail"] = _clean_text(payload.get("detail")) or reason
    for key in _TASK_BOUNDARY_FAILURE_BOOL_KEYS:
        if key in payload:
            row[key] = _truthy_metadata_value(payload.get(key))
    return row


def merge_failure_evidence_payload(
    existing: Mapping[str, Any] | None,
    raw_evidence: Any,
) -> dict[str, Any]:
    """Merge aggregate/runtime failure-evidence payloads without prose parsing.

    Boundary:
        Mapping payloads are treated as already-shaped projections and overlay
        existing keys. List/tuple payloads are treated as structured evidence
        rows and projected into ``items``, ``failure_classes`` and
        ``evidence_refs``. Malformed values are ignored.

    Complexity:
        O(n*m) for row de-duplication plus O(n) for summary token projection.
    """

    payload = dict(existing) if isinstance(existing, Mapping) else {}
    if isinstance(raw_evidence, Mapping):
        raw_mapping = dict(raw_evidence)
        payload.update(raw_mapping)
        for nested_key in ("failure_evidence", "items"):
            payload = _merge_failure_evidence_rows_into_payload(payload, raw_mapping.get(nested_key))
        return payload
    return _merge_failure_evidence_rows_into_payload(payload, raw_evidence)


def looks_like_failure_evidence_payload(value: Any) -> bool:
    """Return whether *value* is structured failure evidence.

    Boundary:
        Run Ledger exposes the platform failure-evidence predicate as a public
        contract, while final-request evidence owns the context-slot shape
        detection. This wrapper keeps existing Run Ledger consumers on the same
        API without maintaining a second structural key table.

    Complexity:
        O(k) via the final-request evidence context predicate; O(1) memory.
    """

    return looks_like_failed_gate_evidence_context_payload(value)


def _merge_failure_evidence_rows_into_payload(
    payload: dict[str, Any],
    raw_rows: Any,
) -> dict[str, Any]:
    if not isinstance(raw_rows, (list, tuple)):
        return payload
    rows = [dict(item) for item in raw_rows if isinstance(item, Mapping)]
    if not rows:
        return payload
    payload["items"] = merge_failure_evidence_rows(payload.get("items"), *rows)

    existing_classes_raw = payload.get("failure_classes") or ()
    existing_classes: list[str] = []
    existing_known_keys: set[str] = set()
    for token in existing_classes_raw:
        normalized = _failure_class_evidence_value(token)
        if not normalized:
            continue
        key = _failure_class_key(normalized)
        known = _FAILURE_CLASS_BY_KEY.get(key)
        if known is not None:
            if key in existing_known_keys:
                continue
            existing_known_keys.add(key)
        existing_classes.append(normalized)

    new_tokens: list[str] = []
    seen_keys: set[str] = set(existing_known_keys)
    for row in rows:
        token = _failure_class_evidence_value(row.get("failure_class"))
        if not token:
            continue
        key = _failure_class_key(token)
        known = _FAILURE_CLASS_BY_KEY.get(key)
        if known is not None:
            if key in seen_keys:
                continue
            seen_keys.add(key)
        elif token in existing_classes or token in new_tokens:
            continue
        new_tokens.append(token)

    payload["failure_classes"] = (*existing_classes, *new_tokens)
    evidence_ref_tokens: list[str] = list(_dedupe_text_tokens(payload.get("evidence_refs") or ()))
    for row in rows:
        raw_refs = row.get("evidence_refs")
        if not isinstance(raw_refs, (list, tuple)):
            continue
        evidence_ref_tokens.extend(str(ref or "") for ref in raw_refs)
    payload["evidence_refs"] = _dedupe_text_tokens(evidence_ref_tokens)
    return payload


def summarize_failed_gate_evidence_context_slot(value: Any) -> dict[str, Any]:
    """Project a final-request failed-gate context slot from structured evidence.

    Boundary:
        This helper is a Run Ledger public projection for ContextOS/final-request
        audit surfaces. It consumes already-shaped failure-evidence payloads and
        rows, and does not parse diagnostic prose.

    Complexity:
        O(n*m) through :func:`merge_failure_evidence_payload`, where n is the
        tiny evidence row count and m is row width.
    """

    found = merge_failure_evidence_payload({}, value)
    raw_evidence_items = found.get("items")
    evidence_items = list(raw_evidence_items) if isinstance(raw_evidence_items, (list, tuple)) else []
    raw_diagnostics = found.get("diagnostics")
    diagnostics = list(raw_diagnostics) if isinstance(raw_diagnostics, (list, tuple)) else []
    raw_quality_errors = found.get("quality_errors")
    quality_errors = list(raw_quality_errors) if isinstance(raw_quality_errors, (list, tuple)) else []
    first_item = next((dict(item) for item in evidence_items if isinstance(item, Mapping)), {})
    quality_metrics = found.get("quality_metrics")
    quality_minimums = found.get("quality_minimums")
    return {
        "schema_version": "polaris.failed_gate_evidence.context_slot.v1",
        "source_schema_version": str(found.get("schema_version") or ""),
        "source": str(found.get("source") or found.get("modality") or "failed_gate_evidence"),
        "failure_class": str(found.get("failure_class") or first_item.get("failure_class") or ""),
        "failure_classes": list(_dedupe_text_tokens(found.get("failure_classes") or ())),
        "failure_evidence_count": len(evidence_items),
        "responsible_layer": str(found.get("responsible_layer") or first_item.get("responsible_layer") or ""),
        "repairable_by_director": _bool_value(
            found.get("repairable_by_director", first_item.get("repairable_by_director"))
        ),
        "requires_ce_replan": _bool_value(found.get("requires_ce_replan", first_item.get("requires_ce_replan"))),
        "requires_pm_revision": _bool_value(found.get("requires_pm_revision", first_item.get("requires_pm_revision"))),
        "evidence_refs": list(_dedupe_text_tokens(found.get("evidence_refs") or ())),
        "command": str(found.get("command") or found.get("verifier_command") or ""),
        "exit_code": _int_value(found.get("exit_code")),
        "diagnostic_count": len(diagnostics),
        "quality_error_count": len(quality_errors),
        "quality_metrics": dict(quality_metrics) if isinstance(quality_metrics, Mapping) else {},
        "quality_minimums": dict(quality_minimums) if isinstance(quality_minimums, Mapping) else {},
        "missing_target_file_count": len(_dedupe_text_tokens(found.get("missing_target_files") or ())),
        "repair_target_file_count": len(_dedupe_text_tokens(found.get("repair_target_files") or ())),
        "failed_required_modalities": list(_dedupe_text_tokens(found.get("failed_required_modalities") or ())),
        "failed_checks": list(_dedupe_text_tokens(found.get("failed_checks") or ())),
    }


def _explicit_failure_classes_from_rows(rows: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    found_explicit_classes = False
    failure_classes: list[str] = []
    seen: set[str] = set()
    for row in rows:
        raw_classes = row.get("failure_classes")
        if not isinstance(raw_classes, (list, tuple, set)):
            continue
        found_explicit_classes = True
        for raw_class in raw_classes:
            failure_class = _failure_class_evidence_value(raw_class)
            if not failure_class or failure_class in seen:
                continue
            failure_classes.append(failure_class)
            seen.add(failure_class)
    return found_explicit_classes, failure_classes


def summarize_failure_evidence_rows(
    rows: Any,
    *,
    existing_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the canonical metadata summary for failure evidence rows.

    Boundary:
        This helper summarizes already-structured evidence. It does not parse
        prose diagnostics and intentionally ignores malformed legacy rows.

    Complexity:
        O(n) time and memory for normalizing the row list.
    """

    evidence_rows = merge_failure_evidence_rows(rows)
    summary = dict(existing_summary) if isinstance(existing_summary, Mapping) else {}
    summary["count"] = len(evidence_rows)
    summary["latest_failure_class"] = evidence_rows[-1].get("failure_class") if evidence_rows else None
    found_failure_classes, failure_classes = _explicit_failure_classes_from_rows(evidence_rows)
    if found_failure_classes:
        summary["failure_classes"] = failure_classes
    return summary


_SUSPECTED_FILE_KEYS: tuple[str, ...] = (
    "changed_files",
    "target_paths",
    "candidate_files",
    "suspected_files",
)


def suspected_files_from_failure_evidence_payload(
    failure_evidence: Mapping[str, Any] | None,
    *,
    limit: int = 20,
) -> list[str]:
    """Project suspected file paths from structured failure evidence.

    Boundary:
        This helper owns the legacy file-list projection used by runtime
        planning. It reads structured failure-evidence payload fields only; it
        does not parse compiler prose or diagnostic text.

    Complexity:
        O(n * k) time and O(n) memory, where ``n`` is the number of structured
        evidence rows and ``k`` is the fixed suspected-file key count.
    """

    if not isinstance(failure_evidence, Mapping):
        return []
    max_items = max(0, int(limit or 0))
    if max_items == 0:
        return []
    files: list[str] = []
    seen: set[str] = set()

    def append_values(value: Any) -> None:
        values: tuple[Any, ...]
        if isinstance(value, str):
            values = (value,)
        elif isinstance(value, (list, tuple)):
            values = tuple(value)
        else:
            values = ()
        for item in values:
            path = str(item or "").strip()
            if not path or path in seen:
                continue
            seen.add(path)
            files.append(path)

    def collect_from_mapping(source: Mapping[str, Any]) -> None:
        for key in _SUSPECTED_FILE_KEYS:
            append_values(source.get(key))

    collect_from_mapping(failure_evidence)
    for nested_key in ("items", "failure_evidence"):
        nested = failure_evidence.get(nested_key)
        if not isinstance(nested, (list, tuple)):
            continue
        for item in nested:
            if isinstance(item, Mapping):
                collect_from_mapping(item)
    return files[:max_items]


def append_failure_evidence_to_metadata(
    metadata: dict[str, Any],
    *new_rows: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Append structured failure evidence rows and refresh metadata summary.

    Boundary:
        This is the single Run Ledger public projection helper for the legacy
        ``failure_evidence`` / ``failure_evidence_summary`` metadata pair. It
        does not parse prose diagnostics or infer failure classes.

    Complexity:
        O(n*m) time for stable row de-duplication, matching
        :func:`merge_failure_evidence_rows`; O(n) memory for the projected rows.
    """

    rows = merge_failure_evidence_rows(metadata.get("failure_evidence"), *new_rows)
    metadata["failure_evidence"] = rows
    summary = metadata.get("failure_evidence_summary")
    metadata["failure_evidence_summary"] = summarize_failure_evidence_rows(
        rows,
        existing_summary=summary if isinstance(summary, Mapping) else None,
    )
    return rows


@dataclass(frozen=True)
class FailureEvidenceV1:
    """Structured failure evidence suitable for Run Ledger and QA projections."""

    failure_class: str
    responsible_layer: str
    reason: str
    evidence_refs: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "failure_evidence.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "failure_class": _failure_class_evidence_value(self.failure_class),
            "responsible_layer": str(self.responsible_layer or "").strip(),
            "reason": str(self.reason or "").strip(),
            "evidence_refs": [str(item).strip() for item in self.evidence_refs if str(item).strip()],
            "metadata": dict(self.metadata),
        }


__all__ = [
    "FailureClassV1",
    "FailureEvidenceV1",
    "append_failure_evidence_to_metadata",
    "is_failure_class",
    "looks_like_failure_evidence_payload",
    "merge_failure_evidence_payload",
    "merge_failure_evidence_rows",
    "normalize_failure_class",
    "summarize_failed_gate_evidence_context_slot",
    "summarize_failure_evidence_rows",
    "suspected_files_from_failure_evidence_payload",
    "task_boundary_failure_evidence_from_verdict",
]
