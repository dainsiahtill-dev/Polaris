"""CLI runner for role agents.

This module contains CLI execution modes (interactive, oneshot, autonomous, server)
extracted from RoleRuntimeService.

Wave 3 extraction - E4: Service Layer Lead.

Architecture:
    CliRunner depends on IRoleRuntime (RoleRuntimeService) for execution.
    RoleRuntimeService retains core execution responsibilities.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleSessionCommandV1,
    ExecuteRoleTaskCommandV1,
    IRoleRuntime,
)
from polaris.cells.runtime.task_runtime.public import (
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementOutcomeV1,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService

logger = logging.getLogger(__name__)


class CliRunner:
    """CLI execution modes for role agents.

    This class is the terminal/API host adapter for role execution. It keeps
    input/output concerns at the edge while routing every turn through
    IRoleRuntime (RoleRuntimeService).

    Args:
        runtime: The role runtime service to delegate execution to.
    """

    def __init__(self, runtime: IRoleRuntime) -> None:
        self._runtime = runtime

    @staticmethod
    def _bootstrap_fact_stream_workspace(workspace: str, maintenance_reason: str) -> None:
        """Delegate formal role-CLI startup to the platform bootstrap service."""

        bootstrap_fact_stream_workspace(
            BootstrapFactStreamWorkspaceCommandV1(
                workspace=workspace,
                streams=fact_stream_bootstrap_streams(),
                maintenance_reason=maintenance_reason,
            )
        )

    @staticmethod
    def _claim_task_execution_attempt(
        *,
        role_id: str,
        workspace: str,
        task_id: str,
        objective: str,
        run_id: str,
    ) -> tuple[TaskRuntimeService, int, TaskRuntimeExecutionAttemptIdentityV1]:
        """Materialize and claim CLI task authority through TaskRuntime."""

        task_runtime = TaskRuntimeService(workspace)
        task_row = task_runtime.ensure_task_row(
            external_task_id=task_id,
            subject=f"CLI role task: {task_id}",
            description=objective,
            metadata={
                "source": "roles.runtime.public.cli_runner.execute_role",
                "role": role_id,
                "execution_identity_required": True,
            },
        )
        task_runtime_task_id = task_runtime.normalize_task_id(task_row.get("id"))
        if task_runtime_task_id is None:
            raise RuntimeError("cli_task_execution_task_id_invalid")
        claim = task_runtime.claim_execution(
            task_runtime_task_id,
            worker_id=f"cli:{role_id}",
            role_id=role_id,
            run_id=run_id,
            selection_source="roles.runtime.public.cli_runner.execute_role",
            external_task_id=task_id,
            context_summary=objective,
            metadata={"source": "roles.runtime.public.cli_runner.execute_role"},
        )
        session = claim.get("session") if isinstance(claim, dict) else None
        attempt_record = claim.get("execution_attempt") if isinstance(claim, dict) else None
        if (
            not bool(claim.get("success"))
            or not isinstance(session, Mapping)
            or not isinstance(attempt_record, Mapping)
        ):
            reason = str(claim.get("reason") or "unknown") if isinstance(claim, dict) else "invalid_claim_result"
            raise RuntimeError(f"cli_task_execution_claim_failed:{reason}")
        identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(attempt_record)
        if (
            identity.workspace != workspace
            or identity.task_id != task_runtime_task_id
            or identity.external_task_id != task_id
            or identity.role_id != role_id
            or identity.run_id != run_id
            or identity.session_id != str(session.get("session_id") or "").strip()
            or identity.attempt != session.get("attempt")
        ):
            raise RuntimeError("cli_task_execution_attempt_session_mismatch")
        return task_runtime, task_runtime_task_id, identity

    @staticmethod
    def _settle_task_execution(
        *,
        task_runtime: TaskRuntimeService,
        task_runtime_task_id: int,
        execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        result_ok: bool | None,
        summary: str,
    ) -> None:
        """Settle one CLI TaskRuntime claim without hiding settlement failures."""

        if result_ok is True:
            outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1 = "completed"
        elif result_ok is False:
            outcome = "failed"
        else:
            outcome = "suspended"
        if task_runtime_task_id != execution_attempt.task_id:
            raise RuntimeError("cli_task_execution_attempt_task_mismatch")
        settlement = task_runtime.settle_execution_attempt(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=execution_attempt.workspace,
                identity=execution_attempt,
                outcome=outcome,
                summary=summary,
                lock_timeout_seconds=5.0,
                metadata={"source": "roles.runtime.public.cli_runner.execute_role"},
            )
        )
        if not bool(settlement.get("success")):
            reason = str(settlement.get("reason") or "unknown")
            raise RuntimeError(f"cli_task_execution_settlement_failed:{reason}")

    async def run_interactive(
        self,
        role: str,
        workspace: str,
        welcome_message: str = "",
    ) -> None:
        """Interactive REPL loop for a role.

        Args:
            role: Role name (e.g. 'architect', 'director').
            workspace: Workspace directory path.
            welcome_message: Optional banner to print before the REPL starts.
        """
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        self._bootstrap_fact_stream_workspace(workspace, "roles_runtime_cli_interactive_startup")

        # Access RoleRuntimeService-specific method via casting
        runtime_svc = self._runtime
        if not isinstance(runtime_svc, RoleRuntimeService):
            raise TypeError("run_interactive requires RoleRuntimeService instance")

        session_id = str(uuid.uuid4())[:8]
        role_display = role.title()

        if welcome_message:
            print(welcome_message)

        print(f"[{role_display}] Interactive mode - /quit to exit, /help for commands\n")
        history: list[tuple[str, str]] = []
        session_context_config: dict[str, Any] = {}

        while True:
            try:
                user_input = input("\033[94mYou\033[0m > ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nGoodbye!")
                break

            if not user_input:
                continue
            if user_input.lower() in {"/quit", "/exit"}:
                logger.info("%s: Goodbye!", role_display)
                break
            if user_input.lower() == "/help":
                print("Commands: /quit  /help  /status")
                continue
            if user_input.lower() == "/status":
                print(f"Role: {role_display}, Session: {session_id}, Status: ready")
                continue

            projected_history, projected_context, session_context_config = await runtime_svc._project_host_history(
                session_id=session_id,
                role=role,
                workspace=workspace,
                history=tuple(history),
                context={"host_kind": "runtime_interactive", "role": role},
                session_context_config=session_context_config,
                history_limit=10,
                session_title=f"{role_display} interactive session",
            )
            command = ExecuteRoleSessionCommandV1(
                role=role,
                session_id=session_id,
                workspace=workspace,
                user_message=user_input,
                history=projected_history,
                context=projected_context,
                stream=False,
            )
            try:
                result = await self._runtime.execute_role_session(command)
                history.append(("user", user_input))
                assistant_text = str(result.output or result.error_message or "").strip()
                if assistant_text:
                    history.append(("assistant", assistant_text))
                print(f"\n{result.output or '(no output)'}")
                if result.tool_calls:
                    print(f"Tools called: {', '.join(result.tool_calls)}")
                if not result.ok:
                    print(f"[ERROR] {result.error_message}")
            except (RuntimeError, ValueError) as e:
                logger.exception("Interactive session error")
                print(f"[ERROR] {e}")

    async def run_oneshot(
        self,
        role: str,
        workspace: str,
        goal: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a single role goal and return structured result.

        Args:
            role: Role name.
            workspace: Workspace directory path.
            goal: Task goal description.
            context: Optional execution context.

        Returns:
            dict with 'result' key containing RoleExecutionResultV1 as dict.
        """
        self._bootstrap_fact_stream_workspace(workspace, "roles_runtime_cli_oneshot_startup")
        session_id = str(uuid.uuid4())[:8]
        command = ExecuteRoleSessionCommandV1(
            role=role,
            session_id=session_id,
            workspace=workspace,
            user_message=goal,
            context=dict(context) if context else {},
            stream=False,
        )
        result = await self._runtime.execute_role_session(command)
        return {
            "plan": {"goal": goal, "steps": []},
            "result": {
                "ok": result.ok,
                "status": result.status,
                "output": result.output,
                "thinking": result.thinking,
                "tool_calls": list(result.tool_calls),
                "artifacts": list(result.artifacts),
                "error_message": result.error_message,
            },
            "completed_at": datetime.now().isoformat(),
        }

    async def run_autonomous(
        self,
        role: str,
        workspace: str,
        goal: str,
        max_iterations: int = 10,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Plan-and-execute loop for a role, up to max_iterations.

        Args:
            role: Role name.
            workspace: Workspace directory path.
            goal: High-level goal.
            max_iterations: Max plan-execute cycles.
            context: Optional execution context.

        Returns:
            dict with 'initial_goal', 'iterations', 'history', 'final_result'.
        """
        self._bootstrap_fact_stream_workspace(workspace, "roles_runtime_cli_autonomous_startup")
        session_id = str(uuid.uuid4())[:8]
        history = []
        current_goal = goal

        for iteration in range(max_iterations):
            logger.info("--- Iteration %s/%s ---", iteration + 1, max_iterations)

            command = ExecuteRoleSessionCommandV1(
                role=role,
                session_id=f"{session_id}-iter{iteration}",
                workspace=workspace,
                user_message=current_goal,
                context=dict(context) if context else {},
                stream=False,
            )
            try:
                result = await self._runtime.execute_role_session(command)
            except (RuntimeError, ValueError):
                result = None
                logger.exception("Autonomous iteration error")

            history.append(
                {
                    "iteration": iteration,
                    "goal": current_goal,
                    "result": {
                        "ok": getattr(result, "ok", False),
                        "status": getattr(result, "status", "error"),
                        "output": getattr(result, "output", ""),
                    }
                    if result
                    else {"status": "error"},
                }
            )

            if result and result.ok and result.status == "ok":
                logger.info("Task completed successfully!")
                break

            # Advance goal for next iteration
            if result and result.output:
                current_goal = result.output[:500]

        return {
            "initial_goal": goal,
            "iterations": len(history),
            "history": history,
            "final_result": history[-1] if history else None,
        }

    async def run_server(
        self,
        role: str,
        workspace: str,
        host: str = "127.0.0.1",
        port: int = 50000,
    ) -> None:
        """Run a FastAPI server for programmatic role access.

        Args:
            role: Role name.
            workspace: Workspace directory path.
            host: Server bind host.
            port: Server bind port.
        """
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        self._bootstrap_fact_stream_workspace(workspace, "roles_runtime_cli_server_startup")

        # Access RoleRuntimeService-specific method via casting
        runtime_svc = self._runtime
        if not isinstance(runtime_svc, RoleRuntimeService):
            raise TypeError("run_server requires RoleRuntimeService instance")

        try:
            import uvicorn
            from fastapi import FastAPI, HTTPException
        except ImportError as e:
            raise RuntimeError(
                f"run_server requires uvicorn and fastapi: pip install uvicorn fastapi. Error: {e}"
            ) from e

        role_display = role.title()
        app = FastAPI(title=f"{role_display} Role Agent API")

        @app.post("/chat")
        async def chat_endpoint(request: dict[str, Any]) -> dict[str, Any]:
            message = request.get("message", "")
            if not message:
                raise HTTPException(status_code=400, detail="message is required")
            session_id = str(request.get("session_id") or uuid.uuid4())[:8]
            metadata = dict(request.get("metadata") or {})
            context = dict(request.get("context") or {})
            prompt_appendix = str(request.get("prompt_appendix") or "").strip()
            if prompt_appendix:
                metadata["prompt_appendix"] = prompt_appendix
            domain = request.get("domain") or context.get("domain") or metadata.get("domain")
            domain_token = str(domain).strip() if domain is not None else ""
            projected_history, projected_context, _ = await runtime_svc._project_host_history(
                session_id=session_id,
                role=role,
                workspace=workspace,
                history=request.get("history") or (),
                context=context,
                session_context_config=context,
                history_limit=10,
                session_title=f"{role_display} api chat session",
            )
            command = ExecuteRoleSessionCommandV1(
                role=role,
                session_id=session_id,
                workspace=workspace,
                user_message=message,
                domain=domain_token or None,
                history=projected_history,
                context=projected_context,
                metadata=metadata,
                stream=False,
            )
            result = await self._runtime.execute_role_session(command)
            return {
                "ok": result.ok,
                "session_id": session_id,
                "output": result.output,
                "thinking": result.thinking,
            }

        @app.post("/execute")
        async def execute_endpoint(request: dict[str, Any]) -> dict[str, Any]:
            goal = request.get("goal", request.get("message", ""))
            context = dict(request.get("context") or {})
            session_id = str(request.get("session_id") or uuid.uuid4())[:8]
            metadata = dict(request.get("metadata") or {})
            prompt_appendix = str(request.get("prompt_appendix") or "").strip()
            if prompt_appendix:
                metadata["prompt_appendix"] = prompt_appendix
            domain = request.get("domain") or context.get("domain") or metadata.get("domain")
            domain_token = str(domain).strip() if domain is not None else ""
            projected_history, projected_context, _ = await runtime_svc._project_host_history(
                session_id=session_id,
                role=role,
                workspace=workspace,
                history=request.get("history") or (),
                context=context,
                session_context_config=context,
                history_limit=10,
                session_title=f"{role_display} api execute session",
            )
            command = ExecuteRoleSessionCommandV1(
                role=role,
                session_id=session_id,
                workspace=workspace,
                user_message=goal,
                domain=domain_token or None,
                history=projected_history,
                context=projected_context,
                metadata=metadata,
                stream=False,
            )
            result = await self._runtime.execute_role_session(command)
            return {
                "ok": result.ok,
                "status": result.status,
                "session_id": session_id,
                "output": result.output,
                "tool_calls": list(result.tool_calls),
            }

        @app.get("/status")
        async def status_endpoint() -> dict[str, Any]:
            return {"role": role, "workspace": workspace, "status": "ready"}

        logger.info("Starting %s API server on %s:%s", role_display, host, port)
        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    async def execute_role(
        self,
        role_id: str,
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Execute a role task or session based on context parameters.

        Args:
            role_id: Role name.
            context: Execution context containing workspace, session_id, etc.

        Returns:
            dict containing execution result fields.
        """
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        # Access RoleRuntimeService-specific method via casting
        runtime_svc = self._runtime
        if not isinstance(runtime_svc, RoleRuntimeService):
            raise TypeError("execute_role requires RoleRuntimeService instance")

        payload = dict(context)
        workspace = str(payload.get("workspace") or ".").strip() or "."
        self._bootstrap_fact_stream_workspace(workspace, "roles_runtime_cli_execute_startup")
        session_id = str(payload.get("session_id") or "").strip()
        metadata = dict(payload.get("metadata") or {})
        context_map = dict(payload.get("context") or {})
        domain = payload.get("domain") or context_map.get("domain") or metadata.get("domain")
        domain_token = str(domain).strip() if domain is not None else ""
        prompt_appendix = str(payload.get("prompt_appendix") or "").strip()
        if prompt_appendix:
            metadata["prompt_appendix"] = prompt_appendix
        if session_id:
            projected_history, projected_context, _ = await runtime_svc._project_host_history(
                session_id=session_id,
                role=role_id,
                workspace=workspace,
                history=payload.get("history") or (),
                context=context_map,
                session_context_config=context_map,
                history_limit=10,
                session_title=f"{role_id} execute_role session",
            )
            result = await self._runtime.execute_role_session(
                ExecuteRoleSessionCommandV1(
                    role=role_id,
                    session_id=session_id,
                    workspace=workspace,
                    user_message=str(payload.get("message") or payload.get("input") or "").strip(),
                    run_id=str(payload.get("run_id") or "").strip() or None,
                    task_id=str(payload.get("task_id") or "").strip() or None,
                    domain=domain_token or None,
                    history=projected_history,
                    context=projected_context,
                    metadata=metadata,
                    stream=bool(payload.get("stream", True)),
                )
            )
        else:
            task_id = str(payload.get("task_id") or "").strip()
            if not task_id:
                raise ValueError("task_id is required when execute_role is used without session_id")
            objective = str(payload.get("message") or payload.get("input") or "").strip() or "execute role task"
            run_id = str(payload.get("run_id") or "").strip()
            task_runtime, task_runtime_task_id, execution_attempt = self._claim_task_execution_attempt(
                role_id=role_id,
                workspace=workspace,
                task_id=task_id,
                objective=objective,
                run_id=run_id,
            )
            try:
                result = await self._runtime.execute_role_task(
                    ExecuteRoleTaskCommandV1(
                        role=role_id,
                        task_id=task_id,
                        workspace=workspace,
                        objective=objective,
                        run_id=run_id or None,
                        domain=domain_token or None,
                        context=context_map,
                        metadata=metadata,
                        timeout_seconds=payload.get("timeout_seconds"),
                        stream=bool(payload.get("stream", False)),
                        execution_attempt=execution_attempt,
                    )
                )
            except BaseException:
                self._settle_task_execution(
                    task_runtime=task_runtime,
                    task_runtime_task_id=task_runtime_task_id,
                    execution_attempt=execution_attempt,
                    result_ok=None,
                    summary="CLI task execution raised before a result was returned",
                )
                raise
            self._settle_task_execution(
                task_runtime=task_runtime,
                task_runtime_task_id=task_runtime_task_id,
                execution_attempt=execution_attempt,
                result_ok=result.ok,
                summary=str(result.output or result.error_message or result.status or "").strip(),
            )
        return {
            "ok": result.ok,
            "status": result.status,
            "role": result.role,
            "workspace": result.workspace,
            "task_id": result.task_id,
            "session_id": result.session_id,
            "run_id": result.run_id,
            "output": result.output,
            "thinking": result.thinking,
            "tool_calls": list(result.tool_calls),
            "artifacts": list(result.artifacts),
            "usage": dict(result.usage),
            "error_code": result.error_code,
            "error_message": result.error_message,
        }


__all__ = [
    "CliRunner",
]
