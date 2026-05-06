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
_CODE_FILE_EXTENSIONS = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".php",
        ".py",
        ".pyi",
        ".rb",
        ".rs",
        ".scala",
        ".sql",
        ".swift",
        ".ts",
        ".tsx",
    }
)
_CODE_PATH_PREFIXES = ("src/", "test/", "tests/", "app/", "apps/", "backend/", "frontend/", "lib/", "scripts/")
_NO_CHANGE_FLAGS = frozenset(
    {
        "allow_no_changes",
        "no_changes_expected",
        "allow_empty_changed_files",
        "director_noop_allowed",
    }
)
_NO_CHANGE_MODES = frozenset(
    {
        "noop",
        "no_op",
        "no-op",
        "read_only",
        "read-only",
        "inspection",
        "inspection_only",
        "analysis_only",
    }
)


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


def _normalize_path_values(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (str, os.PathLike)):
        raw_values: list[Any] = [raw]
    elif isinstance(raw, (list, tuple, set)):
        raw_values = list(raw)
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        if not isinstance(item, (str, os.PathLike)):
            continue
        token = str(item).strip()
        if not token:
            continue
        key = token.replace("\\", "/").lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(token)
    return normalized


def _truthy_payload_flag(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _allows_no_director_changes(payload: dict[str, Any]) -> bool:
    for key in _NO_CHANGE_FLAGS:
        if _truthy_payload_flag(payload, key):
            return True

    for key in ("execution_mode", "task_mode", "mode", "change_mode"):
        mode = str(payload.get(key) or "").strip().lower()
        if mode in _NO_CHANGE_MODES:
            return True
    return False


def _collect_payload_paths(payload: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    paths: list[str] = []
    for key in keys:
        paths.extend(_normalize_path_values(payload.get(key)))
    scope = payload.get("scope")
    if isinstance(scope, list) and "scope" in keys:
        for item in scope:
            if isinstance(item, dict):
                paths.extend(_normalize_path_values(item.get("path") or item.get("file")))
            else:
                paths.extend(_normalize_path_values(item))
    return _normalize_path_values(paths)


def _has_code_path(paths: list[str]) -> bool:
    for raw_path in paths:
        path = raw_path.replace("\\", "/").strip().lower().lstrip("./")
        if not path:
            continue
        if path.endswith(tuple(_CODE_FILE_EXTENSIONS)):
            return True
        if path.startswith(_CODE_PATH_PREFIXES):
            return True
    return False


def _is_code_task_payload(payload: dict[str, Any]) -> bool:
    if _has_code_path(_collect_payload_paths(payload, ("target_files", "scope_paths", "scope"))):
        return True

    task_payload = payload.get("task")
    if isinstance(task_payload, dict) and _has_code_path(
        _collect_payload_paths(task_payload, ("target_files", "scope_paths", "scope"))
    ):
        return True

    text_fields = (
        payload.get("type"),
        payload.get("task_type"),
        payload.get("category"),
        payload.get("title"),
        payload.get("subject"),
        payload.get("goal"),
    )
    haystack = " ".join(str(value).lower() for value in text_fields if value)
    if "document" in haystack or "docs" in haystack:
        return False
    return any(token in haystack for token in ("code", "implement", "fix", "refactor", "test"))


def _requires_director_changed_files(payload: dict[str, Any]) -> bool:
    if _allows_no_director_changes(payload):
        return False
    if str(payload.get("blueprint_id") or "").strip():
        return True
    return _is_code_task_payload(payload)


def _extract_director_changed_files(payload: dict[str, Any]) -> list[str]:
    changed_files = _normalize_path_values(payload.get("changed_files"))
    if changed_files:
        return changed_files

    for key in ("director_changed_files", "files_changed"):
        changed_files = _normalize_path_values(payload.get(key))
        if changed_files:
            return changed_files

    for key in ("execution_result", "director_result", "execution"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            changed_files = _normalize_path_values(nested.get("changed_files"))
            if changed_files:
                return changed_files
    return []


def _extract_fallback_audit_files(payload: dict[str, Any]) -> list[str]:
    return _collect_payload_paths(payload, ("target_files", "scope_paths", "scope"))


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
                "metrics": audit_result.get("metrics", {}),
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

        # Extract Director execution evidence from payload.  Ack metadata from
        # Director is merged into task_market payload before QA claims it.
        changed_files: list[str] = []
        require_director_evidence = False
        if isinstance(payload, dict):
            changed_files = _extract_director_changed_files(payload)
            require_director_evidence = _requires_director_changed_files(payload)
            if not changed_files and not require_director_evidence:
                changed_files = _extract_fallback_audit_files(payload)

        task_subject = str(payload.get("title", payload.get("subject", task_id)))

        # Run audit
        audit = await self._qa_svc.audit_task(
            task_id=task_id,
            task_subject=task_subject,
            changed_files=changed_files,
            require_changed_files=require_director_evidence,
        )

        # Convert to result dict
        findings = []
        for issue in audit.issues:
            findings.append(
                f"[{issue.get('severity', 'info')}] {issue.get('file', 'unknown')}: {issue.get('message', '')}"
            )

        result: dict[str, Any] = {
            "audit_id": audit.audit_id,
            "verdict": audit.verdict,
            "findings": findings,
            "metrics": dict(audit.metrics),
            "score": audit.metrics.get("files_audited", 0) * 10 if audit.verdict == "PASS" else 0.0,
        }
        if audit.metrics.get("missing_director_changed_files_evidence"):
            result["next_stage"] = "pending_exec"
        return result

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
