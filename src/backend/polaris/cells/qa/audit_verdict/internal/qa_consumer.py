"""QA consumer that polls PENDING_QA and emits audit verdicts."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from polaris.cells.qa.audit_verdict.internal.qa_service import QAService
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
)
from polaris.cells.runtime.task_market.public.service import get_task_market_service

logger = logging.getLogger(__name__)
_REQUEUE_STAGE_BY_VERDICT: dict[str, str] = {
    "REQUEUE_EXEC": "pending_exec",
    "RETRY_EXEC": "pending_exec",
    "REQUEUE_DESIGN": "pending_design",
    "RETRY_DESIGN": "pending_design",
    "REQUEUE_QA": "pending_qa",
    "RETRY_QA": "pending_qa",
    "NEEDS_REVIEW": "waiting_human",
    "WAITING_HUMAN": "waiting_human",
    "HITL": "waiting_human",
}
_VALID_ROUTE_STAGES = frozenset({"pending_design", "pending_exec", "pending_qa", "waiting_human"})


def _resolve_qa_route(audit_result: dict[str, Any]) -> tuple[str, str, str]:
    """Resolve QA route from audit result.

    Returns:
        ``(verdict, next_stage, terminal_status)``.
        One of ``next_stage`` / ``terminal_status`` will be non-empty.
    """
    verdict = str(audit_result.get("verdict") or "FAIL").strip().upper() or "FAIL"
    explicit_stage = str(audit_result.get("next_stage") or "").strip().lower()
    if explicit_stage in _VALID_ROUTE_STAGES:
        return verdict, explicit_stage, ""
    if verdict == "PASS":
        return verdict, "", "resolved"
    if verdict in {"FAIL", "REJECT", "REJECTED"}:
        return verdict, "", "rejected"
    mapped_stage = _REQUEUE_STAGE_BY_VERDICT.get(verdict, "")
    if mapped_stage:
        return verdict, mapped_stage, ""
    return verdict, "", "rejected"


class QAConsumer:
    """QA consumer for PENDING_QA tasks.

    This consumer polls the task market for tasks in the ``pending_qa`` stage,
    runs the QA audit, and acknowledges the task with ``resolved`` or
    ``rejected`` as the terminal status.

    Args:
        workspace: Workspace path for task market operations.
        worker_id: Unique identifier for this worker instance.
        visibility_timeout_seconds: How long a claimed task is locked before it
            becomes visible to other workers again on failure.
        poll_interval: Seconds to sleep between poll cycles when no task is found.
    """

    def __init__(
        self,
        workspace: str,
        worker_id: str = "qa_worker",
        visibility_timeout_seconds: int = 900,
        poll_interval: float = 5.0,
    ) -> None:
        self._workspace = str(workspace or "").strip()
        if not self._workspace:
            raise ValueError("workspace must be a non-empty string")
        self._worker_id = str(worker_id or "").strip()
        if not self._worker_id:
            raise ValueError("worker_id must be a non-empty string")
        self._visibility_timeout = int(visibility_timeout_seconds)
        self._poll_interval = float(poll_interval)
        self._stop_event = threading.Event()
        self._svc = get_task_market_service()

        # Initialize QA service
        from polaris.cells.qa.audit_verdict.internal.qa_service import QAConfig

        qa_config = QAConfig(workspace=self._workspace, enable_auto_audit=False)
        self._qa_svc = QAService(qa_config)

    def poll_once(self) -> list[dict[str, Any]]:
        """Poll once for PENDING_QA tasks.

        Claims and processes all available tasks until no claimable work remains.
        Returns a list of processed task results, each containing ``task_id``,
        ``ok`` status, and (on failure) ``reason``.
        """
        results: list[dict[str, Any]] = []
        while not self._stop_event.is_set():
            processed = self._claim_and_process_one()
            if processed is None:
                break
            results.append(processed)
        return results

    def run(self) -> None:
        """Run the consumer continuously until stop() is called."""
        logger.info("QA consumer running — press Ctrl+C to stop")
        while not self._stop_event.is_set():
            results = self.poll_once()
            if not results:
                self._stop_event.wait(self._poll_interval)

    def stop(self) -> None:
        """Signal the consumer to stop after the current cycle."""
        self._stop_event.set()

    def _claim_and_process_one(self) -> dict[str, Any] | None:
        """Attempt to claim one PENDING_QA task and process it.

        Returns:
            Processed result dict, or None if no claimable task was found.
        """
        claim = self._svc.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=self._workspace,
                stage="pending_qa",
                worker_id=self._worker_id,
                worker_role="qa",
                visibility_timeout_seconds=self._visibility_timeout,
            )
        )
        if not claim.ok:
            return None

        task_id = str(claim.task_id or "").strip()
        lease_token = str(claim.lease_token or "").strip()

        try:
            payload: dict[str, Any] = dict(claim.payload) if claim.payload else {}

            # Run QA audit
            audit_result = self._run_qa_audit(task_id, payload)

            verdict, next_stage, terminal_status = _resolve_qa_route(audit_result)

            ack_payload: dict[str, Any] = {
                "verdict": verdict,
                "audit_id": audit_result.get("audit_id", ""),
                "findings": audit_result.get("findings", []),
                "score": audit_result.get("score", 0.0),
                "qa_next_stage": next_stage,
                "qa_terminal_status": terminal_status,
            }

            command_kwargs: dict[str, Any] = {
                "workspace": self._workspace,
                "task_id": task_id,
                "lease_token": lease_token,
                "summary": f"QA verdict: {verdict}",
                "metadata": ack_payload,
            }
            if next_stage:
                command_kwargs["next_stage"] = next_stage
            else:
                command_kwargs["terminal_status"] = terminal_status

            ack = self._svc.acknowledge_task_stage(AcknowledgeTaskStageCommandV1(**command_kwargs))
            return {
                "task_id": task_id,
                "ok": bool(ack.ok),
                "verdict": verdict,
                "status": str(ack.status or ""),
            }

        except Exception as exc:
            logger.exception("QA consumer failed for task %s: %s", task_id, exc)
            self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="QA_audit_failed",
                    error_message=str(exc),
                    requeue_stage="pending_qa",
                )
            )
            return {
                "task_id": task_id,
                "ok": False,
                "reason": str(exc),
            }

    async def _run_qa_audit_async(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run QA audit asynchronously and return result dict.

        Args:
            task_id: Identifier of the task being audited.
            payload: Task payload dict from the task market.

        Returns:
            Audit result dict with ``verdict``, ``audit_id``, ``findings``, ``score``.
        """

        # Extract changed files from payload
        changed_files: list[str] = []
        if isinstance(payload, dict):
            changed_files = [str(f) for f in payload.get("target_files", []) if f and isinstance(f, (str, os.PathLike))]
            # Also check scope
            scope = payload.get("scope", [])
            if isinstance(scope, list):
                for item in scope:
                    if isinstance(item, dict):
                        path = item.get("path") or item.get("file")
                        if path:
                            changed_files.append(str(path))
                    elif isinstance(item, str):
                        changed_files.append(item)

        task_subject = str(payload.get("title", payload.get("subject", task_id)))

        # Run audit
        audit = await self._qa_svc.audit_task(
            task_id=task_id,
            task_subject=task_subject,
            changed_files=changed_files,
        )

        # Convert to result dict
        findings = []
        for issue in audit.issues:
            findings.append(
                f"[{issue.get('severity', 'info')}] {issue.get('file', 'unknown')}: {issue.get('message', '')}"
            )

        return {
            "audit_id": audit.audit_id,
            "verdict": audit.verdict,
            "findings": findings,
            "score": audit.metrics.get("files_audited", 0) * 10 if audit.verdict == "PASS" else 0.0,
        }

    def _run_qa_audit(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Synchronous wrapper for QA audit.

        Args:
            task_id: Identifier of the task being audited.
            payload: Task payload dict from the task market.

        Returns:
            Audit result dict with ``verdict``, ``audit_id``, ``findings``, ``score``.
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(self._run_qa_audit_async(task_id, payload))
