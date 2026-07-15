"""Canonical QA evidence and verdict commit boundaries.

Local QA scanners produce observations only.  This module is the single owner
for turning those observations into Run Ledger facts, committing the canonical
verdict, and returning the coordinates required by downstream barriers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from polaris.cells.control_plane.run_ledger.public.contracts import (
    AppendRunLedgerEventCommandV1,
)
from polaris.cells.control_plane.run_ledger.public.service import append_run_ledger_event

_FINAL_VERDICT_GATE_NAME = "qa_verdict"
_RESERVED_FINAL_GATE_NAMES = frozenset({_FINAL_VERDICT_GATE_NAME, "qa_exception"})
_CANONICAL_VERDICTS = frozenset({"PASS", "FAIL", "BLOCKED", "NEEDS_REVIEW"})
_CANONICAL_NEXT_STAGES = frozenset({"pending_design", "pending_exec", "pending_qa", "waiting_human"})
_CANONICAL_TERMINAL_STATUSES = frozenset({"resolved", "rejected"})


@dataclass(frozen=True, slots=True)
class QaEvidenceCommitReceiptV1:
    """Projection-barrier coordinates for one committed QA evidence fact."""

    run_id: str
    append_id: str
    event_hash: str

    def __post_init__(self) -> None:
        for field_name in ("run_id", "append_id", "event_hash"):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"{field_name} must be a non-empty string")

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-safe barrier projection."""

        return {
            "run_id": self.run_id,
            "append_id": self.append_id,
            "event_hash": self.event_hash,
        }


@dataclass(frozen=True, slots=True)
class QaVerdictCommitReceiptV1:
    """Immutable receipt for one canonical QA verdict ledger fact."""

    run_id: str
    append_id: str
    event_hash: str
    envelope_hash: str
    verdict: str

    def __post_init__(self) -> None:
        for field_name in (
            "run_id",
            "append_id",
            "event_hash",
            "envelope_hash",
            "verdict",
        ):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"{field_name} must be a non-empty string")

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-safe final-verdict commit receipt."""

        return {
            "run_id": self.run_id,
            "append_id": self.append_id,
            "event_hash": self.event_hash,
            "envelope_hash": self.envelope_hash,
            "verdict": self.verdict,
        }


def _findings_count(value: Any) -> int:
    if isinstance(value, (list, tuple)):
        return len(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    return 0


def _append_qa_event(
    *,
    workspace: str,
    run_id: str,
    event: dict[str, Any],
) -> tuple[str, str]:
    """Append one QA event and return immutable projection coordinates."""

    append_result = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=workspace,
            run_id=run_id,
            event=event,
        )
    )
    receipt = append_result.receipt
    event_receipt = receipt.get("event") if isinstance(receipt, Mapping) else None
    event_map = event_receipt if isinstance(event_receipt, Mapping) else {}
    append_id = str(event_map.get("append_id") or "").strip()
    event_hash = str(event_map.get("content_id") or event_map.get("event_id") or "").strip()
    if not append_id or not event_hash:
        raise RuntimeError("Run Ledger append did not return QA projection-barrier coordinates")
    return append_id, event_hash


def _normalized_identity(
    *,
    workspace: str,
    run_id: str,
    task_id: str,
) -> tuple[str, str, str]:
    workspace_token = str(workspace or "").strip()
    run_token = str(run_id or "").strip()
    task_token = str(task_id or "").strip()
    if not workspace_token:
        raise ValueError("workspace must be a non-empty string")
    if not run_token:
        raise ValueError("run_id must be a non-empty string")
    if not task_token:
        raise ValueError("task_id must be a non-empty string")
    return workspace_token, run_token, task_token


def _validate_canonical_verdict_route(
    *,
    envelope: Mapping[str, Any],
    verdict: str,
    conflicts: list[Any],
) -> None:
    """Reject internally contradictory canonical verdict projections."""

    ok = envelope.get("ok") is True
    next_stage = str(envelope.get("next_stage") or "").strip().lower()
    terminal_status = str(envelope.get("terminal_status") or "").strip().lower()
    if next_stage and next_stage not in _CANONICAL_NEXT_STAGES:
        raise ValueError(f"unsupported canonical QA next_stage: {next_stage!r}")
    if terminal_status and terminal_status not in _CANONICAL_TERMINAL_STATUSES:
        raise ValueError(f"unsupported canonical QA terminal_status: {terminal_status!r}")
    if next_stage and terminal_status:
        raise ValueError("canonical QA verdict cannot set both route destinations")
    if conflicts and verdict != "BLOCKED":
        raise ValueError("canonical QA conflicts require a BLOCKED verdict")
    if verdict == "PASS" and (not ok or next_stage or terminal_status != "resolved"):
        raise ValueError("PASS verdict must authorize only resolved terminal state")
    if verdict == "FAIL" and (ok or (not next_stage and not terminal_status)):
        raise ValueError("FAIL verdict requires one non-success route destination")
    if verdict == "BLOCKED" and (ok or not next_stage or terminal_status):
        raise ValueError("BLOCKED verdict must route to a non-terminal canonical stage")
    if verdict == "NEEDS_REVIEW" and (ok or next_stage != "waiting_human" or terminal_status):
        raise ValueError("NEEDS_REVIEW verdict must route to waiting_human")


def commit_qa_evidence(
    *,
    workspace: str,
    run_id: str,
    task_id: str,
    gate_name: str,
    ok: bool,
    summary: str,
    verdict: str,
    audit_result: Mapping[str, Any] | None = None,
    failure_reason: str = "",
    job_token: Mapping[str, Any] | None = None,
) -> QaEvidenceCommitReceiptV1:
    """Commit one typed QA observation and return its barrier receipt.

    Raises:
        ValueError: If identity fields or append receipt coordinates are absent.
        RuntimeError: Propagated when Run Ledger cannot commit the evidence.

    Complexity:
        O(f + m) time and memory over findings and metrics payload size, plus
        the Run Ledger append cost.
    """

    workspace_token, run_token, task_token = _normalized_identity(
        workspace=workspace,
        run_id=run_id,
        task_id=task_id,
    )
    gate_token = str(gate_name or "qa_audit").strip()
    if gate_token.lower() in _RESERVED_FINAL_GATE_NAMES:
        raise ValueError(f"{gate_token!r} is reserved for canonical QA verdict events")

    audit = dict(audit_result or {})
    findings = audit.get("findings", [])
    metrics_raw = audit.get("metrics")
    metrics = dict(metrics_raw) if isinstance(metrics_raw, Mapping) else {}
    verdict_token = str(verdict or "").strip().upper() or ("PASS" if ok else "FAIL")
    clean_summary = str(summary or gate_token).strip()
    physical_evidence = {
        "schema_version": "qa.evidence_fact.v1",
        "task_id": task_token,
        "authoritative": False,
        "evidence_kind": "qa_evidence",
        "audit_id": str(audit.get("audit_id") or ""),
        "verdict": verdict_token,
        "failure_class": str(audit.get("failure_class") or ""),
        "responsible_layer": str(audit.get("responsible_layer") or "qa"),
        "failure_reason": str(failure_reason or "").strip(),
        "findings_count": _findings_count(findings),
        "metrics": metrics,
        "modalities": {
            "qa": {
                "present": True,
                "ok": bool(ok),
                "detail": clean_summary,
                "verdict": verdict_token,
                "findings_count": _findings_count(findings),
            }
        },
        "qa_verifiers": [
            {
                "id": str(audit.get("audit_id") or task_token),
                "name": gate_token,
                "kind": "qa",
                "modality": "qa",
                "ok": bool(ok),
                "detail": clean_summary,
            }
        ],
    }
    event: dict[str, Any] = {
        "event_type": "gate_evaluated",
        "stage": "qa",
        "task_id": task_token,
        "authoritative": False,
        "gate": {
            "name": gate_token,
            "ok": bool(ok),
            "summary": clean_summary,
        },
        "physical_evidence": physical_evidence,
    }
    if job_token:
        event["job_token"] = dict(job_token)

    append_id, event_hash = _append_qa_event(
        workspace=workspace_token,
        run_id=run_token,
        event=event,
    )
    return QaEvidenceCommitReceiptV1(
        run_id=run_token,
        append_id=append_id,
        event_hash=event_hash,
    )


def commit_qa_verdict(
    *,
    workspace: str,
    run_id: str,
    task_id: str,
    envelope: Mapping[str, Any],
    evidence_commit_receipt: Mapping[str, str],
    job_token: Mapping[str, Any] | None = None,
) -> QaVerdictCommitReceiptV1:
    """Commit the canonical QA verdict before any task-state transition.

    The verdict event is authoritative only because it embeds a validated
    ``qa.verdict_envelope.v1`` produced from a satisfied Run Ledger barrier.
    Local audit observations cannot call this function without the evidence
    commit coordinates that fed that barrier.

    Raises:
        ValueError: If identity, envelope, verdict, or barrier data is invalid.
        RuntimeError: If Run Ledger does not durably acknowledge the append.

    Complexity:
        O(e) time and memory over the serialized envelope, plus one Run Ledger
        append. No workspace files are scanned.
    """

    workspace_token, run_token, task_token = _normalized_identity(
        workspace=workspace,
        run_id=run_id,
        task_id=task_id,
    )
    envelope_map = dict(envelope)
    if envelope_map.get("schema_version") != "qa.verdict_envelope.v1":
        raise ValueError("envelope must be qa.verdict_envelope.v1")
    if str(envelope_map.get("run_id") or "").strip() != run_token:
        raise ValueError("envelope run_id does not match commit run_id")
    if str(envelope_map.get("task_id") or "").strip() != task_token:
        raise ValueError("envelope task_id does not match commit task_id")
    if str(envelope_map.get("workspace") or "").strip() != workspace_token:
        raise ValueError("envelope workspace does not match commit workspace")

    verdict = str(envelope_map.get("verdict") or "").strip().upper()
    if verdict not in _CANONICAL_VERDICTS:
        raise ValueError(f"unsupported canonical QA verdict: {verdict!r}")
    envelope_hash = str(envelope_map.get("content_hash") or "").strip()
    if not envelope_hash:
        raise ValueError("canonical QA verdict envelope requires content_hash")

    ledger_raw = envelope_map.get("ledger")
    ledger = dict(ledger_raw) if isinstance(ledger_raw, Mapping) else {}
    if ledger.get("source") != "run_ledger_projection" or ledger.get("available") is not True:
        raise ValueError("canonical QA verdict requires an available Run Ledger projection")

    evidence_raw = envelope_map.get("evidence")
    evidence = dict(evidence_raw) if isinstance(evidence_raw, Mapping) else {}
    conflict_raw = evidence.get("conflict_matrix")
    conflict_matrix = dict(conflict_raw) if isinstance(conflict_raw, Mapping) else {}
    conflicts = conflict_matrix.get("conflicts")
    if not isinstance(conflicts, list):
        raise ValueError("canonical QA verdict requires a typed conflict matrix")
    _validate_canonical_verdict_route(
        envelope=envelope_map,
        verdict=verdict,
        conflicts=conflicts,
    )

    receipt = {str(key): str(value) for key, value in evidence_commit_receipt.items()}
    if str(receipt.get("run_id") or "").strip() != run_token:
        raise ValueError("evidence commit receipt run_id does not match verdict run_id")
    if not str(receipt.get("append_id") or "").strip() or not str(receipt.get("event_hash") or "").strip():
        raise ValueError("canonical QA verdict requires evidence barrier coordinates")

    classification_raw = envelope_map.get("classification")
    classification = dict(classification_raw) if isinstance(classification_raw, Mapping) else {}
    failure_class_raw = classification.get("failure_class")
    failure_class = str(failure_class_raw).strip() if failure_class_raw is not None else None
    responsible_layer = str(classification.get("responsible_layer") or "").strip()
    if not responsible_layer:
        raise ValueError("canonical QA verdict requires responsible_layer")
    if verdict == "PASS" and failure_class:
        raise ValueError("canonical PASS verdict must not carry a failure_class")
    if verdict != "PASS" and not failure_class:
        raise ValueError("canonical non-PASS verdict requires failure_class")
    evidence_refs_raw = envelope_map.get("evidence_refs")
    evidence_refs = (
        [str(item) for item in evidence_refs_raw if str(item).strip()]
        if isinstance(evidence_refs_raw, (list, tuple))
        else []
    )
    barrier_raw = evidence.get("barrier")
    barrier = dict(barrier_raw) if isinstance(barrier_raw, Mapping) else {}
    verdict_id = f"qa-verdict-{envelope_hash}"
    physical_evidence = {
        "schema_version": "qa.final_verdict_fact.v1",
        "task_id": task_token,
        "run_id": run_token,
        "authoritative": True,
        "evidence_kind": "qa_final_verdict",
        "verdict": verdict,
        "failure_class": failure_class,
        "responsible_layer": responsible_layer,
        "next_stage": str(envelope_map.get("next_stage") or ""),
        "terminal_status": str(envelope_map.get("terminal_status") or ""),
        "envelope_hash": envelope_hash,
        "verdict_id": verdict_id,
        "evidence_refs": evidence_refs,
        "barrier_coordinates": receipt,
        "projection_barrier": barrier,
        "qa_verdict_envelope": envelope_map,
        "modalities": {
            "qa": {
                "present": True,
                "ok": bool(envelope_map.get("ok")),
                "detail": f"Canonical QA verdict: {verdict}",
                "verdict": verdict,
            }
        },
    }
    event: dict[str, Any] = {
        "event_id": verdict_id,
        "event_type": "gate_evaluated",
        "stage": "qa",
        "task_id": task_token,
        "authoritative": True,
        "gate": {
            "name": _FINAL_VERDICT_GATE_NAME,
            "ok": bool(envelope_map.get("ok")),
            "summary": f"Canonical QA verdict: {verdict}",
        },
        "physical_evidence": physical_evidence,
    }
    if job_token:
        event["job_token"] = dict(job_token)

    append_id, event_hash = _append_qa_event(
        workspace=workspace_token,
        run_id=run_token,
        event=event,
    )
    return QaVerdictCommitReceiptV1(
        run_id=run_token,
        append_id=append_id,
        event_hash=event_hash,
        envelope_hash=envelope_hash,
        verdict=verdict,
    )


__all__ = [
    "QaEvidenceCommitReceiptV1",
    "QaVerdictCommitReceiptV1",
    "commit_qa_evidence",
    "commit_qa_verdict",
]
