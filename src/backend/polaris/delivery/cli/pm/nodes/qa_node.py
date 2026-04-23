"""QA Node implementation (门下侍中).

This module implements the QA role node for quality auditing
and integration verification.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Any

from polaris.delivery.cli.pm.command_runner import parse_command_args
from polaris.delivery.cli.pm.nodes.base import BaseRoleNode
from polaris.delivery.cli.pm.nodes.protocols import RoleContext, RoleResult

logger = logging.getLogger(__name__)


class QANode(BaseRoleNode):
    """QA Node - 门下侍中 (Censor).

    Responsible for:
    - Running integration QA after task execution
    - Quality gate evaluation
    - Evidence verification
    - Audit reporting
    """

    @property
    def role_name(self) -> str:
        return "QA"

    def get_dependencies(self) -> list[str]:
        """QA depends on Director completing."""
        return ["Director"]

    def get_trigger_conditions(self) -> list[str]:
        """QA runs after Director completes."""
        return ["director_complete", "manual"]

    def can_handle(self, context: RoleContext) -> bool:
        """Can handle if Director result is available."""
        return context.director_result is not None

    def _execute_impl(self, context: RoleContext) -> RoleResult:
        """Execute QA logic for quality auditing."""
        from polaris.cells.orchestration.pm_dispatch.public import run_post_dispatch_integration_qa

        workspace = context.workspace_full
        iteration = context.pm_iteration
        run_id = context.run_id
        args = context.args

        # Get tasks and director result
        tasks = context.get_tasks()

        # Get status summary
        from polaris.delivery.cli.pm.tasks_utils import get_director_task_status_summary

        status_summary = get_director_task_status_summary(tasks)

        metadata = context.metadata if isinstance(context.metadata, dict) else {}
        run_dir = context.run_dir or str(metadata.get("run_dir") or "").strip()
        docs_stage = metadata.get("docs_stage")

        # Run integration QA with legacy-compatible logic
        start_time = time.time()
        verify_result = run_post_dispatch_integration_qa(
            args=args,
            workspace_full=workspace,
            cache_root_full=context.cache_root_full,
            run_dir=run_dir,
            run_id=run_id,
            iteration=iteration,
            tasks=tasks,
            run_events=context.events_path,
            dialogue_full=context.dialogue_path,
            docs_stage=docs_stage if isinstance(docs_stage, dict) else None,
        )

        # Determine verdict
        ran = bool(verify_result.get("ran"))
        passed = bool(verify_result.get("passed") is True)
        reason = str(verify_result.get("reason") or "").strip()
        summary = str(verify_result.get("summary") or "").strip()
        errors = verify_result.get("errors", [])

        duration_ms = int((time.time() - start_time) * 1000)

        return RoleResult(
            success=(not ran) or passed,
            exit_code=0 if ((not ran) or passed) else 1,
            tasks=tasks,
            report=verify_result,
            next_role="",
            continue_reason=summary or reason or "QA stage completed",
            metadata={
                "ran": ran,
                "passed": passed,
                "reason": reason,
                "error_count": len(errors),
                "status_summary": status_summary,
                "integration_qa_result": verify_result,
            },
            duration_ms=duration_ms,
        )

    def _should_run_qa(self, status_summary: dict[str, Any]) -> bool:
        """Determine if QA should run based on task status."""
        total = status_summary.get("total", 0)
        if total <= 0:
            return False

        # Check if all tasks are terminal
        todo = status_summary.get("todo", 0)
        in_progress = status_summary.get("in_progress", 0)
        review = status_summary.get("review", 0)
        needs_continue = status_summary.get("needs_continue", 0)

        pending = todo + in_progress + review + needs_continue
        if pending > 0:
            return False

        # Check for failures
        failed = status_summary.get("failed", 0)
        blocked = status_summary.get("blocked", 0)

        # Don't run QA if there are failures (they need fixing first)
        return not (failed > 0 or blocked > 0)

    def _run_integration_verification(
        self,
        workspace: str,
        iteration: int,
        run_id: str,
    ) -> dict[str, Any]:
        """Run integration verification command."""
        import os

        result: dict[str, Any] = {
            "schema_version": 1,
            "ran": False,
            "passed": None,
            "summary": "",
            "errors": [],
            "run_id": run_id,
            "pm_iteration": iteration,
        }

        # Detect verification command
        command = self._detect_verification_command(workspace)
        if not command:
            result["summary"] = "No verification command detected"
            return result

        try:
            command_args = parse_command_args(command)
        except ValueError as exc:
            result["ran"] = False
            result["passed"] = False
            result["summary"] = f"Integration verification command rejected: {exc}"
            result["errors"].append(str(exc))
            return result

        # Get timeout
        timeout_seconds = 300
        timeout_env = os.environ.get("KERNELONE_INTEGRATION_QA_TIMEOUT_SECONDS", "300")
        try:
            timeout_seconds = max(30, int(timeout_env))
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to parse QA timeout: {e}")

        # Run command
        result["ran"] = True

        try:
            completed = subprocess.run(
                command_args,
                cwd=workspace,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )

            if completed.returncode == 0:
                result["passed"] = True
                result["summary"] = f"Integration verification passed: {command}"
            else:
                result["passed"] = False
                result["summary"] = f"Integration verification failed: {command}"

                # Collect errors
                stdout_lines = completed.stdout.splitlines()[-10:]
                stderr_lines = completed.stderr.splitlines()[-10:]

                for line in stdout_lines:
                    if line.strip():
                        result["errors"].append(f"[stdout] {line}")
                for line in stderr_lines:
                    if line.strip():
                        result["errors"].append(f"[stderr] {line}")

        except subprocess.TimeoutExpired:
            result["passed"] = False
            result["summary"] = f"Integration verification timed out after {timeout_seconds}s"
            result["errors"].append(f"Timeout after {timeout_seconds} seconds")

        except (RuntimeError, ValueError) as e:
            result["passed"] = False
            result["summary"] = f"Integration verification error: {e}"
            result["errors"].append(str(e))

        return result

    def _detect_verification_command(self, workspace: str) -> str | None:
        """Detect the appropriate verification command for the project."""
        from polaris.cells.orchestration.pm_planning.public import detect_integration_verify_command

        return detect_integration_verify_command(workspace)


__all__ = ["QANode"]
