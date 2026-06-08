"""Public service exports for `qa.audit_verdict` cell."""

from __future__ import annotations

import asyncio
import hashlib
import logging
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
    GetQaVerdictQueryV1,
    ParseTracebackFramesCommandV1,
    ParseTracebackFramesResultV1,
    QaAuditResultV1,
    RunQaAuditCommandV1,
    RunVisualQaAuditCommandV1,
    TracebackFrameV1,
    VisualAuditFindingV1,
    VisualQaAuditResultV1,
)

logger = logging.getLogger(__name__)

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
    result = QaAuditResultV1(
        ok=audit.verdict == "PASS",
        task_id=command.task_id,
        workspace=command.workspace,
        verdict=audit.verdict,
        score=1.0 if audit.verdict == "PASS" else 0.0,
        findings=findings,
        suggestions=(),
    )
    # 持久化最新判定，供后续只读上下文信号（verdict_history）回读。失败不得影响审计本身。
    _persist_qa_verdict(command.task_id, command.workspace, result, run_id=str(criteria.get("run_id") or "") or None)
    return result


def _persist_qa_verdict(
    task_id: str,
    workspace: str,
    result: QaAuditResultV1,
    *,
    run_id: str | None = None,
) -> None:
    """把一次 QA 判定持久化到磁盘（原子写）。任何失败仅记日志、不抛出。"""
    try:
        from datetime import datetime, timezone

        from polaris.cells.qa.audit_verdict.internal.verdict_persistence import VerdictPersistence

        now = datetime.now(timezone.utc).isoformat()
        safe_task = re.sub(r"[^A-Za-z0-9_.-]", "_", str(task_id)) or "task"
        # 微秒级时间戳保证同一 task 多次判定的 verdict_id 唯一且按时间可排序。
        stamp = now.replace(":", "").replace("-", "").replace(".", "")
        verdict_id = f"{safe_task}__{stamp}"
        VerdictPersistence(workspace).save(
            verdict_id,
            {
                "task_id": str(task_id),
                "workspace": str(workspace),
                "run_id": run_id or "",
                "verdict": result.verdict,
                "score": float(result.score),
                "findings": list(result.findings),
                "suggestions": list(result.suggestions),
                "created_at": now,
                "updated_at": now,
            },
        )
    except Exception:  # noqa: BLE001 - 持久化是旁路，绝不能影响审计主流程
        logger.debug("persist qa verdict failed", exc_info=True)


def _latest_verdict_for_task(
    persistence: Any,
    *,
    task_id: str,
    run_id: str | None,
) -> dict[str, Any] | None:
    """返回该 task 最新的已持久化判定 payload；无则 None。"""
    matches: list[tuple[str, str, dict[str, Any]]] = []
    for verdict_id in persistence.list_all():
        payload = persistence.load(verdict_id)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("task_id") or "").strip() != task_id:
            continue
        if run_id and str(payload.get("run_id") or "").strip() != run_id:
            continue
        updated_at = str(payload.get("updated_at") or payload.get("created_at") or "").strip()
        matches.append((updated_at, verdict_id, payload))
    if not matches:
        return None
    _ts, _vid, payload = max(matches, key=lambda item: (item[0], item[1]))
    return payload


def get_qa_verdict(query: GetQaVerdictQueryV1) -> QaAuditResultV1:
    """读取某 task 最新的已持久化 QA 判定（只读，无副作用）。

    无持久化判定时返回 ``ok=False, verdict="missing"``。这是 verdict_history
    上下文信号的数据源（对应 chief_engineer 的 get_blueprint_status）。
    """
    if not isinstance(query, GetQaVerdictQueryV1):
        raise TypeError("query must be GetQaVerdictQueryV1")
    from polaris.cells.qa.audit_verdict.internal.verdict_persistence import VerdictPersistence

    persistence = VerdictPersistence(query.workspace, ensure_directory=False)
    payload = _latest_verdict_for_task(persistence, task_id=query.task_id, run_id=query.run_id)
    if payload is None:
        return QaAuditResultV1(
            ok=False,
            task_id=query.task_id,
            workspace=query.workspace,
            verdict="missing",
            score=0.0,
        )
    return QaAuditResultV1(
        ok=True,
        task_id=query.task_id,
        workspace=query.workspace,
        verdict=str(payload.get("verdict") or "unknown").strip() or "unknown",
        score=float(payload.get("score") or 0.0),
        findings=_string_tuple(payload.get("findings")),
        suggestions=_string_tuple(payload.get("suggestions")),
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
