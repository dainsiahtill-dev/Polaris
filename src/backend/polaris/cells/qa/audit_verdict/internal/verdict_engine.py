"""Evidence-driven QA verdict engine.

This module is intentionally side-effect free. It does not claim task-market
leases, mutate tasks, or append Run Ledger events. Consumers provide evidence
and then apply the returned transition through their own public contracts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from polaris.cells.control_plane.run_ledger.public import (
    FailureClassV1,
    TaskBoundaryFailureClassV1,
    is_failure_class,
    normalize_failure_class,
    project_tool_lifecycle_failure_status,
)
from polaris.cells.control_plane.run_ledger.public.contracts import (
    ReadRunLedgerProjectionBarrierQueryV1,
    ReadRunLedgerProjectionQueryV1,
)
from polaris.cells.control_plane.run_ledger.public.service import (
    read_run_ledger_projection,
    read_run_ledger_projection_barrier,
)
from polaris.cells.qa.audit_verdict.public.contracts import (
    QaFailureClassificationV1,
    QaVerdictEnvelopeV1,
    QaVerdictLineageV1,
    build_qa_failure_classification_v1,
    build_qa_pass_classification_v1,
    normalize_qa_failure_class,
)
from polaris.kernelone.quality.artifact_quality import (
    ArtifactQualityEvidence,
    scan_workspace_artifact_quality_evidence,
)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = []
    rows: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        token = str(item or "").strip()
        if token and token not in seen:
            rows.append(token)
            seen.add(token)
    return rows


def _payload_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return _mapping(payload.get("metadata"))


def _job_token(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = _payload_metadata(payload)
    for container in (payload, metadata):
        for key in ("job_token", "control_plane_job_token", "capability_token"):
            token = container.get(key)
            if isinstance(token, dict):
                return dict(token)
    return {}


def _run_id(payload: dict[str, Any], job_token: dict[str, Any]) -> str:
    return str(job_token.get("run_id") or payload.get("run_id") or payload.get("source_run_id") or "").strip()


def _target_files(payload: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("target_files", "scope_paths", "changed_files"):
        values.extend(_string_list(payload.get(key)))
    metadata = _payload_metadata(payload)
    for key in ("target_files", "scope_paths", "changed_files"):
        values.extend(_string_list(metadata.get(key)))
    token = _job_token(payload)
    values.extend(_string_list(token.get("target_files")))
    values.extend(_string_list(token.get("allowed_paths")))
    return list(dict.fromkeys(path.replace("\\", "/").lstrip("./") for path in values if path))


def _artifact_quality_dict(value: ArtifactQualityEvidence | dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, ArtifactQualityEvidence):
        return value.to_dict()
    if isinstance(value, dict):
        return dict(value)
    return {}


def _artifact_quality_issues(value: dict[str, Any]) -> list[dict[str, Any]]:
    issues = value.get("issues")
    if not isinstance(issues, list):
        return []
    return [dict(item) for item in issues if isinstance(item, dict)]


def _artifact_quality_issue_codes(value: dict[str, Any]) -> set[str]:
    return {code for issue in _artifact_quality_issues(value) if (code := str(issue.get("code") or "").strip())}


def _artifact_quality_issue_reason(value: dict[str, Any], *, fallback: str) -> str:
    issues = _artifact_quality_issues(value)
    if not issues:
        return fallback
    first = issues[0]
    code = str(first.get("code") or "artifact_quality_issue").strip()
    path = str(first.get("path") or "").strip()
    message = str(first.get("message") or "").strip()
    location = f" in {path}" if path else ""
    if message:
        return f"Artifact quality issue {code}{location}: {message}"
    return f"Artifact quality issue {code}{location}"


_EXECUTION_EVIDENCE_MISSING_METRICS = frozenset(
    {
        "missing_director_changed_files_evidence",
    }
)


def classify_qa_audit_failure(
    audit_result: dict[str, Any],
) -> tuple[str, str]:
    """Classify local QA observations before they are committed as evidence.

    Execution-receipt gaps are control-plane failures even when the local
    scanner represents them as ordinary error findings. Exact metric keys are
    used deliberately so implementation findings cannot be reclassified by
    incidental message text.

    Returns:
        A ``(failure_class, responsible_layer)`` pair. The failure class is
        empty when the local observation passed.
    """

    metrics = _mapping(audit_result.get("metrics"))
    if any(metrics.get(key) is True for key in _EXECUTION_EVIDENCE_MISSING_METRICS):
        return (
            FailureClassV1.EXECUTION_EVIDENCE_MISSING.value,
            "execution_control_plane",
        )

    explicit_failure_class = str(audit_result.get("failure_class") or "").strip()
    if explicit_failure_class:
        return (
            normalize_qa_failure_class(explicit_failure_class),
            str(audit_result.get("responsible_layer") or "qa").strip() or "qa",
        )

    verdict = str(audit_result.get("verdict") or "").strip().upper()
    if verdict in {"REQUEUE_DESIGN", "RETRY_DESIGN"}:
        return FailureClassV1.BLUEPRINT_SCOPE_MISMATCH.value, "chief_engineer"
    if verdict in {"REQUEUE_QA", "RETRY_QA"}:
        return FailureClassV1.TEST_ENVIRONMENT_FAILURE.value, "qa_infra"
    if verdict in {"NEEDS_REVIEW", "WAITING_HUMAN", "HITL"}:
        return FailureClassV1.CONTRACT_AMBIGUOUS.value, "pm"
    if verdict != "PASS":
        return FailureClassV1.IMPLEMENTATION_DEFECT.value, "director"
    return "", "qa"


class QaVerdictConflictV1(StrEnum):
    """Canonical evidence conflicts that must block a QA verdict."""

    MISSING_RUN_ID = "missing_run_id"
    LEDGER_UNAVAILABLE = "ledger_projection_unavailable"
    LEDGER_SOURCE_INVALID = "ledger_projection_source_invalid"
    LEDGER_RUN_SCOPE_MISMATCH = "ledger_projection_run_scope_mismatch"
    BARRIER_MISSING = "projection_barrier_missing"
    BARRIER_UNSATISFIED = "projection_barrier_unsatisfied"
    BARRIER_SCOPE_MISMATCH = "projection_barrier_scope_mismatch"
    TASK_BOUNDARY_MISSING = "task_boundary_missing"
    TASK_BOUNDARY_NON_AUTHORITATIVE = "task_boundary_non_authoritative"


@dataclass(frozen=True)
class QaVerdictConflictRuleV1:
    """One deterministic conflict-resolution rule.

    The tuple order is the precedence order. This makes combinations such as a
    local PASS plus an unavailable ledger deterministic and inspectable.
    """

    conflict: QaVerdictConflictV1
    reason: str
    responsible_layer: str = "execution_control_plane"


QA_VERDICT_CONFLICT_MATRIX_V1: tuple[QaVerdictConflictRuleV1, ...] = (
    QaVerdictConflictRuleV1(
        QaVerdictConflictV1.MISSING_RUN_ID,
        "QA verdict requires an explicit run_id bound to the canonical Run Ledger",
    ),
    QaVerdictConflictRuleV1(
        QaVerdictConflictV1.LEDGER_UNAVAILABLE,
        "Canonical Run Ledger projection is missing or unavailable",
    ),
    QaVerdictConflictRuleV1(
        QaVerdictConflictV1.LEDGER_SOURCE_INVALID,
        "QA received a ledger projection from a non-canonical source",
    ),
    QaVerdictConflictRuleV1(
        QaVerdictConflictV1.LEDGER_RUN_SCOPE_MISMATCH,
        "Canonical Run Ledger projection is not bound to the requested run_id",
    ),
    QaVerdictConflictRuleV1(
        QaVerdictConflictV1.BARRIER_MISSING,
        "QA requires a projection barrier for the referenced ledger effect",
    ),
    QaVerdictConflictRuleV1(
        QaVerdictConflictV1.BARRIER_UNSATISFIED,
        "Run Ledger projection barrier was not satisfied before QA verdict",
    ),
    QaVerdictConflictRuleV1(
        QaVerdictConflictV1.BARRIER_SCOPE_MISMATCH,
        "Run Ledger projection barrier is not bound to the requested run_id",
    ),
    QaVerdictConflictRuleV1(
        QaVerdictConflictV1.TASK_BOUNDARY_MISSING,
        "Canonical Run Ledger projection has no TaskBoundary verdict for this task",
    ),
    QaVerdictConflictRuleV1(
        QaVerdictConflictV1.TASK_BOUNDARY_NON_AUTHORITATIVE,
        "TaskBoundary has no authoritative terminal verdict for this task and run",
    ),
)

_CONFLICT_RULE_BY_CODE = {rule.conflict: rule for rule in QA_VERDICT_CONFLICT_MATRIX_V1}
_TASK_BOUNDARY_SCHEMA = "polaris.task_boundary_verdict.v1"
_TASK_BOUNDARY_TERMINAL_STATUSES = frozenset(
    {
        "artifact_semantic_mismatch",
        "completed_verified",
        "deferred_followup_required",
        "dependency_not_unlocked",
        "execution_evidence_missing",
        "incomplete_materialization",
        "missing_entrypoint_target",
        "no_materialized_effect",
        "required_evidence_failed",
        "required_tool_text_fallback_not_dispatched",
        "required_verifier_failed",
        "required_verifier_missing",
        "tool_dispatch_dropped",
        "unresolved_local_import",
    }
)


def _task_boundary_for_task(ledger: dict[str, Any], task_id: str) -> dict[str, Any]:
    task_boundary = _mapping(ledger.get("task_boundary"))
    latest_by_task = _mapping(task_boundary.get("latest_by_task"))
    by_task = latest_by_task.get(task_id)
    if isinstance(by_task, dict):
        return dict(by_task)

    latest = _mapping(task_boundary.get("latest"))
    if str(latest.get("task_id") or "").strip() == task_id:
        return latest

    failed = task_boundary.get("failed")
    if isinstance(failed, list):
        for item in reversed(failed):
            if isinstance(item, dict) and str(item.get("task_id") or "").strip() == task_id:
                return dict(item)
    return {}


def _task_boundary_is_authoritative(
    boundary: dict[str, Any],
    *,
    task_id: str,
    run_id: str,
) -> bool:
    if str(boundary.get("schema_version") or "").strip() != _TASK_BOUNDARY_SCHEMA:
        return False
    if str(boundary.get("task_id") or "").strip() != task_id:
        return False
    if str(boundary.get("run_id") or "").strip() != run_id:
        return False
    status = str(boundary.get("status") or "").strip()
    if status not in _TASK_BOUNDARY_TERMINAL_STATUSES:
        return False
    ok = boundary.get("ok")
    failure_class = str(boundary.get("failure_class") or "").strip().upper()
    if not isinstance(ok, bool) or not failure_class:
        return False
    if status == "completed_verified":
        return ok is True and failure_class == TaskBoundaryFailureClassV1.PASSED.value
    return ok is False and failure_class != TaskBoundaryFailureClassV1.PASSED.value


def _canonical_evidence_conflicts(
    *,
    task_id: str,
    run_id: str,
    payload: dict[str, Any],
    ledger: dict[str, Any],
    barrier: dict[str, Any],
) -> tuple[tuple[QaVerdictConflictV1, ...], dict[str, Any]]:
    conflicts: set[QaVerdictConflictV1] = set()
    if not run_id:
        conflicts.add(QaVerdictConflictV1.MISSING_RUN_ID)

    if not ledger or ledger.get("available") is not True:
        conflicts.add(QaVerdictConflictV1.LEDGER_UNAVAILABLE)
    if ledger and (
        str(ledger.get("source") or "").strip() != "run_ledger_projection" or ledger.get("schema_version") != 1
    ):
        conflicts.add(QaVerdictConflictV1.LEDGER_SOURCE_INVALID)
    consumed_run_ids = set(_string_list(ledger.get("consumed_run_ids")))
    if run_id and run_id not in consumed_run_ids:
        conflicts.add(QaVerdictConflictV1.LEDGER_RUN_SCOPE_MISMATCH)

    requires_barrier = bool(
        str(payload.get("last_effect_append_id") or payload.get("min_append_id") or "").strip()
        or str(
            payload.get("last_effect_receipt_hash")
            or payload.get("last_effect_event_hash")
            or payload.get("min_event_hash")
            or ""
        ).strip()
    )
    if requires_barrier and not barrier:
        conflicts.add(QaVerdictConflictV1.BARRIER_MISSING)
    if barrier:
        requested_append_id = str(payload.get("last_effect_append_id") or payload.get("min_append_id") or "").strip()
        requested_event_hash = str(
            payload.get("last_effect_receipt_hash")
            or payload.get("last_effect_event_hash")
            or payload.get("min_event_hash")
            or ""
        ).strip()
        consumed_append_ids = set(_string_list(barrier.get("consumed_append_ids")))
        consumed_event_hashes = set(_string_list(barrier.get("consumed_event_hashes")))
        barrier_claim_is_complete = (
            barrier.get("barrier_satisfied") is True
            and (not requested_append_id or requested_append_id in consumed_append_ids)
            and (not requested_event_hash or requested_event_hash in consumed_event_hashes)
        )
        if not barrier_claim_is_complete:
            conflicts.add(QaVerdictConflictV1.BARRIER_UNSATISFIED)
        if str(barrier.get("schema_version") or "").strip() != "run_ledger.projection_barrier.v1" or (
            run_id and str(barrier.get("run_id") or "").strip() != run_id
        ):
            conflicts.add(QaVerdictConflictV1.BARRIER_SCOPE_MISMATCH)

    boundary = _task_boundary_for_task(ledger, task_id)
    if not boundary:
        conflicts.add(QaVerdictConflictV1.TASK_BOUNDARY_MISSING)
    elif not _task_boundary_is_authoritative(boundary, task_id=task_id, run_id=run_id):
        conflicts.add(QaVerdictConflictV1.TASK_BOUNDARY_NON_AUTHORITATIVE)

    ordered = tuple(rule.conflict for rule in QA_VERDICT_CONFLICT_MATRIX_V1 if rule.conflict in conflicts)
    return ordered, boundary


def _blocked_conflict_classification(
    conflicts: tuple[QaVerdictConflictV1, ...],
    *,
    evidence_refs: tuple[str, ...],
) -> QaFailureClassificationV1:
    primary = conflicts[0]
    rule = _CONFLICT_RULE_BY_CODE[primary]
    detail = ", ".join(conflict.value for conflict in conflicts)
    return build_qa_failure_classification_v1(
        failure_class=FailureClassV1.LEDGER_PROJECTION_INCOMPLETE.value,
        route="pending_qa",
        reason=f"{rule.reason} (conflicts: {detail})",
        repairable_by_director=False,
        owner="qa_infra",
        responsible_layer=rule.responsible_layer,
        evidence_refs=evidence_refs,
    )


def _execution_evidence_missing_outcome(
    *,
    reason: str,
    evidence_refs: tuple[str, ...],
) -> tuple[str, bool, str, str, QaFailureClassificationV1]:
    """Build the canonical non-Director route for missing execution evidence."""

    classification = build_qa_failure_classification_v1(
        failure_class=FailureClassV1.EXECUTION_EVIDENCE_MISSING.value,
        route="pending_qa",
        reason=reason,
        repairable_by_director=False,
        severity="high",
        owner="execution_control_plane",
        responsible_layer="execution_control_plane",
        evidence_refs=evidence_refs,
    )
    return "BLOCKED", False, "pending_qa", "", classification


def _route_committed_qa_failure(
    *,
    failure_class: str,
    reason: str,
    responsible_layer: str,
    evidence_refs: tuple[str, ...],
) -> tuple[str, bool, str, str, QaFailureClassificationV1]:
    """Route a typed QA finding only after its ledger barrier is satisfied."""

    normalized = normalize_qa_failure_class(failure_class)
    layer = responsible_layer or "qa"
    if normalized == FailureClassV1.EXECUTION_EVIDENCE_MISSING.value:
        return _execution_evidence_missing_outcome(
            reason=reason,
            evidence_refs=evidence_refs,
        )
    if normalized in {
        FailureClassV1.INCOMPLETE_MATERIALIZATION.value,
        FailureClassV1.NO_MATERIALIZED_EFFECT.value,
        FailureClassV1.COMPILER_OR_TEST_FAILURE.value,
        FailureClassV1.IMPLEMENTATION_DEFECT.value,
        FailureClassV1.DEFERRED_FOLLOWUP_REQUIRED.value,
    }:
        classification = build_qa_failure_classification_v1(
            failure_class=normalized,
            route="pending_exec",
            reason=reason,
            repairable_by_director=True,
            owner="director",
            responsible_layer=layer,
            evidence_refs=evidence_refs,
        )
        return "FAIL", False, "pending_exec", "", classification
    if normalized in {
        FailureClassV1.MISSING_ENTRYPOINT_TARGET.value,
        FailureClassV1.BLUEPRINT_SCOPE_MISMATCH.value,
        FailureClassV1.BLUEPRINT_VERIFY_INVALID.value,
    }:
        classification = build_qa_failure_classification_v1(
            failure_class=normalized,
            route="pending_design",
            reason=reason,
            repairable_by_director=False,
            requires_ce_replan=True,
            owner="chief_engineer",
            responsible_layer=layer,
            evidence_refs=evidence_refs,
        )
        return "FAIL", False, "pending_design", "", classification
    if normalized in {
        FailureClassV1.CONTRACT_AMBIGUOUS.value,
        FailureClassV1.ACCEPTANCE_INVALID.value,
    }:
        classification = build_qa_failure_classification_v1(
            failure_class=normalized,
            route="waiting_human",
            reason=reason,
            repairable_by_director=False,
            requires_pm_revision=True,
            owner="pm",
            responsible_layer=layer,
            evidence_refs=evidence_refs,
        )
        return "NEEDS_REVIEW", False, "waiting_human", "", classification
    if normalized == FailureClassV1.TEST_ENVIRONMENT_FAILURE.value:
        classification = build_qa_failure_classification_v1(
            failure_class=normalized,
            route="pending_qa",
            reason=reason,
            repairable_by_director=False,
            owner="qa_infra",
            responsible_layer=layer,
            evidence_refs=evidence_refs,
        )
        return "BLOCKED", False, "pending_qa", "", classification
    classification = build_qa_failure_classification_v1(
        failure_class=normalized,
        route="waiting_human",
        reason=reason,
        repairable_by_director=False,
        severity="critical",
        owner="platform",
        responsible_layer=layer,
        evidence_refs=evidence_refs,
    )
    return "BLOCKED", False, "waiting_human", "", classification


def _route_classification(
    *,
    task_id: str,
    run_id: str,
    payload: dict[str, Any],
    gate_name: str,
    gate_summary: str,
    audit_result: dict[str, Any],
    ledger: dict[str, Any],
    barrier: dict[str, Any],
    artifact_quality: dict[str, Any],
    evidence_refs: tuple[str, ...],
) -> tuple[str, bool, str, str, QaFailureClassificationV1]:
    """Return verdict, ok, next_stage, terminal_status, classification."""

    conflicts, boundary = _canonical_evidence_conflicts(
        task_id=task_id,
        run_id=run_id,
        payload=payload,
        ledger=ledger,
        barrier=barrier,
    )
    if conflicts:
        return (
            "BLOCKED",
            False,
            "pending_qa",
            "",
            _blocked_conflict_classification(
                conflicts,
                evidence_refs=evidence_refs,
            ),
        )

    evidence_policy = _mapping(ledger.get("evidence_policy"))
    missing_required = _string_list(evidence_policy.get("missing_required_modalities"))
    failed_required = _string_list(evidence_policy.get("failed_required_modalities"))
    lifecycle_failure = project_tool_lifecycle_failure_status(_mapping(ledger.get("tool_lifecycle")))
    lifecycle_failure_class = normalize_failure_class(
        lifecycle_failure.get("failure_class"),
        default=FailureClassV1.TOOL_LIFECYCLE_FAILED,
    )
    if is_failure_class(lifecycle_failure_class, FailureClassV1.TOOL_DISPATCH_DROPPED):
        classification = build_qa_failure_classification_v1(
            failure_class=FailureClassV1.TOOL_DISPATCH_DROPPED.value,
            route="waiting_human",
            reason=str(
                lifecycle_failure.get("reason")
                or "LLM emitted tool calls but the execution control plane did not commit dispatch receipts"
            ),
            repairable_by_director=False,
            severity="critical",
            owner="platform",
            responsible_layer="execution_control_plane",
            evidence_refs=evidence_refs,
        )
        return "BLOCKED", False, "waiting_human", "", classification
    if bool(lifecycle_failure.get("failed")):
        reason = str(lifecycle_failure.get("reason") or "Tool lifecycle receipt failed").strip()
        classification = build_qa_failure_classification_v1(
            failure_class=lifecycle_failure_class,
            route="waiting_human",
            reason=reason,
            repairable_by_director=False,
            severity="critical",
            owner="platform",
            responsible_layer="execution_control_plane",
            evidence_refs=evidence_refs,
        )
        return "BLOCKED", False, "waiting_human", "", classification
    if boundary and not bool(boundary.get("ok", True)):
        boundary_failure_class = normalize_qa_failure_class(
            str(boundary.get("failure_class") or TaskBoundaryFailureClassV1.TASK_BOUNDARY_FAILED.value).strip()
        )
        boundary_reason = str(boundary.get("reason") or "Task boundary verdict failed").strip()
        responsible_layer = str(boundary.get("responsible_layer") or "execution_control_plane").strip()
        if boundary_failure_class == TaskBoundaryFailureClassV1.INCOMPLETE_MATERIALIZATION.value:
            classification = build_qa_failure_classification_v1(
                failure_class=FailureClassV1.INCOMPLETE_MATERIALIZATION.value,
                route="pending_exec",
                reason=boundary_reason,
                repairable_by_director=True,
                owner="director",
                responsible_layer=responsible_layer or "director",
                evidence_refs=evidence_refs,
            )
            return "FAIL", False, "pending_exec", "", classification
        if boundary_failure_class == TaskBoundaryFailureClassV1.DEFERRED_FOLLOWUP_REQUIRED.value:
            classification = build_qa_failure_classification_v1(
                failure_class=FailureClassV1.DEFERRED_FOLLOWUP_REQUIRED.value,
                route="pending_exec",
                reason=boundary_reason,
                repairable_by_director=True,
                severity="medium",
                owner="director",
                responsible_layer=responsible_layer,
                evidence_refs=evidence_refs,
            )
            return "FAIL", False, "pending_exec", "", classification
        if boundary_failure_class == TaskBoundaryFailureClassV1.EXECUTION_EVIDENCE_MISSING.value:
            return _execution_evidence_missing_outcome(
                reason=boundary_reason,
                evidence_refs=evidence_refs,
            )
        if boundary_failure_class == TaskBoundaryFailureClassV1.REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED.value:
            classification = build_qa_failure_classification_v1(
                failure_class=FailureClassV1.REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED.value,
                route="waiting_human",
                reason=boundary_reason,
                repairable_by_director=False,
                severity="critical",
                owner="platform",
                responsible_layer="execution_control_plane",
                evidence_refs=evidence_refs,
            )
            return "BLOCKED", False, "waiting_human", "", classification
        if boundary_failure_class == TaskBoundaryFailureClassV1.NO_MATERIALIZED_EFFECT.value:
            classification = build_qa_failure_classification_v1(
                failure_class=FailureClassV1.NO_MATERIALIZED_EFFECT.value,
                route="pending_exec",
                reason=boundary_reason,
                repairable_by_director=True,
                severity="high",
                owner="director",
                responsible_layer=responsible_layer or "execution_control_plane",
                evidence_refs=evidence_refs,
            )
            return "FAIL", False, "pending_exec", "", classification
        if boundary_failure_class == TaskBoundaryFailureClassV1.COMPILER_OR_TEST_FAILURE.value:
            classification = build_qa_failure_classification_v1(
                failure_class=FailureClassV1.COMPILER_OR_TEST_FAILURE.value,
                route="pending_exec",
                reason=boundary_reason,
                repairable_by_director=True,
                owner="director",
                responsible_layer=responsible_layer or "director",
                evidence_refs=evidence_refs,
            )
            return "FAIL", False, "pending_exec", "", classification
        if boundary_failure_class == TaskBoundaryFailureClassV1.IMPLEMENTATION_DEFECT.value:
            classification = build_qa_failure_classification_v1(
                failure_class=FailureClassV1.IMPLEMENTATION_DEFECT.value,
                route="pending_exec",
                reason=boundary_reason,
                repairable_by_director=True,
                owner="director",
                responsible_layer=responsible_layer or "director",
                evidence_refs=evidence_refs,
            )
            return "FAIL", False, "pending_exec", "", classification
        if boundary_failure_class == TaskBoundaryFailureClassV1.DEPENDENCY_NOT_UNLOCKED.value:
            classification = build_qa_failure_classification_v1(
                failure_class=FailureClassV1.DEPENDENCY_NOT_UNLOCKED.value,
                route="pending_exec",
                reason=boundary_reason,
                repairable_by_director=False,
                severity="medium",
                owner="execution_control_plane",
                responsible_layer=responsible_layer or "execution_control_plane",
                evidence_refs=evidence_refs,
            )
            return "BLOCKED", False, "pending_exec", "", classification
        if boundary_failure_class == TaskBoundaryFailureClassV1.MISSING_ENTRYPOINT_TARGET.value:
            classification = build_qa_failure_classification_v1(
                failure_class=FailureClassV1.MISSING_ENTRYPOINT_TARGET.value,
                route="pending_design",
                reason=boundary_reason,
                repairable_by_director=False,
                requires_ce_replan=True,
                owner="chief_engineer",
                responsible_layer=responsible_layer or "task_boundary",
                evidence_refs=evidence_refs,
            )
            return "FAIL", False, "pending_design", "", classification
        if is_failure_class(boundary_failure_class, FailureClassV1.TOOL_DISPATCH_DROPPED):
            classification = build_qa_failure_classification_v1(
                failure_class=FailureClassV1.TOOL_DISPATCH_DROPPED.value,
                route="waiting_human",
                reason=boundary_reason,
                repairable_by_director=False,
                severity="critical",
                owner="platform",
                responsible_layer=responsible_layer or "execution_control_plane",
                evidence_refs=evidence_refs,
            )
            return "BLOCKED", False, "waiting_human", "", classification
        if boundary_failure_class == TaskBoundaryFailureClassV1.TASKBOARD_DEADLOCK.value:
            classification = build_qa_failure_classification_v1(
                failure_class=FailureClassV1.TASKBOARD_DEADLOCK.value,
                route="waiting_human",
                reason=boundary_reason,
                repairable_by_director=False,
                severity="critical",
                owner="platform",
                responsible_layer=responsible_layer or "execution_control_plane",
                evidence_refs=evidence_refs,
            )
            return "BLOCKED", False, "waiting_human", "", classification
        classification = build_qa_failure_classification_v1(
            failure_class=boundary_failure_class,
            route="waiting_human",
            reason=boundary_reason,
            repairable_by_director=False,
            owner="platform",
            responsible_layer=responsible_layer,
            evidence_refs=evidence_refs,
        )
        return "BLOCKED", False, "waiting_human", "", classification
    if (
        str(boundary.get("status") or "").strip() != "completed_verified"
        or normalize_qa_failure_class(
            str(boundary.get("failure_class") or TaskBoundaryFailureClassV1.PASSED.value)
        )
        != TaskBoundaryFailureClassV1.PASSED.value
    ):
        classification = build_qa_failure_classification_v1(
            failure_class=FailureClassV1.LEDGER_PROJECTION_INCOMPLETE.value,
            route="pending_qa",
            reason="QA success requires TaskBoundary status=completed_verified with failure_class=PASSED",
            repairable_by_director=False,
            severity="critical",
            owner="control_plane",
            responsible_layer="execution_control_plane",
            evidence_refs=evidence_refs,
        )
        return "BLOCKED", False, "pending_qa", "", classification
    if artifact_quality.get("contract_amendment_request"):
        classification = build_qa_failure_classification_v1(
            failure_class=FailureClassV1.BLUEPRINT_SCOPE_MISMATCH.value,
            route="pending_design",
            reason="Artifact quality requires a CE interface contract amendment",
            repairable_by_director=False,
            requires_ce_replan=True,
            owner="chief_engineer",
            responsible_layer="chief_engineer",
            evidence_refs=evidence_refs,
        )
        return "FAIL", False, "pending_design", "", classification
    if missing_required:
        return _execution_evidence_missing_outcome(
            reason="Missing required QA evidence modalities: " + ", ".join(missing_required),
            evidence_refs=evidence_refs,
        )
    if failed_required:
        classification = build_qa_failure_classification_v1(
            failure_class=FailureClassV1.COMPILER_OR_TEST_FAILURE.value,
            route="pending_exec",
            reason="Required QA evidence modalities failed: " + ", ".join(failed_required),
            repairable_by_director=True,
            owner="director",
            responsible_layer="director",
            evidence_refs=evidence_refs,
        )
        return "FAIL", False, "pending_exec", "", classification
    observed_failure_class, observed_responsible_layer = classify_qa_audit_failure(audit_result)
    if observed_failure_class == FailureClassV1.EXECUTION_EVIDENCE_MISSING.value:
        return _execution_evidence_missing_outcome(
            reason=gate_summary or str(audit_result.get("summary") or "Required execution evidence is missing").strip(),
            evidence_refs=evidence_refs,
        )
    explicit_failure_class = str(audit_result.get("failure_class") or "").strip()
    if explicit_failure_class:
        return _route_committed_qa_failure(
            failure_class=observed_failure_class or explicit_failure_class,
            reason=gate_summary or str(audit_result.get("summary") or "QA evidence rejected").strip(),
            responsible_layer=observed_responsible_layer,
            evidence_refs=evidence_refs,
        )
    verdict = str(audit_result.get("verdict") or "").strip().upper()
    if verdict in {"REQUEUE_EXEC", "RETRY_EXEC"}:
        classification = build_qa_failure_classification_v1(
            failure_class=FailureClassV1.IMPLEMENTATION_DEFECT.value,
            route="pending_exec",
            reason=gate_summary or "QA requested Director execution repair",
            repairable_by_director=True,
            owner="director",
            responsible_layer="director",
            evidence_refs=evidence_refs,
        )
        return "FAIL", False, "pending_exec", "", classification
    if verdict in {"REQUEUE_DESIGN", "RETRY_DESIGN"}:
        classification = build_qa_failure_classification_v1(
            failure_class=FailureClassV1.BLUEPRINT_SCOPE_MISMATCH.value,
            route="pending_design",
            reason=gate_summary or "QA requested Chief Engineer design repair",
            repairable_by_director=False,
            requires_ce_replan=True,
            owner="chief_engineer",
            responsible_layer="chief_engineer",
            evidence_refs=evidence_refs,
        )
        return "FAIL", False, "pending_design", "", classification
    if verdict in {"REQUEUE_QA", "RETRY_QA"}:
        classification = build_qa_failure_classification_v1(
            failure_class=FailureClassV1.TEST_ENVIRONMENT_FAILURE.value,
            route="pending_qa",
            reason=gate_summary or "QA requested verification retry",
            repairable_by_director=False,
            owner="qa_infra",
            responsible_layer="qa_infra",
            evidence_refs=evidence_refs,
        )
        return "BLOCKED", False, "pending_qa", "", classification
    if audit_result.get("ok") is False and verdict not in {"FAIL", "REJECT", "REJECTED", "NEEDS_REVIEW"}:
        return _execution_evidence_missing_outcome(
            reason=gate_summary or f"QA audit result was not ok: {verdict or 'unknown'}",
            evidence_refs=evidence_refs,
        )
    artifact_issue_codes = _artifact_quality_issue_codes(artifact_quality)
    if gate_name or artifact_quality.get("errors") or artifact_issue_codes or verdict in {"FAIL", "REJECT", "REJECTED"}:
        if artifact_issue_codes:
            if "declared_target_missing" in artifact_issue_codes:
                classification = build_qa_failure_classification_v1(
                    failure_class=FailureClassV1.INCOMPLETE_MATERIALIZATION.value,
                    route="pending_exec",
                    reason=_artifact_quality_issue_reason(
                        artifact_quality,
                        fallback=gate_summary or "Artifact quality reported missing declared targets",
                    ),
                    repairable_by_director=True,
                    owner="director",
                    responsible_layer="director",
                    evidence_refs=evidence_refs,
                )
                return "FAIL", False, "pending_exec", "", classification
            classification = build_qa_failure_classification_v1(
                failure_class=FailureClassV1.IMPLEMENTATION_DEFECT.value,
                route="pending_exec",
                reason=_artifact_quality_issue_reason(
                    artifact_quality,
                    fallback=gate_summary or "Artifact quality reported implementation defects",
                ),
                repairable_by_director=True,
                owner="director",
                responsible_layer="director",
                evidence_refs=evidence_refs,
            )
            return "FAIL", False, "pending_exec", "", classification
        classification = build_qa_failure_classification_v1(
            failure_class=FailureClassV1.IMPLEMENTATION_DEFECT.value,
            route="pending_exec",
            reason=gate_summary or "QA failed with implementation defects",
            repairable_by_director=True,
            owner="director",
            responsible_layer="director",
            evidence_refs=evidence_refs,
        )
        return "FAIL", False, "pending_exec", "", classification
    if verdict == "NEEDS_REVIEW":
        classification = build_qa_failure_classification_v1(
            failure_class=FailureClassV1.CONTRACT_AMBIGUOUS.value,
            route="waiting_human",
            reason="QA review requested human judgement",
            repairable_by_director=False,
            requires_pm_revision=True,
            owner="human",
            responsible_layer="human",
            evidence_refs=evidence_refs,
        )
        return "NEEDS_REVIEW", False, "waiting_human", "", classification
    classification = build_qa_pass_classification_v1(
        reason="QA evidence accepted",
        evidence_refs=evidence_refs,
    )
    return "PASS", True, "", "resolved", classification


class QAVerdictEngine:
    """Build canonical QA verdict envelopes from already-collected evidence."""

    def __init__(self, workspace: str) -> None:
        self.workspace = str(Path(workspace).expanduser().resolve())

    def read_ledger_with_barrier(self, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        token = _job_token(payload)
        run_id = _run_id(payload, token)
        if not run_id:
            return {}, {}
        min_append_id = str(payload.get("last_effect_append_id") or payload.get("min_append_id") or "").strip()
        min_event_hash = str(
            payload.get("last_effect_receipt_hash")
            or payload.get("last_effect_event_hash")
            or payload.get("min_event_hash")
            or ""
        ).strip()
        if min_append_id or min_event_hash:
            barrier_result = read_run_ledger_projection_barrier(
                ReadRunLedgerProjectionBarrierQueryV1(
                    workspace=self.workspace,
                    run_id=run_id,
                    min_append_id=min_append_id,
                    min_event_hash=min_event_hash,
                    timeout_ms=0,
                )
            )
            return dict(barrier_result.projection), dict(barrier_result.barrier)
        projection_result = read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(workspace=self.workspace, run_id=run_id)
        )
        return dict(projection_result.projection), {}

    def scan_artifact_quality(self, payload: dict[str, Any]) -> dict[str, Any]:
        targets = _target_files(payload)
        if not targets:
            return {}
        try:
            return scan_workspace_artifact_quality_evidence(
                self.workspace,
                relative_paths=targets,
                task_id=str(payload.get("task_id") or payload.get("id") or ""),
            ).to_dict()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "ok": False,
                "errors": [f"artifact quality scan failed: {exc}"],
                "scan_error": str(exc),
            }

    def build_envelope(
        self,
        *,
        task_id: str,
        payload: dict[str, Any],
        gate_name: str = "",
        gate_summary: str = "",
        audit_result: dict[str, Any] | None = None,
        ledger_projection: dict[str, Any] | None = None,
        barrier: dict[str, Any] | None = None,
        artifact_quality: ArtifactQualityEvidence | dict[str, Any] | None = None,
        lineage: QaVerdictLineageV1 | None = None,
    ) -> QaVerdictEnvelopeV1:
        token = _job_token(payload)
        run_id = _run_id(payload, token)
        audit = dict(audit_result or {})
        ledger = dict(ledger_projection or {})
        barrier_map = dict(barrier or {})
        if not ledger and run_id:
            try:
                ledger, barrier_map = self.read_ledger_with_barrier(payload)
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                ledger = {
                    "schema_version": 1,
                    "source": "run_ledger_projection",
                    "available": False,
                    "ok": False,
                    "status": "read_error",
                    "consumed_run_ids": [run_id],
                    "detail": f"canonical Run Ledger projection read failed: {exc}",
                }
                barrier_map = {}
        artifact = _artifact_quality_dict(artifact_quality)
        if not artifact:
            artifact = self.scan_artifact_quality(payload)
        evidence_refs = tuple(
            item
            for item in (
                str(ledger.get("audit_path") or "").strip(),
                str(barrier_map.get("ledger_paths", [""])[0] if barrier_map.get("ledger_paths") else "").strip(),
            )
            if item
        )
        conflicts, boundary = _canonical_evidence_conflicts(
            task_id=task_id,
            run_id=run_id,
            payload=payload,
            ledger=ledger,
            barrier=barrier_map,
        )
        verdict, ok, next_stage, terminal_status, classification = _route_classification(
            task_id=task_id,
            run_id=run_id,
            payload=payload,
            gate_name=gate_name,
            gate_summary=gate_summary,
            audit_result=audit,
            ledger=ledger,
            barrier=barrier_map,
            artifact_quality=artifact,
            evidence_refs=evidence_refs,
        )
        findings = _string_list(audit.get("findings"))
        if gate_summary:
            findings.insert(0, gate_summary)
        authority = {
            "job_token_id": str(token.get("token_id") or ""),
            "contract_hash": str(token.get("contract_hash") or payload.get("contract_hash") or ""),
            "blueprint_hash": str(token.get("blueprint_hash") or payload.get("blueprint_hash") or ""),
            "capability_ok": bool(_mapping(token.get("capability_audit")).get("ok", bool(token))),
            "target_files": _target_files(payload),
            "allowed_paths": _string_list(token.get("allowed_paths")),
        }
        evidence_policy = _mapping(ledger.get("evidence_policy"))
        evidence = {
            "evidence_refs": list(evidence_refs),
            "required_modalities": _string_list(evidence_policy.get("required_modalities")),
            "missing_required_modalities": _string_list(evidence_policy.get("missing_required_modalities")),
            "failed_required_modalities": _string_list(evidence_policy.get("failed_required_modalities")),
            "barrier": barrier_map,
            "conflict_matrix": {
                "schema_version": "qa.verdict_conflict_matrix.v1",
                "conflicts": [conflict.value for conflict in conflicts],
                "selected_task_boundary": boundary,
            },
        }
        content_without_hash = {
            "workspace": self.workspace,
            "run_id": run_id,
            "task_id": task_id,
            "verdict": verdict,
            "ok": ok,
            "next_stage": next_stage,
            "terminal_status": terminal_status,
            "authority": authority,
            "ledger": ledger,
            "evidence": evidence,
            "artifact_quality": artifact,
            "classification": classification.to_dict(),
            "findings": findings,
            "metrics": _mapping(audit.get("metrics")),
        }
        return QaVerdictEnvelopeV1(
            workspace=self.workspace,
            run_id=run_id,
            task_id=task_id,
            verdict=verdict,
            ok=ok,
            next_stage=next_stage,
            terminal_status=terminal_status,
            authority=authority,
            ledger=ledger,
            evidence=evidence,
            artifact_quality=artifact,
            classification=classification,
            lineage=lineage or QaVerdictLineageV1(),
            findings=tuple(findings),
            metrics=_mapping(audit.get("metrics")),
            evidence_refs=evidence_refs,
            content_hash=_stable_hash(content_without_hash),
        )


def diff_verdicts(
    *,
    fallback_verdict: str,
    fallback_next_stage: str = "",
    fallback_terminal_status: str = "",
    engine_envelope: QaVerdictEnvelopeV1,
) -> dict[str, Any]:
    """Compare consumer fallback routing against the verdict engine."""

    engine = engine_envelope.to_dict()
    mismatches: list[str] = []
    if str(fallback_verdict or "").strip().upper() != str(engine.get("verdict") or "").strip().upper():
        mismatches.append("verdict")
    if str(fallback_next_stage or "").strip() != str(engine.get("next_stage") or "").strip():
        mismatches.append("next_stage")
    if str(fallback_terminal_status or "").strip() != str(engine.get("terminal_status") or "").strip():
        mismatches.append("terminal_status")
    return {
        "schema_version": "qa.verdict_diff.v1",
        "authoritative": False,
        "mismatch": bool(mismatches),
        "mismatches": mismatches,
        "fallback": {
            "verdict": fallback_verdict,
            "next_stage": fallback_next_stage,
            "terminal_status": fallback_terminal_status,
        },
        "engine": {
            "verdict": engine.get("verdict"),
            "next_stage": engine.get("next_stage"),
            "terminal_status": engine.get("terminal_status"),
            "classification": engine.get("classification"),
            "content_hash": engine.get("content_hash"),
        },
    }


__all__ = ["QAVerdictEngine", "diff_verdicts"]
