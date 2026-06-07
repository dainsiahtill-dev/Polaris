"""Public service exports for `qa.audit_verdict` cell."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Coroutine, TypeVar

# Cross-Cell import must go through the public boundary of `audit.verdict`.
from polaris.cells.audit.verdict.public.service import ReviewGate, get_review_gate
from polaris.cells.qa.audit_verdict.internal.qa_agent import QAAgent
from polaris.cells.qa.audit_verdict.internal.qa_consumer import QAConsumer
from polaris.cells.qa.audit_verdict.internal.qa_service import AuditResult, QAConfig, QAService
from polaris.cells.qa.audit_verdict.internal.quality_service import QualityService, get_quality_service
from polaris.cells.qa.audit_verdict.public.contracts import (
    FailureSignalV1,
    ParseTracebackFramesCommandV1,
    ParseTracebackFramesResultV1,
    QaAuditResultV1,
    RunQaAuditCommandV1,
    RunVisualQaAuditCommandV1,
    TracebackFrameV1,
    VisualAuditFindingV1,
    VisualQaAuditResultV1,
)

_T = TypeVar("_T")
_TRACEBACK_FRAME_RE = re.compile(r'^\s*File "([^"]+)", line (\d+), in ([^\n]+)\s*$')


def _run_async(coro: Coroutine[Any, Any, _T]) -> _T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        token = value.strip()
        return (token,) if token else ()
    if isinstance(value, Mapping):
        return ()
    if isinstance(value, Iterable):
        rows: list[str] = []
        seen: set[str] = set()
        for item in value:
            token = str(item or "").strip()
            if token and token not in seen:
                rows.append(token)
                seen.add(token)
        return tuple(rows)
    token = str(value).strip()
    return (token,) if token else ()


def _finding_from_issue(issue: Mapping[str, Any]) -> str:
    file_path = str(issue.get("file") or "").strip()
    message = str(issue.get("message") or issue).strip()
    if file_path:
        return f"{file_path}: {message}"
    return message


def run_qa_audit(command: RunQaAuditCommandV1) -> QaAuditResultV1:
    """Run QA audit through the qa.audit_verdict public contract."""
    if not isinstance(command, RunQaAuditCommandV1):
        raise TypeError("command must be RunQaAuditCommandV1")

    criteria = dict(command.criteria)
    service = QAService(QAConfig(workspace=command.workspace, enable_auto_audit=False))
    audit = _run_async(
        service.audit_task(
            command.task_id,
            str(criteria.get("task_subject") or criteria.get("objective") or command.task_id),
            list(_string_tuple(criteria.get("changed_files"))),
            require_changed_files=bool(criteria.get("require_changed_files", False)),
        )
    )
    if not isinstance(audit, AuditResult):
        raise TypeError("QAService.audit_task must return AuditResult")

    findings = tuple(_finding_from_issue(issue) for issue in audit.issues)
    return QaAuditResultV1(
        ok=audit.verdict == "PASS",
        task_id=command.task_id,
        workspace=command.workspace,
        verdict=audit.verdict,
        score=1.0 if audit.verdict == "PASS" else 0.0,
        findings=findings,
        suggestions=(),
    )


def _visual_evidence_failure(command: RunVisualQaAuditCommandV1, exc: Exception) -> VisualQaAuditResultV1:
    finding = VisualAuditFindingV1(
        finding_id=f"visual-evidence-{command.task_id}",
        image_ref=command.image_refs[0],
        category="audit_evidence_append_failed",
        summary=f"visual audit evidence append failed: {type(exc).__name__}: {exc}",
        severity="error",
        confidence=1.0,
        metadata={"source_cell": "qa.audit_verdict", "owner_cell": "audit.evidence"},
    )
    return VisualQaAuditResultV1(
        ok=False,
        task_id=command.task_id,
        workspace=command.workspace,
        verdict="VISUAL_AUDIT_EVIDENCE_FAILED",
        image_refs=command.image_refs,
        model_capability_ref=command.model_capability_ref,
        findings=(finding,),
        score=0.0,
        evidence_refs=command.evidence_paths,
        metadata={
            "requires_image_input_model": True,
            "evidence_append_owner": "audit.evidence",
        },
    )


def run_visual_qa_audit(
    command: RunVisualQaAuditCommandV1,
    *,
    evidence_service: Any | None = None,
) -> VisualQaAuditResultV1:
    """Record a typed QA visual audit request after LLM image-capability preflight."""
    if not isinstance(command, RunVisualQaAuditCommandV1):
        raise TypeError("command must be RunVisualQaAuditCommandV1")

    criteria = dict(command.criteria)
    try:
        from polaris.cells.audit.evidence.public.contracts import AppendEvidenceEventCommandV1
        from polaris.cells.audit.evidence.public.service import append_evidence_event

        evidence_command = AppendEvidenceEventCommandV1(
            kind="qa.visual_audit",
            workspace=command.workspace,
            payload={
                "task_id": command.task_id,
                "run_id": command.run_id or "",
                "image_refs": command.image_refs,
                "model_capability_ref": command.model_capability_ref,
                "criteria": criteria,
                "evidence_paths": command.evidence_paths,
                "verdict": "VISUAL_AUDIT_RECORDED",
            },
            metadata={
                "source_cell": "qa.audit_verdict",
                "capability": "issue_visual_audit_verdict",
                "requires_image_input_model": True,
            },
        )
        if evidence_service is None:
            evidence_event = append_evidence_event(evidence_command)
        else:
            evidence_event = evidence_service.append_evidence_event(evidence_command)
    except Exception as exc:  # noqa: BLE001 - public RPC boundary returns structured failure
        return _visual_evidence_failure(command, exc)

    evidence_refs = (*command.evidence_paths, evidence_event.receipt_path)
    return VisualQaAuditResultV1(
        ok=True,
        task_id=command.task_id,
        workspace=command.workspace,
        verdict="VISUAL_AUDIT_RECORDED",
        image_refs=command.image_refs,
        model_capability_ref=command.model_capability_ref,
        score=1.0,
        evidence_refs=evidence_refs,
        metadata={
            "criteria_keys": tuple(sorted(str(key) for key in criteria)),
            "image_count": len(command.image_refs),
            "requires_image_input_model": True,
            "evidence_append_ref": evidence_event.receipt_path,
            "evidence_append_owner": "audit.evidence",
        },
    )


def _traceback_summary(lines: list[str]) -> str:
    for raw_line in reversed(lines):
        line = raw_line.strip()
        if not line or line.startswith("File ") or line.startswith("Traceback "):
            continue
        if set(line) <= {"^", "~"}:
            continue
        return line
    return "traceback failure"


def _signal_type(summary: str) -> str:
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?):", summary)
    if match:
        return match.group(1)
    return "traceback_failure"


def _signal_id(command: ParseTracebackFramesCommandV1, summary: str, frames: tuple[TracebackFrameV1, ...]) -> str:
    basis = "|".join(
        (
            command.task_id,
            command.workspace,
            summary,
            ";".join(f"{frame.path}:{frame.line}:{frame.function}" for frame in frames),
        )
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _parse_traceback_frame_rows(traceback_text: str) -> tuple[TracebackFrameV1, ...]:
    lines = traceback_text.splitlines()
    frames: list[TracebackFrameV1] = []
    for index, raw_line in enumerate(lines):
        match = _TRACEBACK_FRAME_RE.match(raw_line)
        if match is None:
            continue
        code = ""
        if index + 1 < len(lines):
            next_line = lines[index + 1].strip()
            if next_line and not next_line.startswith("File ") and not next_line.startswith("Traceback "):
                code = next_line
        frames.append(
            TracebackFrameV1(
                path=match.group(1),
                line=int(match.group(2)),
                function=match.group(3).strip(),
                code=code,
            )
        )
    return tuple(frames)


def parse_traceback_frames(command: ParseTracebackFramesCommandV1) -> ParseTracebackFramesResultV1:
    """Parse traceback text into a typed QA failure signal."""
    if not isinstance(command, ParseTracebackFramesCommandV1):
        raise TypeError("command must be ParseTracebackFramesCommandV1")

    lines = command.traceback_text.splitlines()
    frames = _parse_traceback_frame_rows(command.traceback_text)
    summary = _traceback_summary(lines)
    signal = FailureSignalV1(
        signal_id=_signal_id(command, summary, frames),
        task_id=command.task_id,
        workspace=command.workspace,
        signal_type=_signal_type(summary),
        summary=summary,
        frames=frames,
        source=str(command.metadata.get("source") or "traceback"),
        raw_excerpt=command.traceback_text[:2000],
        metadata=command.metadata,
    )
    return ParseTracebackFramesResultV1(
        ok=True,
        task_id=command.task_id,
        workspace=command.workspace,
        signal=signal,
    )


__all__ = [
    "AuditResult",
    "FailureSignalV1",
    "ParseTracebackFramesCommandV1",
    "ParseTracebackFramesResultV1",
    "QAAgent",
    "QAConfig",
    "QAConsumer",
    "QAService",
    "QualityService",
    "ReviewGate",
    "RunVisualQaAuditCommandV1",
    "TracebackFrameV1",
    "VisualAuditFindingV1",
    "VisualQaAuditResultV1",
    "get_quality_service",
    "get_review_gate",
    "parse_traceback_frames",
    "run_qa_audit",
    "run_visual_qa_audit",
]
