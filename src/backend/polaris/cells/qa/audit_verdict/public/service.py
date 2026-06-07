"""Public service exports for `qa.audit_verdict` cell."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Coroutine, TypeVar

# Cross-Cell import must go through the public boundary of `audit.verdict`.
from polaris.cells.audit.verdict.public.service import ReviewGate, get_review_gate
from polaris.cells.qa.audit_verdict.internal.qa_agent import QAAgent
from polaris.cells.qa.audit_verdict.internal.qa_consumer import QAConsumer
from polaris.cells.qa.audit_verdict.internal.qa_service import AuditResult, QAConfig, QAService
from polaris.cells.qa.audit_verdict.internal.quality_service import QualityService, get_quality_service
from polaris.cells.qa.audit_verdict.public.contracts import QaAuditResultV1, RunQaAuditCommandV1

_T = TypeVar("_T")


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


__all__ = [
    "AuditResult",
    "QAAgent",
    "QAConfig",
    "QAConsumer",
    "QAService",
    "QualityService",
    "ReviewGate",
    "get_quality_service",
    "get_review_gate",
    "run_qa_audit",
]
