"""Evidence-driven QA verdict engine.

This module is intentionally side-effect free. It does not claim task-market
leases, mutate tasks, or append Run Ledger events. Consumers provide evidence
and then apply the returned transition through their own public contracts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from polaris.cells.control_plane.run_ledger.public import (
    FailureClassV1,
    is_failure_class,
    normalize_failure_class,
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


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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
    return {
        code
        for issue in _artifact_quality_issues(value)
        if (code := str(issue.get("code") or "").strip())
    }


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


def _failure_text(*values: Any) -> str:
    return "\n".join(str(value or "") for value in values).lower()


def _route_classification(
    *,
    gate_name: str,
    gate_summary: str,
    audit_result: dict[str, Any],
    ledger: dict[str, Any],
    barrier: dict[str, Any],
    artifact_quality: dict[str, Any],
    evidence_refs: tuple[str, ...],
) -> tuple[str, bool, str, str, QaFailureClassificationV1]:
    """Return verdict, ok, next_stage, terminal_status, classification."""

    text = _failure_text(gate_name, gate_summary, audit_result, artifact_quality)
    evidence_policy = _mapping(ledger.get("evidence_policy"))
    missing_required = _string_list(evidence_policy.get("missing_required_modalities"))
    failed_required = _string_list(evidence_policy.get("failed_required_modalities"))
    if barrier and not bool(barrier.get("barrier_satisfied", True)):
        classification = build_qa_failure_classification_v1(
            failure_class="TEST_ENVIRONMENT_FAILURE",
            route="pending_qa",
            reason="Run Ledger projection barrier was not satisfied before QA verdict",
            repairable_by_director=False,
            owner="qa_infra",
            responsible_layer="qa_infra",
            evidence_refs=evidence_refs,
        )
        return "BLOCKED", False, "pending_qa", "", classification
    tool_lifecycle = _mapping(ledger.get("tool_lifecycle"))
    if tool_lifecycle and _int_value(tool_lifecycle.get("dropped_count")) > 0:
        classification = build_qa_failure_classification_v1(
            failure_class=FailureClassV1.TOOL_DISPATCH_DROPPED.value,
            route="waiting_human",
            reason="LLM emitted tool calls but the execution control plane did not commit dispatch receipts",
            repairable_by_director=False,
            severity="critical",
            owner="platform",
            responsible_layer="execution_control_plane",
            evidence_refs=evidence_refs,
        )
        return "BLOCKED", False, "waiting_human", "", classification
    if tool_lifecycle and _int_value(tool_lifecycle.get("failed_count")) > 0:
        events = tool_lifecycle.get("events")
        event_rows = events if isinstance(events, list) else []
        failed_events = [item for item in event_rows if isinstance(item, dict) and bool(item.get("failed"))]
        latest_failure = failed_events[-1] if failed_events else {}
        failure_class = normalize_failure_class(
            latest_failure.get("failure_class"),
            default=FailureClassV1.TOOL_LIFECYCLE_FAILED,
        )
        reason = str(latest_failure.get("reason") or "Tool lifecycle receipt failed").strip()
        classification = build_qa_failure_classification_v1(
            failure_class=failure_class,
            route="waiting_human",
            reason=reason,
            repairable_by_director=False,
            severity="critical",
            owner="platform",
            responsible_layer="execution_control_plane",
            evidence_refs=evidence_refs,
        )
        return "BLOCKED", False, "waiting_human", "", classification
    task_boundary = _mapping(ledger.get("task_boundary"))
    latest_boundary = _mapping(task_boundary.get("latest"))
    if latest_boundary and not bool(latest_boundary.get("ok", True)):
        boundary_failure_class = str(latest_boundary.get("failure_class") or "TASK_BOUNDARY_FAILED").strip()
        boundary_reason = str(latest_boundary.get("reason") or "Task boundary verdict failed").strip()
        responsible_layer = str(latest_boundary.get("responsible_layer") or "execution_control_plane").strip()
        if boundary_failure_class == "INCOMPLETE_MATERIALIZATION":
            classification = build_qa_failure_classification_v1(
                failure_class="INCOMPLETE_MATERIALIZATION",
                route="pending_exec",
                reason=boundary_reason,
                repairable_by_director=True,
                owner="director",
                responsible_layer=responsible_layer or "director",
                evidence_refs=evidence_refs,
            )
            return "FAIL", False, "pending_exec", "", classification
        if boundary_failure_class == "DEFERRED_FOLLOWUP_REQUIRED":
            classification = build_qa_failure_classification_v1(
                failure_class="DEFERRED_FOLLOWUP_REQUIRED",
                route="pending_exec",
                reason=boundary_reason,
                repairable_by_director=True,
                severity="medium",
                owner="director",
                responsible_layer=responsible_layer,
                evidence_refs=evidence_refs,
            )
            return "FAIL", False, "pending_exec", "", classification
        if boundary_failure_class == "EXECUTION_EVIDENCE_MISSING":
            classification = build_qa_failure_classification_v1(
                failure_class="EXECUTION_EVIDENCE_MISSING",
                route="pending_exec",
                reason=boundary_reason,
                repairable_by_director=True,
                owner="director",
                responsible_layer=responsible_layer or "director",
                evidence_refs=evidence_refs,
            )
            return "FAIL", False, "pending_exec", "", classification
        if boundary_failure_class == "IMPLEMENTATION_DEFECT":
            classification = build_qa_failure_classification_v1(
                failure_class="IMPLEMENTATION_DEFECT",
                route="pending_exec",
                reason=boundary_reason,
                repairable_by_director=True,
                owner="director",
                responsible_layer=responsible_layer or "director",
                evidence_refs=evidence_refs,
            )
            return "FAIL", False, "pending_exec", "", classification
        if boundary_failure_class == "DEPENDENCY_NOT_UNLOCKED":
            classification = build_qa_failure_classification_v1(
                failure_class="DEPENDENCY_NOT_UNLOCKED",
                route="pending_exec",
                reason=boundary_reason,
                repairable_by_director=False,
                severity="medium",
                owner="execution_control_plane",
                responsible_layer=responsible_layer or "execution_control_plane",
                evidence_refs=evidence_refs,
            )
            return "BLOCKED", False, "pending_exec", "", classification
        if boundary_failure_class == "MISSING_ENTRYPOINT_TARGET":
            classification = build_qa_failure_classification_v1(
                failure_class="MISSING_ENTRYPOINT_TARGET",
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
        if boundary_failure_class == "TASKBOARD_DEADLOCK":
            classification = build_qa_failure_classification_v1(
                failure_class="TASKBOARD_DEADLOCK",
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
    if artifact_quality.get("contract_amendment_request"):
        classification = build_qa_failure_classification_v1(
            failure_class="BLUEPRINT_SCOPE_MISMATCH",
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
        classification = build_qa_failure_classification_v1(
            failure_class="EXECUTION_EVIDENCE_MISSING",
            route="pending_exec",
            reason="Missing required QA evidence modalities: " + ", ".join(missing_required),
            repairable_by_director=True,
            owner="director",
            responsible_layer="director",
            evidence_refs=evidence_refs,
        )
        return "FAIL", False, "pending_exec", "", classification
    if failed_required:
        classification = build_qa_failure_classification_v1(
            failure_class="IMPLEMENTATION_DEFECT",
            route="pending_exec",
            reason="Required QA evidence modalities failed: " + ", ".join(failed_required),
            repairable_by_director=True,
            owner="director",
            responsible_layer="director",
            evidence_refs=evidence_refs,
        )
        return "FAIL", False, "pending_exec", "", classification
    if "scope mismatch" in text or "outside scope" in text or "scope expansion" in text:
        classification = build_qa_failure_classification_v1(
            failure_class="BLUEPRINT_SCOPE_MISMATCH",
            route="pending_design",
            reason="QA failure indicates CE scope or blueprint mismatch",
            repairable_by_director=False,
            requires_ce_replan=True,
            owner="chief_engineer",
            responsible_layer="chief_engineer",
            evidence_refs=evidence_refs,
        )
        return "FAIL", False, "pending_design", "", classification
    if "contract ambiguous" in text or "missing acceptance" in text or "clarification" in text:
        classification = build_qa_failure_classification_v1(
            failure_class="CONTRACT_AMBIGUOUS",
            route="waiting_human",
            reason="QA failure requires PM or human contract clarification",
            repairable_by_director=False,
            requires_pm_revision=True,
            owner="pm",
            responsible_layer="pm",
            evidence_refs=evidence_refs,
        )
        return "NEEDS_REVIEW", False, "waiting_human", "", classification
    if "security policy" in text or "policy violation" in text or "path traversal" in text or "unauthorized" in text:
        classification = build_qa_failure_classification_v1(
            failure_class="SECURITY_POLICY_VIOLATION",
            route="waiting_human",
            reason="QA failure indicates security or authorization policy violation",
            repairable_by_director=False,
            severity="critical",
            owner="security",
            responsible_layer="security_policy",
            evidence_refs=evidence_refs,
        )
        return "BLOCKED", False, "waiting_human", "", classification
    verdict = str(audit_result.get("verdict") or "").strip().upper()
    if bool(audit_result.get("qa_findings_bounce_limit_reached")):
        classification = build_qa_failure_classification_v1(
            failure_class="IMPLEMENTATION_DEFECT_BOUNCE_LIMIT",
            route="rejected",
            reason="QA findings already exhausted the bounded Director feedback loop",
            repairable_by_director=False,
            severity="high",
            owner="director",
            responsible_layer="director",
            evidence_refs=evidence_refs,
        )
        return "FAIL", False, "", "rejected", classification
    if verdict in {"REQUEUE_EXEC", "RETRY_EXEC"}:
        classification = build_qa_failure_classification_v1(
            failure_class="IMPLEMENTATION_DEFECT",
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
            failure_class="BLUEPRINT_SCOPE_MISMATCH",
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
            failure_class="TEST_ENVIRONMENT_FAILURE",
            route="pending_qa",
            reason=gate_summary or "QA requested verification retry",
            repairable_by_director=False,
            owner="qa_infra",
            responsible_layer="qa_infra",
            evidence_refs=evidence_refs,
        )
        return "BLOCKED", False, "pending_qa", "", classification
    if audit_result.get("ok") is False and verdict not in {"FAIL", "REJECT", "REJECTED", "NEEDS_REVIEW"}:
        classification = build_qa_failure_classification_v1(
            failure_class="EXECUTION_EVIDENCE_MISSING",
            route="pending_exec",
            reason=gate_summary or f"QA audit result was not ok: {verdict or 'unknown'}",
            repairable_by_director=True,
            owner="director",
            responsible_layer="director",
            evidence_refs=evidence_refs,
        )
        return "FAIL", False, "pending_exec", "", classification
    artifact_issue_codes = _artifact_quality_issue_codes(artifact_quality)
    if gate_name or artifact_quality.get("errors") or artifact_issue_codes or verdict in {"FAIL", "REJECT", "REJECTED"}:
        if "step verify command rejected" in text:
            classification = build_qa_failure_classification_v1(
                failure_class="BLUEPRINT_VERIFY_INVALID",
                route="pending_design",
                reason="CE step verify command is invalid or unsafe",
                repairable_by_director=False,
                requires_ce_replan=True,
                owner="chief_engineer",
                responsible_layer="chief_engineer",
                evidence_refs=evidence_refs,
            )
            return "FAIL", False, "pending_design", "", classification
        if artifact_issue_codes:
            if "declared_target_missing" in artifact_issue_codes:
                classification = build_qa_failure_classification_v1(
                    failure_class="INCOMPLETE_MATERIALIZATION",
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
                failure_class="IMPLEMENTATION_DEFECT",
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
            failure_class="IMPLEMENTATION_DEFECT",
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
            failure_class="CONTRACT_AMBIGUOUS",
            route="waiting_human",
            reason="QA review requested human judgement",
            repairable_by_director=False,
            requires_pm_revision=True,
            owner="human",
            responsible_layer="human",
            evidence_refs=evidence_refs,
        )
        return "NEEDS_REVIEW", False, "waiting_human", "", classification
    classification = build_qa_failure_classification_v1(
        failure_class="PASSED",
        route="resolved",
        reason="QA evidence accepted",
        repairable_by_director=False,
        severity="info",
        owner="qa",
        responsible_layer="qa",
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
            result = read_run_ledger_projection_barrier(
                ReadRunLedgerProjectionBarrierQueryV1(
                    workspace=self.workspace,
                    run_id=run_id,
                    min_append_id=min_append_id,
                    min_event_hash=min_event_hash,
                    timeout_ms=0,
                )
            )
            return dict(result.projection), dict(result.barrier)
        result = read_run_ledger_projection(ReadRunLedgerProjectionQueryV1(workspace=self.workspace, run_id=run_id))
        return dict(result.projection), {}

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
            ledger, barrier_map = self.read_ledger_with_barrier(payload)
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
        verdict, ok, next_stage, terminal_status, classification = _route_classification(
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
