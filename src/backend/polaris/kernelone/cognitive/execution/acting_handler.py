"""Acting Phase Handler - Fast, precise, verifiable execution."""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.kernelone.cognitive.execution.cautious_policy import ExecutionRecommendation
from polaris.kernelone.cognitive.execution.rollback_manager import RollbackManager, RollbackPlan
from polaris.kernelone.cognitive.types import ActingOutput

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActingPhaseConfig:
    """Configuration for acting phase."""

    enable_rollback: bool = True
    max_retries: int = 1
    require_verification: bool = True


@dataclass(frozen=True)
class ActionResult:
    """Result of a single action."""

    action: str
    status: str  # success | failed | skipped
    output: str | None
    error: str | None
    verification_passed: bool = False
    # Error context propagated from tool executor for unified error handling
    error_type: str | None = None
    retryable: bool = True


class ActingPhaseHandler:
    """
    Implements the ACTING phase of Cognitive Life Form.

    ACTING PHASE characteristics:
    - Must be fast, precise, verifiable
    - No ambiguity allowed
    - Executes only after thinking phase is complete
    - Produces traceable action results
    """

    def __init__(
        self,
        config: ActingPhaseConfig | None = None,
        rollback_manager: RollbackManager | None = None,
        workspace: str | None = None,
    ) -> None:
        self._config = config or ActingPhaseConfig()
        self._rollback_manager = rollback_manager or RollbackManager()
        self._action_history: list[ActionResult] = []
        self._workspace = workspace or "."
        self._tool_executor: Any = None

    def _get_tool_executor(self) -> Any:
        """Lazy initialization of tool executor."""
        if self._tool_executor is None:
            try:
                from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor

                self._tool_executor = AgentAccelToolExecutor(
                    workspace=self._workspace,
                    worker_id="cognitive-acting",
                )
            except (RuntimeError, ValueError):
                self._tool_executor = None
        return self._tool_executor

    def reset_for_new_turn(self) -> None:
        """Reset failure budget for a new turn.

        Called at the start of each new conversation turn to prevent
        failures from previous turns affecting the current turn.
        """
        executor = self._get_tool_executor()
        if executor is not None and hasattr(executor, "reset_failure_budget"):
            executor.reset_failure_budget()
        self._action_history.clear()

    async def execute_action(
        self,
        action: str,
        execution_recommendation: ExecutionRecommendation,
        rollback_plan: RollbackPlan | None = None,
        target_paths: tuple[str, ...] | None = None,
    ) -> ActingOutput:
        """
        Execute a single action with cautious execution principles.

        For L3/L4 operations, MUST have rollback plan ready.
        """
        results = []

        # Extract target paths from action if not provided
        if target_paths is None:
            target_paths = self._extract_target_paths(action)
        # Normalize target paths against workspace so rollback snapshots
        # always read the same files that tool execution will touch.
        target_paths = self._resolve_target_paths(target_paths)

        # Check if action should be executed
        if execution_recommendation.path.value == "bypass":
            # Direct execution for L0
            result = self._execute_direct(action)
            # Append to history for BYPASS (like other paths do)
            result_with_verification = ActionResult(
                action=result.action,
                status=result.status,
                output=result.output,
                error=result.error,
                verification_passed=result.status == "success",
                error_type=result.error_type,
                retryable=result.retryable,
            )
            self._action_history.append(result_with_verification)
            results.append(result)

        elif execution_recommendation.path.value == "fast_think":
            # Fast execution with minimal verification
            result = await self._execute_with_verification(action, light=True)
            results.append(result)

        elif execution_recommendation.path.value == "thinking":
            # Thinking execution with full verification
            result = await self._execute_with_verification(action, light=False)
            results.append(result)

        elif execution_recommendation.path.value == "full_pipe":
            # Full pipe - requires rollback plan and user confirmation
            # Try to prepare rollback plan if not provided but required
            if (
                execution_recommendation.requires_rollback_plan
                and not rollback_plan
                and self._config.enable_rollback
                and target_paths
            ):
                with contextlib.suppress(ValueError):
                    rollback_plan = await self._rollback_manager.prepare_rollback(
                        action_description=action,
                        target_paths=target_paths,
                    )

            if execution_recommendation.requires_user_confirmation:
                return ActingOutput(
                    content=f"AWAITING USER CONFIRMATION for high-risk action: {action}",
                    actions_taken=(),
                    risk_level=execution_recommendation.risk_level,
                    rollback_steps=tuple(rollback_plan.steps) if rollback_plan else (),
                    verification_needed=True,
                )

            # L3/L4 operations use rollback-aware execution
            result = await self._execute_with_rollback(action, target_paths, rollback_plan)
            results.append(result)

        # Build rollback steps
        rollback_steps: tuple[str, ...] = ()
        if rollback_plan:
            rollback_steps = rollback_plan.steps

        # Build actions taken
        actions_taken = tuple(r.action for r in results)

        # Aggregate error context from tool results for unified error handling
        # This enables Workflow to make decisions based on error_type instead of pure retry counts
        error_type: str | None = None
        retryable = True
        for r in results:
            if r.status == "failed" and r.error_type:
                error_type = r.error_type
                retryable = r.retryable
                break  # Use first failed result's error context

        return ActingOutput(
            content="\n".join(r.output or r.error or "no output" for r in results),
            actions_taken=actions_taken,
            risk_level=execution_recommendation.risk_level,
            rollback_steps=rollback_steps,
            verification_needed=self._config.require_verification,
            error_type=error_type,
            retryable=retryable,
        )

    async def _execute_with_rollback(
        self,
        action: str,
        target_paths: tuple[str, ...],
        rollback_plan: RollbackPlan | None = None,
    ) -> ActionResult:
        """
        Execute action with rollback support for L3/L4 operations.

        If rollback_plan is provided, uses it directly. Otherwise, if enable_rollback
        is True and target_paths are provided, prepares a new rollback plan.

        Calls prepare_rollback() before execution, execute_rollback() on success,
        or abort_rollback() on failure.
        """
        # Step 1: Prepare rollback if not provided
        if rollback_plan is None and self._config.enable_rollback:
            try:
                rollback_plan = await self._rollback_manager.prepare_rollback(
                    action_description=action,
                    target_paths=target_paths,
                )
            except (RuntimeError, ValueError) as e:
                # Could not prepare rollback - action is blocked
                logger.error("Failed to prepare rollback for action %s: %s", action, e)
                return ActionResult(
                    action=action,
                    status="failed",
                    output=None,
                    error=f"BLOCKED: Cannot prepare rollback: {e}",
                    verification_passed=False,
                    error_type="rollback_prepare_failed",
                    retryable=False,  # Blocked - not retryable
                )

        # Step 2: Execute the action
        result = self._execute_direct(action)
        result_with_verification = ActionResult(
            action=result.action,
            status=result.status,
            output=result.output,
            error=result.error,
            verification_passed=result.status == "success",
            error_type=result.error_type,
            retryable=result.retryable,
        )

        # Step 3: Handle rollback based on execution result
        if rollback_plan is not None:
            if result.status == "success":
                # Execute rollback to verify state can be restored
                rollback_result = await self._rollback_manager.execute_rollback(rollback_plan)

                if rollback_result.status == "ABORTED":
                    # State drift detected - action result is blocked
                    logger.error(
                        "Rollback ABORTED for action %s: %s",
                        action,
                        rollback_result.reason,
                    )
                    return ActionResult(
                        action=action,
                        status="blocked",
                        output=result.output,
                        error=f"STATE DRIFT DETECTED: {rollback_result.reason}",
                        verification_passed=False,
                        error_type="state_drift",
                        retryable=False,  # Blocked - not retryable
                    )
                elif rollback_result.status == "PARTIAL":
                    # Some rollback steps failed - mark result as degraded but not blocked
                    logger.warning(
                        "Rollback PARTIAL for action %s: %s",
                        action,
                        rollback_result.reason,
                    )
                    # Update result_with_verification to reflect PARTIAL state
                    result_with_verification = ActionResult(
                        action=result.action,
                        status="partial_failure",
                        output=result.output,
                        error=f"Rollback PARTIAL: {rollback_result.reason}",
                        verification_passed=False,
                        error_type=result.error_type,
                        retryable=result.retryable,
                    )
            else:
                # Execution failed - abort rollback to clean up
                await self._rollback_manager.abort_rollback(rollback_plan)

        self._action_history.append(result_with_verification)
        return result_with_verification

    def _extract_target_paths(self, action: str) -> tuple[str, ...]:
        """Extract target file paths from action string with workspace validation."""
        import re

        paths: list[str] = []
        workspace_resolved = Path(self._workspace).resolve()

        def _is_safe_path(path: str) -> bool:
            try:
                candidate = Path(path)
                if not candidate.is_absolute():
                    candidate = (workspace_resolved / candidate).resolve()
                else:
                    candidate = candidate.resolve()
                return any(p == workspace_resolved for p in candidate.parents) or candidate == workspace_resolved
            except (OSError, ValueError):
                return False

        quoted_patterns = [
            r'"([^"]+)"',
            r"'([^']+)'",
        ]
        for pattern in quoted_patterns:
            for match in re.finditer(pattern, action):
                potential_path = match.group(1)
                if ("/" in potential_path or "\\" in potential_path or "." in potential_path) and _is_safe_path(
                    potential_path
                ):
                    paths.append(potential_path)

        path_pattern = r"([\w\-./\\]+\.\w+)"
        for match in re.finditer(path_pattern, action):
            potential_path = match.group(1)
            if potential_path not in paths and _is_safe_path(potential_path):
                paths.append(potential_path)

        return tuple(paths) if paths else ()

    def _resolve_target_paths(self, target_paths: tuple[str, ...]) -> tuple[str, ...]:
        """Resolve relative target paths against configured workspace."""
        if not target_paths:
            return ()

        workspace_resolved = Path(self._workspace).resolve()
        resolved: list[str] = []
        for raw in target_paths:
            token = str(raw or "").strip()
            if not token:
                continue
            candidate = Path(token)
            if not candidate.is_absolute():
                candidate = workspace_resolved / candidate
            with contextlib.suppress(OSError, ValueError):
                candidate = candidate.resolve()
            resolved.append(str(candidate))
        return tuple(resolved)

    _ALLOWED_TOOLS: frozenset[str] = frozenset(
        {
            "read_file",
            "edit_file",
            "create_file",
            "ripgrep",
            "list_directory",
            "get_file_info",
            "glob_search",
        }
    )

    _BLOCKED_PATTERNS: tuple[str, ...] = (
        r"rm\s+-rf",
        r"del\s+/[fqs]",
        r"format\s+[a-z]:",
        r"\.\./",
        r"\x00",
        r"\|\s*nohup",
        r"&&\s*nohup",
    )

    def _validate_action(self, action: str) -> tuple[bool, str]:
        if not action or not action.strip():
            return False, "Empty action"
        import re

        for pattern in self._BLOCKED_PATTERNS:
            if re.search(pattern, action, re.IGNORECASE):
                return False, f"Action matches blocked pattern: {pattern}"
        return True, ""

    def _execute_direct(self, action: str) -> ActionResult:
        """Direct execution for BYPASS path."""
        logger.info("AUDIT: executing action: %s", action)
        is_valid, reason = self._validate_action(action)
        if not is_valid:
            logger.warning("AUDIT: action blocked: %s reason=%s", action, reason)
            return ActionResult(
                action=action,
                status="failed",
                output=None,
                error=f"Action blocked: {reason}",
            )

        executor = self._get_tool_executor()

        if executor is None:
            return ActionResult(
                action=action,
                status="success",
                output=f"Executed: {action}",
                error=None,
            )

        tool_name, arguments = self._parse_action(action)

        if tool_name is None:
            return ActionResult(
                action=action,
                status="failed",
                output=f"Failed to parse action: {action}",
                error=None,
            )

        if tool_name not in self._ALLOWED_TOOLS:
            logger.warning("AUDIT: tool not in whitelist: %s", tool_name)
            return ActionResult(
                action=action,
                status="failed",
                output=None,
                error=f"Tool '{tool_name}' is not in the allowed tools whitelist",
            )

        if tool_name == "_BLOCKED_DELETE":
            return ActionResult(
                action=action,
                status="failed",
                output=None,
                error="Delete operations are not supported in cognitive acting phase",
            )

        try:
            result = executor.execute(tool_name, arguments)
            if result.get("ok"):
                return ActionResult(
                    action=action,
                    status="success",
                    output=str(result.get("result", {})),
                    error=None,
                )
            else:
                return ActionResult(
                    action=action,
                    status="failed",
                    output=None,
                    error=result.get("error", "Unknown error"),
                    error_type=result.get("error_type"),
                    retryable=result.get("retryable", True),
                )
        except (RuntimeError, ValueError) as e:
            return ActionResult(
                action=action,
                status="failed",
                output=None,
                error=str(e),
                error_type=type(e).__name__,
                retryable=False,
            )

    def _parse_action(self, action: str) -> tuple[str | None, dict[str, Any]]:
        """Parse natural language action into tool call."""
        action_lower = action.lower()

        # Map intent patterns to tools
        if action_lower.startswith("read"):
            return ("read_file", {"file": self._extract_path(action)})
        elif action_lower.startswith("create"):
            return ("create_file", {"path": self._extract_path(action), "content": ""})
        elif action_lower.startswith("modify") or action_lower.startswith("update"):
            return ("edit_file", {"path": self._extract_path(action)})
        elif action_lower.startswith("delete"):
            # delete_file not in _TOOL_SPECS — return blocked status with clear reason
            return ("_BLOCKED_DELETE", {})
        elif action_lower.startswith("search") or action_lower.startswith("find"):
            return ("ripgrep", {"pattern": self._extract_pattern(action)})

        return None, {}

    def _extract_path(self, action: str) -> str:
        """Extract file path from action."""
        # Simple extraction - look for common path patterns
        import re

        patterns = [
            r'["\']([^"\']+)["\']',  # Quoted strings
            r"(?:at|from|in)\s+(\S+)",  # "at X", "from X", "in X"
            r"([\w/\\.]+\.\w+)",  # File paths with extensions
        ]
        for pattern in patterns:
            match = re.search(pattern, action)
            if match:
                return match.group(1)
        return action

    def _extract_pattern(self, action: str) -> str:
        """Extract search pattern from action."""
        import re

        match = re.search(r'["\']([^"\']+)["\']', action)
        if match:
            return match.group(1)
        return action

    async def _execute_with_verification(
        self,
        action: str,
        light: bool,
    ) -> ActionResult:
        """Execute with verification."""
        # Execute
        result = self._execute_direct(action)

        # Light verification: just check success
        verification_passed = result.status == "success"

        # Create new instance with verification_passed set (ActionResult is frozen)
        result_with_verification = ActionResult(
            action=result.action,
            status=result.status,
            output=result.output,
            error=result.error,
            verification_passed=verification_passed,
            error_type=result.error_type,
            retryable=result.retryable,
        )

        self._action_history.append(result_with_verification)
        return result_with_verification
